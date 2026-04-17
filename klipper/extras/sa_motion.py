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
        """Move servo to engaged angle and cut PWM (latching servo)."""
        owner = self.owner
        sn = self._owner_srv_name()
        owner.gcode.run_script_from_command(
            "SET_SERVO SERVO=%s ANGLE=%.1f" % (sn, owner.servo_engaged_angle))
        owner.reactor.pause(owner.reactor.monotonic() + owner.servo_move_delay)
        owner.gcode.run_script_from_command("SET_SERVO SERVO=%s WIDTH=0" % sn)
        owner._servo_is_engaged = True
        logging.debug("SAMotion: servo engaged (%.1f°)", owner.servo_engaged_angle)

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
        """Home selector — method selected by owner.homing_mode.

        1 = physical endstop double-touch (SA_SELECTOR_STOP / PA15)
        2 = sensorless stallguard (SA_SELECTOR_DIAG / PB9)
        """
        if self.owner.homing_mode == 1:
            self._selector_home_endstop()
        else:
            self._selector_home_sensorless()

    def _selector_home_endstop(self):
        """Physical endstop double-touch homing (homing_mode=1).

        The switch triggers at path 0. SET_POSITION=0 directly after each
        trigger — no offset correction needed here.
        """
        owner  = self.owner
        sn     = self._owner_sel_name()
        hs     = owner.selector_homing_speed
        bo     = owner.selector_homing_backoff
        mt     = owner.selector_max_travel

        self.servo_disengage()
        self._cancel_timeout(sn)

        sel_obj = owner.printer.lookup_object('manual_stepper sa_selector')
        rail    = sel_obj.rail
        orig_endstops = list(rail.endstops)

        if owner._selector_phys_endstop is None:
            raise RuntimeError(
                "SA HOME: physical endstop (PA15) not registered.\n"
                "endstop_pin in hardware.cfg must ALWAYS be ^!autoloader:SA_SELECTOR_DIAG.\n"
                "Do NOT change it to SA_SELECTOR_STOP — that causes a 'pin used multiple times' "
                "conflict and disables this feature. Only change homing_mode to switch modes.")

        rail.endstops = [(owner._selector_phys_endstop, 'physical_stop')]
        try:
            owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % sn)
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)

            # ── Fast approach ────────────────────────────────────────────────
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s MOVE=-%.1f SPEED=%.1f STOP_ON_ENDSTOP=1"
                % (sn, mt + 20.0, hs))
            owner.gcode.run_script_from_command("M400")
            # Switch triggers at path 0 — zero position here directly
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)

            # ── Back off ─────────────────────────────────────────────────────
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s MOVE=%.1f SPEED=%.1f" % (sn, bo, hs))
            owner.gcode.run_script_from_command("M400")

            # ── Slow re-approach ─────────────────────────────────────────────
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s MOVE=-%.1f SPEED=%.1f STOP_ON_ENDSTOP=1"
                % (sn, bo * 4.0, hs / 4.0))
            owner.gcode.run_script_from_command("M400")
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)
        finally:
            rail.endstops = orig_endstops

        self._arm_timeout(sn)
        owner.current_path = -1
        self._selector_position = 0.0
        logging.info("SAMotion: selector homed (endstop mode)")
        self.save_position()

    def _selector_home_sensorless(self):
        """Sensorless stallguard homing (homing_mode=2).

        Pre-backoff ensures the motor is at full speed when it hits the hard
        stop, making stall detection reliable. Second touch is optional —
        set selector_stall_speed_2=0 to skip (recommended for sensorless
        since the motor starts from rest over a short backoff and may not
        reach stall threshold).
        """
        owner         = self.owner
        sn            = self._owner_sel_name()
        mt            = owner.selector_max_travel
        bo            = owner.selector_homing_backoff
        threshold     = owner.selector_stall_threshold
        stall_current = owner.selector_stall_current
        stall_speed   = owner.selector_stall_speed
        stall_speed_2 = owner.selector_stall_speed_2
        prebackoff    = owner.selector_stall_prebackoff

        self.servo_disengage()
        self._cancel_timeout(sn)

        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % sn)
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)

        # Pre-backoff: move away from home so motor is at full speed at stall point
        owner.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s MOVE=%.1f SPEED=%.1f" % (sn, prebackoff, stall_speed))
        owner.gcode.run_script_from_command("M400")

        # Arm stallguard
        owner.gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=%s FIELD=sgt VALUE=%d" % (sn, threshold))
        owner.gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=%s FIELD=tcoolthrs VALUE=5000" % sn)
        owner.gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=%s FIELD=diag1_stall VALUE=1" % sn)
        owner.gcode.run_script_from_command(
            "SET_TMC_CURRENT STEPPER=%s CURRENT=%.3f" % (sn, stall_current))
        owner.reactor.pause(owner.reactor.monotonic() + 0.3)

        try:
            # Fast approach — DIAG endstop in rail stops move on stall
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s MOVE=-%.1f SPEED=%.1f STOP_ON_ENDSTOP=1"
                % (sn, mt + prebackoff + 20.0, stall_speed))
            owner.gcode.run_script_from_command("M400")
            # Stall point is selector_endstop_offset mm before path 0.
            # Label position as -offset so MOVE=0 steps carriage forward to path 0.
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s SET_POSITION=%.2f"
                % (sn, -owner.selector_endstop_offset))
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s MOVE=0.0 SPEED=%.1f" % (sn, stall_speed))
            owner.gcode.run_script_from_command("M400")

            # Clear stallguard
            owner.gcode.run_script_from_command(
                "SET_TMC_FIELD STEPPER=%s FIELD=diag1_stall VALUE=0" % sn)
            owner.gcode.run_script_from_command(
                "SET_TMC_FIELD STEPPER=%s FIELD=tcoolthrs VALUE=0" % sn)
            owner.gcode.run_script_from_command(
                "SET_TMC_CURRENT STEPPER=%s CURRENT=0.600" % sn)
            owner.reactor.pause(owner.reactor.monotonic() + 0.1)

            if stall_speed_2 > 0.0:
                # Optional slow re-approach for higher repeatability
                owner.gcode.run_script_from_command(
                    "MANUAL_STEPPER STEPPER=%s MOVE=%.1f SPEED=%.1f"
                    % (sn, bo, stall_speed))
                owner.gcode.run_script_from_command("M400")

                owner.gcode.run_script_from_command(
                    "SET_TMC_FIELD STEPPER=%s FIELD=sgt VALUE=%d" % (sn, threshold))
                owner.gcode.run_script_from_command(
                    "SET_TMC_FIELD STEPPER=%s FIELD=tcoolthrs VALUE=5000" % sn)
                owner.gcode.run_script_from_command(
                    "SET_TMC_FIELD STEPPER=%s FIELD=diag1_stall VALUE=1" % sn)
                owner.gcode.run_script_from_command(
                    "SET_TMC_CURRENT STEPPER=%s CURRENT=%.3f" % (sn, stall_current))
                owner.reactor.pause(owner.reactor.monotonic() + 0.3)

                owner.gcode.run_script_from_command(
                    "MANUAL_STEPPER STEPPER=%s MOVE=-%.1f SPEED=%.1f STOP_ON_ENDSTOP=1"
                    % (sn, bo * 4.0, stall_speed_2))
                owner.gcode.run_script_from_command("M400")
                owner.gcode.run_script_from_command(
                    "MANUAL_STEPPER STEPPER=%s SET_POSITION=%.2f"
                    % (sn, -owner.selector_endstop_offset))
                owner.gcode.run_script_from_command(
                    "MANUAL_STEPPER STEPPER=%s MOVE=0.0 SPEED=%.1f" % (sn, stall_speed_2))
                owner.gcode.run_script_from_command("M400")

        finally:
            owner.gcode.run_script_from_command(
                "SET_TMC_FIELD STEPPER=%s FIELD=diag1_stall VALUE=0" % sn)
            owner.gcode.run_script_from_command(
                "SET_TMC_FIELD STEPPER=%s FIELD=tcoolthrs VALUE=0" % sn)
            owner.gcode.run_script_from_command(
                "SET_TMC_CURRENT STEPPER=%s CURRENT=0.600" % sn)

        self._arm_timeout(sn)
        owner.current_path = -1
        self._selector_position = 0.0
        logging.info("SAMotion: selector homed (sensorless, speed_2=%.0f)", stall_speed_2)
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
            sv.allVariables['sa_selector_pos']   = self._selector_position
            sv.allVariables['sa_current_path']    = owner.current_path
            sv.save_variables()
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
            logging.info("SAMotion: restored selector position=%.3fmm path=%d from save_variables",
                         pos, path)
        else:
            logging.info("SAMotion: no saved position found — selector position unknown")
