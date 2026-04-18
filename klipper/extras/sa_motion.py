# sa_motion.py — Stealth Autoloader motion primitives
#
# Handles all low-level hardware movement:
#   - Servo engage/disengage/off
#   - Selector homing, moves, and far-end detection
#   - Drive motor moves
#   - Stepper idle-timeout management
#   - Optional position persistence via save_variables

import sys, os as _os
_extras_dir = _os.path.dirname(_os.path.abspath(__file__))
if _extras_dir not in sys.path:
    sys.path.insert(0, _extras_dir)

import logging

# ══════════════════════════════════════════════════════════════════════════════
# SAMotion
# ══════════════════════════════════════════════════════════════════════════════

class SAMotion:
    """All motion primitives for the Stealth Autoloader.

    ``owner`` is the StealthAutoloader instance.  All hardware names and
    motion parameters are read from owner attributes so this class has no
    separate config parsing.
    """

    def __init__(self, owner):
        self.owner = owner
        # stepper_name → reactor timer handle
        self._timeout_handles = {}
        # last known selector position in mm from home
        self._selector_position = 0.0

    # ── internal shorthand ────────────────────────────────────────────────────

    def _owner_sel_name(self):
        """Selector stepper short name (last word of selector_stepper_name)."""
        return self.owner._sel_name()

    def _owner_drv_name(self):
        """Drive stepper short name (last word of drive_stepper_name)."""
        return self.owner._drv_name()

    def _owner_srv_name(self):
        """Servo short name (last word of servo_name)."""
        return self.owner._servo_short_name()

    # ══════════════════════════════════════════════════════════════════════════
    # Servo
    # ══════════════════════════════════════════════════════════════════════════

    def servo_engage(self):
        """Move servo to engaged angle, jitter drive gear to help mesh, then cut PWM.

        Jitter pattern from Happy Hare: ±0.8mm × 3 at 25mm/s after servo moves.
        Vibration seats the drive gear teeth against filament before latching.
        """
        owner = self.owner
        sn    = self._owner_srv_name()
        dn    = self._owner_drv_name()

        # Servo moves first — let it reach position before jitter
        owner.gcode.run_script_from_command(
            "SET_SERVO SERVO=%s ANGLE=%.1f" % (sn, owner.servo_engaged_angle))
        owner.reactor.pause(owner.reactor.monotonic() + owner.servo_move_delay)

        # Jitter: ±0.8mm × 3 at 25mm/s to seat gear teeth
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % dn)
        for _ in range(3):
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s SET_POSITION=0 MOVE=0.8 SPEED=25" % dn)
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s SET_POSITION=0 MOVE=-0.8 SPEED=25" % dn)
        owner.gcode.run_script_from_command("M400")

        # Cut PWM — servo latches in place
        owner.gcode.run_script_from_command("SET_SERVO SERVO=%s WIDTH=0" % sn)
        owner._servo_is_engaged = True
        logging.debug("SAMotion: servo engaged (%.1f°) with gear jitter", owner.servo_engaged_angle)

    def servo_disengage(self):
        """Move servo to disengaged angle and cut PWM (latching servo)."""
        owner = self.owner
        sn = self._owner_srv_name()
        owner.gcode.run_script_from_command(
            "SET_SERVO SERVO=%s ANGLE=%.1f" % (sn, owner.servo_disengaged_angle))
        owner.reactor.pause(owner.reactor.monotonic() + owner.servo_move_delay)
        owner.gcode.run_script_from_command("SET_SERVO SERVO=%s WIDTH=0" % sn)
        owner._servo_is_engaged = False
        logging.debug("SAMotion: servo disengaged (%.1f°)", owner.servo_disengaged_angle)

    def servo_off(self):
        """Immediately cut servo PWM (emergency cutoff, no movement)."""
        owner = self.owner
        sn = self._owner_srv_name()
        owner.gcode.run_script_from_command("SET_SERVO SERVO=%s WIDTH=0" % sn)
        logging.info("SAMotion: servo PWM cut (emergency off)")

    # ══════════════════════════════════════════════════════════════════════════
    # Stepper idle-timeout management
    # ══════════════════════════════════════════════════════════════════════════

    def _arm_timeout(self, stepper_name):
        """Schedule auto-disable for *stepper_name* after owner.stepper_timeout seconds.

        Any previously-armed timer for this stepper is cancelled first so the
        timeout is always measured from the last motion, not the first.
        """
        self._cancel_timeout(stepper_name)
        owner = self.owner
        reactor = owner.reactor
        delay = owner.stepper_timeout

        # Capture stepper_name in closure
        _name = stepper_name

        def _timer_cb(eventtime):
            try:
                owner.gcode.run_script_from_command(
                    "MANUAL_STEPPER STEPPER=%s ENABLE=0" % _name)
                logging.info("SAMotion: auto-disabled stepper '%s' after %.0fs idle",
                             _name, delay)
            except Exception as e:
                logging.warning("SAMotion: failed to disable stepper '%s': %s", _name, e)
            # Remove handle from dict
            self._timeout_handles.pop(_name, None)
            return reactor.NEVER

        handle = reactor.register_timer(
            _timer_cb, reactor.monotonic() + delay)
        self._timeout_handles[stepper_name] = handle

    def _cancel_timeout(self, stepper_name):
        """Cancel an existing idle-timeout timer for *stepper_name* if one is armed."""
        handle = self._timeout_handles.pop(stepper_name, None)
        if handle is not None:
            self.owner.reactor.unregister_timer(handle)

    # ══════════════════════════════════════════════════════════════════════════
    # Selector motor
    # ══════════════════════════════════════════════════════════════════════════

    def selector_home(self):
        """Home selector to physical endstop — double-touch for accuracy.

        Switch (SA_SELECTOR_STOP / PA15) triggers at path 0.
        Fast approach → SET_POSITION=0 → back off → slow re-approach → SET_POSITION=0.
        """
        owner = self.owner
        sn    = self._owner_sel_name()
        hs    = owner.selector_homing_speed
        bo    = owner.selector_homing_backoff
        mt    = owner.selector_max_travel

        self.servo_disengage()
        self._cancel_timeout(sn)

        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % sn)
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)

        # Fast approach
        owner.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s MOVE=-%.1f SPEED=%.1f STOP_ON_ENDSTOP=1"
            % (sn, mt + 20.0, hs))
        owner.gcode.run_script_from_command("M400")
        owner.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)

        # Back off
        owner.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s MOVE=%.1f SPEED=%.1f" % (sn, bo, hs))
        owner.gcode.run_script_from_command("M400")

        # Slow re-approach
        owner.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s MOVE=-%.1f SPEED=%.1f STOP_ON_ENDSTOP=1"
            % (sn, bo * 4.0, hs / 4.0))
        owner.gcode.run_script_from_command("M400")
        owner.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)

        self._arm_timeout(sn)
        owner.current_path = -1
        owner._selector_homed = True
        self._selector_position = 0.0
        logging.info("SAMotion: selector homed")
        self.save_position()

    def selector_move_to(self, position_mm):
        """Move selector carriage to *position_mm* (absolute, mm from home).

        Cancels any pending idle timer, enables stepper, moves, then re-arms
        the idle timer.
        """
        owner = self.owner
        sn = self._owner_sel_name()

        self._cancel_timeout(sn)
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % sn)
        owner.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s MOVE=%.3f SPEED=%.1f"
            % (sn, position_mm, owner.selector_speed))
        owner.gcode.run_script_from_command("M400")
        self._arm_timeout(sn)
        self._selector_position = position_mm
        logging.debug("SAMotion: selector moved to %.3fmm", position_mm)

    # ══════════════════════════════════════════════════════════════════════════
    # Drive motor
    # ══════════════════════════════════════════════════════════════════════════

    def drive_move(self, distance_mm, speed=None):
        """Move the drive stepper by *distance_mm* at *speed* (mm/s).

        Positive = feed (toward extruder).  Negative = retract.
        Cancels pending idle timer, enables, moves, re-arms timer.
        """
        owner = self.owner
        if speed is None:
            speed = owner.feed_speed
        dn = self._owner_drv_name()

        self._cancel_timeout(dn)
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % dn)
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % dn)
        owner.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s MOVE=%.3f SPEED=%.1f" % (dn, distance_mm, speed))
        owner.gcode.run_script_from_command("M400")
        self._arm_timeout(dn)

    def drive_disable(self):
        """Immediately disable drive stepper (no timeout delay)."""
        owner = self.owner
        dn = self._owner_drv_name()
        self._cancel_timeout(dn)
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=0" % dn)
        logging.info("SAMotion: drive stepper disabled")

    # ══════════════════════════════════════════════════════════════════════════
    # Position persistence via save_variables
    # ══════════════════════════════════════════════════════════════════════════

    def save_position(self):
        """Persist selector position and current_path to save_variables if available."""
        owner = self.owner
        sv = owner.printer.lookup_object('save_variables', None)
        if sv is None:
            return
        try:
            owner.gcode.run_script_from_command(
                "SAVE_VARIABLE VARIABLE=sa_selector_pos VALUE=%.3f"
                % self._selector_position)
            owner.gcode.run_script_from_command(
                "SAVE_VARIABLE VARIABLE=sa_current_path VALUE=%d"
                % owner.current_path)
            logging.debug("SAMotion: position saved (sel=%.3f path=%d)",
                          self._selector_position, owner.current_path)
        except Exception as e:
            logging.warning("SAMotion: could not save position: %s", e)

    def load_position(self):
        """Load persisted selector position and current_path.

        Returns (selector_pos_mm, current_path).
        Falls back to (0.0, -1) if save_variables unavailable or key absent.
        """
        owner = self.owner
        sv = owner.printer.lookup_object('save_variables', None)
        if sv is None:
            return 0.0, -1
        try:
            pos  = float(sv.allVariables.get('sa_selector_pos', 0.0))
            path = int(sv.allVariables.get('sa_current_path', -1))
            return pos, path
        except Exception as e:
            logging.warning("SAMotion: could not load saved position: %s", e)
            return 0.0, -1

    # ══════════════════════════════════════════════════════════════════════════
    # Startup
    # ══════════════════════════════════════════════════════════════════════════

    def on_ready(self):
        """Called from StealthAutoloader._on_ready via reactor callback.

        - Unconditionally disengages the servo (safe boot state).
        - Attempts to restore last-known selector position from save_variables.
        - Logs the result.
        """
        owner = self.owner
        try:
            self.servo_disengage()
            logging.info("SAMotion: servo disengaged at startup")
        except Exception as e:
            logging.warning("SAMotion: servo init failed: %s", e)

        pos, path = self.load_position()
        if pos != 0.0 or path != -1:
            self._selector_position = pos
            owner.current_path = path
            owner._selector_homed = True
            logging.info("SAMotion: restored selector position=%.3fmm path=%d from save_variables",
                         pos, path)
        else:
            logging.info("SAMotion: no saved position found — selector position unknown")
