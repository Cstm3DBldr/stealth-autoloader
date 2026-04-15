# filament_feed.py - Stealth Autoloader Python backend
#
# One instance per [filament_feed toolX] section in hardware.cfg.
# SA_FILAMENT_LOAD / SA_FILAMENT_UNLOAD registered once, dispatched via TOOL=.
#
# Hardware per tool:
#   - Entry sensor   : detects filament inserted at roll end
#   - Feed motor     : manual_stepper, drives filament toward toolhead
#   - Binky encoder  : single-pulse rotary encoder just after feed motor
#                      tracks actual filament movement in mm
#   - Extruder       : toolhead extruder (existing klipper-toolchanger config)
#
# The encoder replaces all mid-path sensors (extruder sensor, toolhead sensor,
# buffer tension/compression). Distance to any point in the path is tracked
# by counting encoder pulses × mm_per_pulse.

import logging

class FilamentFeed:
    _tools = {}
    _commands_registered = False

    def __init__(self, config):
        self.printer = config.get_printer()
        self.name    = config.get_name().split()[-1]   # "tool0" … "tool5"

        # Feed stepper — strip "manual_stepper " prefix for MANUAL_STEPPER gcode
        feed_cfg = config.get('feed_stepper')
        self.feed_stepper_name = feed_cfg.split()[-1]

        # Sensor / encoder section names
        self.extruder_name    = config.get('extruder')
        self.entry_sensor_name = config.get('entry_sensor')
        self.encoder_name      = config.get('encoder')   # e.g. "sa_encoder tool0"

        # Tunable parameters (all in hardware.cfg [filament_feed toolX])
        self.tube_length         = config.getfloat('tube_length',              800.0)
        self.sensor_delay        = config.getfloat('sensor_polling_frequency',   0.2)
        self.nozzle_distance     = config.getfloat('nozzle_distance',            50.0)
        self.purge_length        = config.getfloat('purge_length',               30.0)
        self.load_temperature    = config.getfloat('load_temperature',          200.0)
        self.feed_step_size      = config.getfloat('feed_step_size',             10.0)
        self.extruder_step_size  = config.getfloat('extruder_step_size',          5.0)
        # Max distance to feed before expecting encoder motion (jam / no-filament detect)
        self.engage_max_distance = config.getfloat('engage_max_distance',        60.0)
        # Slippage tolerance: alert if encoder vs stepper differ by more than this %
        self.slip_tolerance      = config.getfloat('slip_tolerance',             15.0)

        FilamentFeed._tools[self.name] = self

        self.gcode = self.printer.lookup_object('gcode')
        if not FilamentFeed._commands_registered:
            self.gcode.register_command('SA_FILAMENT_LOAD',   self._dispatch_load,
                desc="Load filament. TOOL=tool0..tool5")
            self.gcode.register_command('SA_FILAMENT_UNLOAD', self._dispatch_unload,
                desc="Unload filament. TOOL=tool0..tool5")
            FilamentFeed._commands_registered = True

        logging.info("Stealth Autoloader '%s' initialized", self.name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _dispatch_load(self, gcmd):
        tool = gcmd.get('TOOL', None)
        if tool not in FilamentFeed._tools:
            gcmd.respond_info("SA_FILAMENT_LOAD: TOOL= required. Available: %s"
                              % list(FilamentFeed._tools.keys()))
            return
        FilamentFeed._tools[tool].do_load(gcmd)

    def _dispatch_unload(self, gcmd):
        tool = gcmd.get('TOOL', None)
        if tool not in FilamentFeed._tools:
            gcmd.respond_info("SA_FILAMENT_UNLOAD: TOOL= required. Available: %s"
                              % list(FilamentFeed._tools.keys()))
            return
        FilamentFeed._tools[tool].do_unload(gcmd)

    def _entry_sensor(self):
        try:
            obj = self.printer.lookup_object(self.entry_sensor_name)
            return obj.get_status(
                self.printer.get_reactor().monotonic())['filament_detected']
        except Exception:
            return False

    def _encoder(self):
        """Return the sa_encoder object for this tool."""
        return self.printer.lookup_object(self.encoder_name)

    def _feed(self, distance, speed=50):
        """Move feed motor by distance mm at speed mm/s, then wait for move."""
        self.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s MOVE=%.1f SPEED=%.1f"
            % (self.feed_stepper_name, distance, speed))
        self.gcode.run_script_from_command("M400")

    # ------------------------------------------------------------------
    # Load sequence
    # ------------------------------------------------------------------

    def do_load(self, gcmd):
        reactor  = self.printer.get_reactor()
        encoder  = self._encoder()

        gcmd.respond_info("Loading Filament — %s" % self.name)

        # Enable feed motor, set encoder direction to forward
        self.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s ENABLE=1" % self.feed_stepper_name)
        encoder.set_direction(forward=True)
        encoder.reset_distance()

        # --- Phase 1: engage filament with feed motor ---
        # Feed up to engage_max_distance looking for encoder motion
        fed = 0.0
        while encoder.get_distance() < self.mm_per_pulse_threshold() and fed < self.engage_max_distance:
            self._feed(self.feed_step_size)
            fed += self.feed_step_size
            reactor.pause(reactor.monotonic() + self.sensor_delay)

        if encoder.get_distance() < self.mm_per_pulse_threshold():
            gcmd.respond_info(
                "ERROR — %s: No encoder motion after %.0fmm. "
                "Check filament is inserted and encoder is working." % (self.name, fed))
            return

        gcmd.respond_info("Filament engaged — feeding to extruder...")

        # --- Phase 2: feed until encoder reads tube_length ---
        stepper_distance = fed
        while encoder.get_distance() < self.tube_length:
            self._feed(self.feed_step_size)
            stepper_distance += self.feed_step_size
            reactor.pause(reactor.monotonic() + self.sensor_delay)
            # Slippage check
            self._check_slip(gcmd, encoder.get_distance(), stepper_distance)

        gcmd.respond_info("Filament at extruder — %.1fmm fed (encoder: %.1fmm)"
                          % (stepper_distance, encoder.get_distance()))

        # --- Phase 3: heat and extrude to nozzle ---
        self.gcode.run_script_from_command(
            "TEMPERATURE_WAIT SENSOR=%s MINIMUM=%.0f"
            % (self.extruder_name, self.load_temperature))

        gcmd.respond_info("Feeding through extruder to nozzle tip...")
        self.gcode.run_script_from_command("M83")
        self.gcode.run_script_from_command(
            "G1 E%.1f F300\nM400" % self.nozzle_distance)

        # --- Phase 4: purge ---
        gcmd.respond_info("Purging nozzle...")
        self.gcode.run_script_from_command(
            "G1 E%.1f F300\nM400" % self.purge_length)

        self.gcode.run_script_from_command("_CLEAN_NOZZLE")
        self.gcode.run_script_from_command("PARK_ON_COOLING_PAD")
        gcmd.respond_info("LOAD COMPLETE — %s" % self.name)

    # ------------------------------------------------------------------
    # Unload sequence
    # ------------------------------------------------------------------

    def do_unload(self, gcmd):
        reactor = self.printer.get_reactor()
        encoder = self._encoder()

        gcmd.respond_info("Unloading Filament — %s" % self.name)

        # Retract from nozzle tip through extruder
        self.gcode.run_script_from_command(
            "M83\nG1 E-%.1f F300\nM400"
            % (self.nozzle_distance + self.purge_length))

        # Reverse feed motor, track encoder back toward entry
        self.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s ENABLE=1" % self.feed_stepper_name)
        encoder.set_direction(forward=False)
        encoder.reset_distance()

        while self._entry_sensor():
            self._feed(-self.feed_step_size)
            reactor.pause(reactor.monotonic() + self.sensor_delay)

        gcmd.respond_info("UNLOAD COMPLETE — %s (encoder: %.1fmm retracted)"
                          % (self.name, abs(encoder.get_distance())))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def mm_per_pulse_threshold(self):
        """Minimum encoder distance that counts as 'filament is moving'."""
        try:
            enc = self._encoder()
            return enc.mm_per_pulse * 3   # at least 3 pulses before we consider it engaged
        except Exception:
            return 1.5

    def _check_slip(self, gcmd, encoder_dist, stepper_dist):
        """Warn if encoder and stepper differ by more than slip_tolerance %."""
        if stepper_dist < 50.0:
            return   # too short to meaningfully compare
        diff_pct = abs(encoder_dist - stepper_dist) / stepper_dist * 100.0
        if diff_pct > self.slip_tolerance:
            gcmd.respond_info(
                "WARNING — %s slip detected: stepper=%.1fmm encoder=%.1fmm (%.1f%%)"
                % (self.name, stepper_dist, encoder_dist, diff_pct))

    @staticmethod
    def load_config_prefix(config):
        return FilamentFeed(config)

def load_config_prefix(config):
    return FilamentFeed.load_config_prefix(config)
