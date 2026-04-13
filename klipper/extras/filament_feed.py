# filament_feed.py - Stealth Autoloader (follows YOUR full flow chart)
# Reads all configurable values from hardware.cfg

import logging

class FilamentFeed:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1] or "tool0"
        
        # Load per-toolhead sections
        self.feed_stepper_name = config.get('feed_stepper')
        self.entry_sensor_name = config.get('entry_sensor')
        self.extruder_sensor_name = config.get('extruder_sensor')
        self.toolhead_sensor_name = config.get('toolhead_sensor')
        self.buffer_tension_name = config.get('buffer_tension_sensor')
        self.buffer_compression_name = config.get('buffer_compression_sensor')
        
        # Load configurable variables from hardware.cfg
        self.tube_length = config.getfloat('tube_length', 800.0)
        self.sensor_delay = config.getfloat('sensor_polling_frequency', 0.2)
        self.buffer_slide = config.getfloat('buffer_slide_distance', 15.0)
        self.extruder_gear = config.getfloat('extruder_gear_distance', 30.0)
        self.nozzle_distance = config.getfloat('nozzle_distance', 50.0)
        self.purge_length = config.getfloat('purge_length', 30.0)
        
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('FILAMENT_LOAD', self.cmd_FILAMENT_LOAD, desc="Full load using your flow chart")
        self.gcode.register_command('FILAMENT_UNLOAD', self.cmd_FILAMENT_UNLOAD, desc="Unload to roll")
        
        logging.info(f"Stealth Autoloader '{self.name}' initialized with tube_length={self.tube_length}mm")

    def get_sensor(self, sensor_name):
        try:
            s = self.printer.lookup_object(sensor_name)
            return s.get_status(self.printer.get_reactor().monotonic())['filament_detected']
        except:
            return False

    def cmd_FILAMENT_LOAD(self, gcmd):
        gcmd.respond_info(f"Loading Filament - {self.name}")
        self.gcode.run_script_from_command(f"SET_STEPPER_ENABLE STEPPER={self.feed_stepper_name} ENABLE=1")
        
        # LOOP while extruder_sensor == false
        while not self.get_sensor(self.extruder_sensor_name):
            self.gcode.run_script_from_command(f"MANUAL_STEPPER {self.feed_stepper_name} MOVE=10 SPEED=50")
            self.gcode.run_script_from_command("M400")
            self.printer.get_reactor().pause(self.printer.get_reactor().monotonic() + self.sensor_delay)
        
        gcmd.respond_info("Feeding to Toolhead")
        # Push until buffer compression
        while not self.get_sensor(self.buffer_compression_name):
            self.gcode.run_script_from_command(f"MANUAL_STEPPER {self.feed_stepper_name} MOVE=5 SPEED=30")
            self.gcode.run_script_from_command("M400")
            self.printer.get_reactor().pause(self.printer.get_reactor().monotonic() + self.sensor_delay)
        
        # Toolhead extruder loop
        self.gcode.run_script_from_command("SET_STEPPER_ENABLE STEPPER=extruder ENABLE=1")
        while not self.get_sensor(self.toolhead_sensor_name):
            self.gcode.run_script_from_command("G1 E10 F300")
            self.gcode.run_script_from_command("M400")
            self.printer.get_reactor().pause(self.printer.get_reactor().monotonic() + self.sensor_delay)
        
        # Final extrude to nozzle + purge
        self.gcode.run_script_from_command(f"M83\nG1 E{self.nozzle_distance} F300\nM400")
        self.gcode.run_script_from_command(f"G1 E{self.purge_length} F300\nM400")
        
        gcmd.respond_info("Purging Nozzle")
        self.gcode.run_script_from_command("_CLEAN_NOZZLE")
        self.gcode.run_script_from_command("PARK_ON_COOLING_PAD")
        gcmd.respond_info("LOAD COMPLETE")

    def cmd_FILAMENT_UNLOAD(self, gcmd):
        gcmd.respond_info(f"Starting UNLOAD for {self.name}")
        self.gcode.run_script_from_command("M83\nG1 E-50 F300\nM400")
        while self.get_sensor(self.entry_sensor_name):
            self.gcode.run_script_from_command(f"MANUAL_STEPPER {self.feed_stepper_name} MOVE=-10 SPEED=50")
            self.gcode.run_script_from_command("M400")
        gcmd.respond_info("UNLOAD COMPLETE")

    @staticmethod
    def load_config(config):
        return FilamentFeed(config)

def load_config(config):
    return FilamentFeed.load_config(config)
