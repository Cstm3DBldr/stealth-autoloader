# filament_feed.py - Stealth Autoloader Python backend
#
# One instance of this class is created per [filament_feed toolX] section in hardware.cfg.
# SA_FILAMENT_LOAD / SA_FILAMENT_UNLOAD are registered ONCE (by the first instance to load)
# and dispatch to the correct tool via TOOL= parameter, so all 6 instances coexist without
# command name conflicts.

import logging

class FilamentFeed:
    # Class-level registry — shared across all 6 tool instances
    _tools = {}
    _commands_registered = False

    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1]   # "tool0" … "tool5"

        # Feed stepper — strip the section prefix for MANUAL_STEPPER gcode
        # hardware.cfg stores "manual_stepper feed_motor_t0"; gcode wants "feed_motor_t0"
        feed_stepper_cfg = config.get('feed_stepper')
        self.feed_stepper_name = feed_stepper_cfg.split()[-1]

        # Sensor section names — full name for printer.lookup_object()
        self.extruder_name           = config.get('extruder')
        self.entry_sensor_name       = config.get('entry_sensor')
        self.extruder_sensor_name    = config.get('extruder_sensor')
        self.toolhead_sensor_name    = config.get('toolhead_sensor')
        self.buffer_tension_name     = config.get('buffer_tension_sensor')
        self.buffer_compression_name = config.get('buffer_compression_sensor')

        # Tunable parameters — all defined in [filament_feed toolX] in hardware.cfg
        self.tube_length        = config.getfloat('tube_length',              800.0)
        self.sensor_delay       = config.getfloat('sensor_polling_frequency',   0.2)
        self.buffer_slide       = config.getfloat('buffer_slide_distance',      15.0)
        self.extruder_gear      = config.getfloat('extruder_gear_distance',     30.0)
        self.nozzle_distance    = config.getfloat('nozzle_distance',            50.0)
        self.purge_length       = config.getfloat('purge_length',               30.0)
        self.load_temperature   = config.getfloat('load_temperature',          200.0)
        self.feed_step_size     = config.getfloat('feed_step_size',             10.0)
        self.extruder_step_size = config.getfloat('extruder_step_size',          5.0)

        # Add this instance to the class registry
        FilamentFeed._tools[self.name] = self

        self.gcode = self.printer.lookup_object('gcode')

        # Register SA_FILAMENT_LOAD / SA_FILAMENT_UNLOAD exactly once
        if not FilamentFeed._commands_registered:
            self.gcode.register_command(
                'SA_FILAMENT_LOAD', self._dispatch_load,
                desc="Load filament. TOOL=tool0..tool5")
            self.gcode.register_command(
                'SA_FILAMENT_UNLOAD', self._dispatch_unload,
                desc="Unload filament. TOOL=tool0..tool5")
            FilamentFeed._commands_registered = True

        logging.info("Stealth Autoloader '%s' initialized", self.name)

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def _dispatch_load(self, gcmd):
        tool = gcmd.get('TOOL', None)
        if tool not in FilamentFeed._tools:
            gcmd.respond_info(
                "SA_FILAMENT_LOAD: TOOL= required. Available: %s"
                % list(FilamentFeed._tools.keys()))
            return
        FilamentFeed._tools[tool].do_load(gcmd)

    def _dispatch_unload(self, gcmd):
        tool = gcmd.get('TOOL', None)
        if tool not in FilamentFeed._tools:
            gcmd.respond_info(
                "SA_FILAMENT_UNLOAD: TOOL= required. Available: %s"
                % list(FilamentFeed._tools.keys()))
            return
        FilamentFeed._tools[tool].do_unload(gcmd)

    # ------------------------------------------------------------------
    # Sensor helper
    # ------------------------------------------------------------------

    def _sensor(self, name):
        try:
            obj = self.printer.lookup_object(name)
            return obj.get_status(
                self.printer.get_reactor().monotonic())['filament_detected']
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Load sequence (follows flow chart in CLAUDE.md exactly)
    # ------------------------------------------------------------------

    def do_load(self, gcmd):
        reactor = self.printer.get_reactor()
        gcmd.respond_info("Loading Filament - %s" % self.name)

        # Enable feed motor
        self.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s ENABLE=1" % self.feed_stepper_name)

        # Feed until filament reaches extruder sensor
        while not self._sensor(self.extruder_sensor_name):
            self.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s MOVE=%.1f SPEED=50"
                % (self.feed_stepper_name, self.feed_step_size))
            self.gcode.run_script_from_command("M400")
            reactor.pause(reactor.monotonic() + self.sensor_delay)

        # Push until buffer reaches compression state
        while not self._sensor(self.buffer_compression_name):
            self.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s MOVE=5 SPEED=30"
                % self.feed_stepper_name)
            self.gcode.run_script_from_command("M400")
            reactor.pause(reactor.monotonic() + self.sensor_delay)

        # Wait for hotend to reach load temperature before extruding
        self.gcode.run_script_from_command(
            "TEMPERATURE_WAIT SENSOR=extruder MINIMUM=%.0f" % self.load_temperature)

        # Extrude until toolhead sensor triggers
        gcmd.respond_info("Feeding to Toolhead")
        self.gcode.run_script_from_command("M83")
        while not self._sensor(self.toolhead_sensor_name):
            self.gcode.run_script_from_command(
                "G1 E%.1f F300" % self.extruder_step_size)
            self.gcode.run_script_from_command("M400")
            reactor.pause(reactor.monotonic() + self.sensor_delay)

        # Advance to nozzle tip
        self.gcode.run_script_from_command(
            "G1 E%.1f F300\nM400" % self.nozzle_distance)

        # Purge
        gcmd.respond_info("Purging Nozzle")
        self.gcode.run_script_from_command(
            "G1 E%.1f F300\nM400" % self.purge_length)

        self.gcode.run_script_from_command("_CLEAN_NOZZLE")
        self.gcode.run_script_from_command("PARK_ON_COOLING_PAD")
        gcmd.respond_info("LOAD COMPLETE")

    # ------------------------------------------------------------------
    # Unload sequence
    # ------------------------------------------------------------------

    def do_unload(self, gcmd):
        reactor = self.printer.get_reactor()
        gcmd.respond_info("Unloading Filament - %s" % self.name)

        # Retract from nozzle tip back past extruder gears
        self.gcode.run_script_from_command(
            "M83\nG1 E-%.1f F300\nM400"
            % (self.nozzle_distance + self.purge_length))

        # Retract extruder until extruder sensor clears
        while self._sensor(self.extruder_sensor_name):
            self.gcode.run_script_from_command(
                "G1 E-%.1f F300" % self.extruder_step_size)
            self.gcode.run_script_from_command("M400")
            reactor.pause(reactor.monotonic() + self.sensor_delay)

        # Retract feed motor until entry sensor clears
        self.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s ENABLE=1" % self.feed_stepper_name)
        while self._sensor(self.entry_sensor_name):
            self.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s MOVE=-%.1f SPEED=50"
                % (self.feed_stepper_name, self.feed_step_size))
            self.gcode.run_script_from_command("M400")
            reactor.pause(reactor.monotonic() + self.sensor_delay)

        gcmd.respond_info("UNLOAD COMPLETE")

    @staticmethod
    def load_config(config):
        return FilamentFeed(config)

def load_config(config):
    return FilamentFeed.load_config(config)
