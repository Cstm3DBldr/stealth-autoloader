# filament_feed.py - Stealth Autoloader for Voron Stealth Changer
# 1 path now → easy copy-paste for all 6 later
# Uses your existing extruder + sensors + new manual_stepper

import logging
from klippy import configfile

class FilamentFeed:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1] or "tool0"
        
        self.extruder_name = config.get('extruder', 'extruder')
        self.feed_stepper_name = config.get('feed_stepper', 'manual_stepper feed_motor')
        self.entry_sensor_name = config.get('entry_sensor', 'filament_switch_sensor entry_sensor')
        
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('FILAMENT_LOAD', self.cmd_FILAMENT_LOAD, desc="Load to hotend using sensors")
        self.gcode.register_command('FILAMENT_UNLOAD', self.cmd_FILAMENT_UNLOAD, desc="Unload to roll")
        
        logging.info(f"Stealth Autoloader '{self.name}' initialized")

    def get_sensor_state(self, sensor_name):
        try:
            sensor = self.printer.lookup_object(sensor_name)
            return sensor.get_status(self.printer.get_reactor().monotonic())['filament_detected']
        except:
            return False

    def cmd_FILAMENT_LOAD(self, gcmd):
        gcmd.respond_info(f"Starting LOAD for {self.name}")
        self.gcode.run_script_from_command(f"SET_STEPPER_ENABLE STEPPER={self.feed_stepper_name} ENABLE=1")
        
        while not self.get_sensor_state(self.entry_sensor_name):
            self.gcode.run_script_from_command(f"MANUAL_STEPPER {self.feed_stepper_name} MOVE=10 SPEED=50")
            self.gcode.run_script_from_command("M400")
        
        gcmd.respond_info("Filament at hotend - parking")
        self.gcode.run_script_from_command("M83\nG1 E50 F300\nM400")
        gcmd.respond_info("LOAD COMPLETE")

    def cmd_FILAMENT_UNLOAD(self, gcmd):
        gcmd.respond_info(f"Starting UNLOAD for {self.name}")
        self.gcode.run_script_from_command(f"SET_STEPPER_ENABLE STEPPER={self.feed_stepper_name} ENABLE=1")
        self.gcode.run_script_from_command("M83\nG1 E-50 F300\nM400")
        
        while self.get_sensor_state(self.entry_sensor_name):
            self.gcode.run_script_from_command(f"MANUAL_STEPPER {self.feed_stepper_name} MOVE=-10 SPEED=50")
            self.gcode.run_script_from_command("M400")
        gcmd.respond_info("UNLOAD COMPLETE")

    @staticmethod
    def load_config(config):
        return FilamentFeed(config)

def load_config(config):
    return FilamentFeed.load_config(config)
