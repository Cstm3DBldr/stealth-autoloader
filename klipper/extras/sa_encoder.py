# sa_encoder.py - Stealth Autoloader Rotary Encoder (Binky-style single-pulse)
#
# One instance per [sa_encoder toolX] section.
# Each encoder tracks actual filament movement independently using hardware
# interrupts via Klipper's buttons module. All 6 can accumulate counts
# simultaneously without data loss — events are queued in the reactor and
# processed in order.
#
# Usage in hardware.cfg:
#   [sa_encoder tool0]
#   sensor_pin: ^autoloader_a:SA_ENCODER_T0
#   mm_per_pulse: 0.5      # calibrate this per encoder
#
# Python API (called from filament_feed.py):
#   encoder.set_direction(forward=True)
#   encoder.reset_distance()
#   encoder.get_distance()  → mm moved since last reset

import logging

class SAEncoder:
    def __init__(self, config):
        self.printer  = config.get_printer()
        self.name     = config.get_name().split()[-1]   # "tool0" … "tool5"

        # mm of filament per encoder pulse — calibrate with SA_CALIBRATE_ENCODER
        # Binky default: 23 mm wheel circumference / ~48 pulses per rev ≈ 0.48 mm/pulse
        self.mm_per_pulse = config.getfloat('mm_per_pulse', 0.5)

        self._distance  = 0.0
        self._direction = 1     # +1 = forward (loading), -1 = reverse (unloading)

        # Register sensor pin for interrupt-driven pulse counting
        sensor_pin = config.get('sensor_pin')
        buttons = self.printer.load_object(config, 'buttons')
        buttons.register_buttons([sensor_pin], self._pulse_callback)

        logging.info("SA Encoder '%s' ready — %.4f mm/pulse", self.name, self.mm_per_pulse)

    # ------------------------------------------------------------------
    # Interrupt callback — called by Klipper reactor on each pin edge
    # ------------------------------------------------------------------

    def _pulse_callback(self, eventtime, state):
        if state:   # count rising edge only
            self._distance += self.mm_per_pulse * self._direction

    # ------------------------------------------------------------------
    # API used by filament_feed.py
    # ------------------------------------------------------------------

    def set_direction(self, forward=True):
        """Call before running the feed motor so distance accumulates correctly."""
        self._direction = 1 if forward else -1

    def reset_distance(self):
        """Zero the counter. Returns the previous value."""
        old = self._distance
        self._distance = 0.0
        return old

    def get_distance(self):
        """Total mm moved since last reset (positive = toward extruder)."""
        return self._distance

    # ------------------------------------------------------------------
    # Klipper status (readable in macros as printer['sa_encoder tool0'].distance)
    # ------------------------------------------------------------------

    def get_status(self, eventtime):
        return {
            'distance':     round(self._distance, 2),
            'mm_per_pulse': self.mm_per_pulse,
            'direction':    self._direction,
        }

    @staticmethod
    def load_config_prefix(config):
        return SAEncoder(config)

def load_config_prefix(config):
    return SAEncoder(config)
