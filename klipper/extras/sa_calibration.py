# sa_calibration.py — Stealth Autoloader calibration routines
#
# Interactive and automated calibration for:
#   - Drive motor rotation_distance (SA_CALIBRATE_DRIVE)
#   - Per-path encoder mm_per_pulse   (SA_CALIBRATE_ENCODER TOOL=N)
#   - Selector path positions         (SA_CALIBRATE_SELECTOR — automated)
#   - Bowden tube length per path     (SA_CALIBRATE_BOWDEN TOOL=N)
#
# All interactive routines use SA_RESPOND VALUE=<answer> for console I/O
# (polling owner._response_ready / owner._pending_response).

import sys, os as _os
_extras_dir = _os.path.dirname(_os.path.abspath(__file__))
if _extras_dir not in sys.path:
    sys.path.insert(0, _extras_dir)

import logging

# ══════════════════════════════════════════════════════════════════════════════
# SACalibration
# ══════════════════════════════════════════════════════════════════════════════

class SACalibration:
    """All calibration routines for the Stealth Autoloader.

    ``owner`` is the StealthAutoloader instance.
    """

    def __init__(self, owner):
        self.owner = owner

    # ══════════════════════════════════════════════════════════════════════════
    # Console interaction helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _wait_for_value(self, gcmd, prompt, example="", timeout=300.0):
        """Send *prompt* to the console and block until SA_RESPOND VALUE=xxx arrives.

        Returns the raw string value the user entered.
        Raises gcmd.error on timeout or if the user sends VALUE=abort.
        """
        owner = self.owner
        owner._pending_response = None
        owner._response_ready   = False

        msg = "SA CAL: %s" % prompt
        if example:
            msg += "\n  Example: %s" % example
        msg += "\n  -> Send:  SA_RESPOND VALUE=<answer>  (SA_RESPOND VALUE=abort to cancel)"
        gcmd.respond_info(msg)

        deadline = owner.reactor.monotonic() + timeout
        while not owner._response_ready:
            if owner.reactor.monotonic() > deadline:
                raise gcmd.error("SA CAL: Timeout waiting for SA_RESPOND")
            owner.reactor.pause(owner.reactor.monotonic() + 0.25)

        val = (owner._pending_response or "").strip()
        if val.lower() == 'abort':
            raise gcmd.error("SA CAL: Calibration aborted by user")
        return val

    def _prompt_yes_no(self, gcmd, question, timeout=120.0):
        """Ask a yes/no question via the console.  Returns True for affirmative."""
        val = self._wait_for_value(gcmd, question, "yes or no", timeout)
        return val.lower() in ('yes', 'y', '1', 'true', 'ok')

    def _save_config_value(self, section, key, value):
        """Queue a config value to be written by the next SAVE_CONFIG."""
        configfile = self.owner.printer.lookup_object('configfile')
        configfile.set(section, key, str(value))

    def _trigger_save_config(self, gcmd):
        """Inform the user and execute SAVE_CONFIG (restarts Klipper)."""
        gcmd.respond_info(
            "SA CAL: Calibration complete. "
            "Running SAVE_CONFIG — printer will restart.")
        self.owner.gcode.run_script_from_command("SAVE_CONFIG")

    # ══════════════════════════════════════════════════════════════════════════
    # Drive motor calibration
    # ══════════════════════════════════════════════════════════════════════════

    def calibrate_drive(self, gcmd):
        """SA_CALIBRATE_DRIVE — interactive rotation_distance calibration.

        Because there is one drive motor for all 6 paths, this needs to be
        done only once.  The routine:
          1. Asks which path has filament loaded.
          2. Moves selector to that path and engages the drive gear.
          3. Commands 100mm and asks the user to measure actual movement.
          4. Recalculates rotation_distance and repeats up to 3 times.
          5. Queues the final value via configfile.set and optionally saves.
        """
        owner  = self.owner
        motion = owner.motion

        gcmd.respond_info(
            "SA DRIVE CALIBRATION\n"
            "====================\n"
            "This calibrates the drive motor rotation_distance.\n"
            "Only needed once — same motor drives all paths.\n"
            "\n"
            "Requirements:\n"
            "  - Filament loaded past the drive gear on at least one path.\n"
            "  - A ruler or calipers to measure filament exit movement.")

        # ── Ask which path to use ─────────────────────────────────────────────
        val = self._wait_for_value(
            gcmd, "Which path number has filament loaded past the drive gear?",
            "0  through  %d" % (owner.num_paths - 1))
        try:
            path = int(val)
        except ValueError:
            raise gcmd.error("SA CAL: Invalid path number '%s'" % val)
        if not (0 <= path < owner.num_paths):
            raise gcmd.error("SA CAL: Path %d out of range (0-%d)" % (path, owner.num_paths - 1))

        # ── Move selector to path and engage ──────────────────────────────────
        gcmd.respond_info("SA CAL: Selecting path %d and engaging drive gear..." % path)
        motion.servo_disengage()
        motion.selector_move_to(owner._selector_positions[path])
        owner.current_path = path
        motion.servo_engage()

        # ── Read current rotation_distance from the stepper ───────────────────
        drive_obj = owner.printer.lookup_object(owner.drive_stepper_name)
        steppers  = drive_obj.get_steppers()
        if steppers:
            current_rd = steppers[0].get_rotation_distance()[0]
        else:
            current_rd = 22.0
            logging.warning("SACalibration: could not read rotation_distance — defaulting to 22.0")

        target    = 100.0
        best_rd   = current_rd

        for attempt in range(3):
            gcmd.respond_info(
                "SA CAL: Attempt %d/3 — will command %.0fmm.\n"
                "  Mark the filament at the encoder exit (or put a piece of tape)\n"
                "  so you can measure how far it actually moves."
                % (attempt + 1, target))

            # Wait for user ready
            self._wait_for_value(
                gcmd,
                "Filament marked? Ready to run?",
                "yes")

            # Feed 100mm
            enc = owner._encoder(path)
            enc.set_direction(forward=True)
            enc.reset_distance()
            motion.drive_move(target, speed=owner.feed_speed * 0.5)

            gcmd.respond_info(
                "SA CAL: %.0fmm commanded. "
                "Measure the distance from your mark to the new filament end." % target)
            measured_str = self._wait_for_value(
                gcmd,
                "How many mm did the filament actually travel?",
                "103.5")
            try:
                measured = float(measured_str)
            except ValueError:
                raise gcmd.error("SA CAL: Invalid measurement '%s'" % measured_str)
            if measured <= 0.0:
                raise gcmd.error("SA CAL: Measured distance must be > 0")

            error = abs(measured - target)
            pct   = error / target * 100.0
            new_rd = best_rd * (measured / target)

            gcmd.respond_info(
                "SA CAL: Commanded %.1fmm  |  Measured %.2fmm  |  "
                "Error %.2fmm (%.1f%%)\n"
                "  Current  rotation_distance: %.4f\n"
                "  Suggested rotation_distance: %.4f"
                % (target, measured, error, pct, best_rd, new_rd))

            best_rd = new_rd

            if error <= 1.0:
                gcmd.respond_info("SA CAL: Error within 1mm tolerance — drive calibrated!")
                break
            if attempt < 2:
                gcmd.respond_info(
                    "SA CAL: Error > 1mm. Running another pass. "
                    "Note: the stepper still uses the old value at runtime; "
                    "the measured correction is carried forward mathematically.")

        motion.servo_disengage()

        # Queue for SAVE_CONFIG
        self._save_config_value('manual_stepper sa_drive', 'rotation_distance',
                                '%.4f' % best_rd)
        gcmd.respond_info(
            "SA CAL: Final rotation_distance=%.4f queued for SAVE_CONFIG." % best_rd)

        if self._prompt_yes_no(gcmd, "Save drive calibration and restart printer now?"):
            self._trigger_save_config(gcmd)
        else:
            gcmd.respond_info("SA CAL: Value queued. Run SAVE_CONFIG when ready.")

    # ══════════════════════════════════════════════════════════════════════════
    # Per-path encoder calibration
    # ══════════════════════════════════════════════════════════════════════════

    def calibrate_encoder(self, gcmd):
        """SA_CALIBRATE_ENCODER TOOL=N — interactive mm_per_pulse calibration.

        Runs 5 × feed/retract cycles of 400mm and averages the results.
        Requires filament loaded past the drive gear and encoder for path N.
        """
        owner  = self.owner
        motion = owner.motion
        path   = gcmd.get_int('TOOL', minval=0, maxval=owner.num_paths - 1)
        enc    = owner._encoder(path)

        gcmd.respond_info(
            "SA ENCODER CALIBRATION — Path %d\n"
            "==================================\n"
            "Runs 5 feed/retract cycles of 400mm and averages pulse counts.\n"
            "\n"
            "Requirements:\n"
            "  - Filament loaded past the drive gear AND through the encoder.\n"
            "  - Enough free filament to allow 400mm × 5 = 2000mm total travel." % path)

        gcmd.respond_info("SA CAL: Selecting path %d and engaging drive gear..." % path)
        motion.servo_disengage()
        motion.selector_move_to(owner._selector_positions[path])
        owner.current_path = path
        motion.servo_engage()

        # ── Baseline check: confirm encoder responds ──────────────────────────
        self._wait_for_value(
            gcmd,
            "Position filament flush with encoder exit as a zero reference. Ready?",
            "yes")

        enc.set_direction(forward=True)
        enc.reset_distance()
        motion.drive_move(200.0, speed=owner.feed_speed * 0.5)

        if enc.get_distance() < 1.0:
            motion.servo_disengage()
            raise gcmd.error(
                "SA CAL: Encoder %d returned < 1mm. "
                "Check encoder wiring and filament grip." % path)

        gcmd.respond_info(
            "SA CAL: Encoder responding — %.2f raw units over 200mm stepper travel. Good."
            % enc.get_distance())
        self._prompt_yes_no(
            gcmd,
            "Has the filament moved approximately 200mm from the zero mark?")

        # ── 5 × feed/retract cycles ───────────────────────────────────────────
        gcmd.respond_info(
            "SA CAL: Running 5 × 400mm feed/retract cycles. Do not touch the filament.")

        feed_counts    = []
        retract_counts = []

        for i in range(5):
            # Feed 400mm
            enc.set_direction(forward=True)
            enc.reset_distance()
            motion.drive_move(400.0, speed=owner.feed_speed * 0.5)
            feed_dist = enc.get_distance()
            feed_counts.append(feed_dist)

            owner.reactor.pause(owner.reactor.monotonic() + 0.3)

            # Retract 400mm
            enc.set_direction(forward=False)
            enc.reset_distance()
            motion.drive_move(-400.0, speed=owner.feed_speed * 0.5)
            ret_dist = enc.get_distance()
            retract_counts.append(ret_dist)

            gcmd.respond_info(
                "SA CAL: Cycle %d/5 — feed=%.2f  ret=%.2f  (raw encoder units)"
                % (i + 1, feed_dist, ret_dist))
            owner.reactor.pause(owner.reactor.monotonic() + 0.2)

        # ── Calculate new mm_per_pulse ────────────────────────────────────────
        # enc.get_distance() returns (pulses × mm_per_pulse).
        # avg_count = average distance reported over the 10 passes (400mm each).
        # avg_pulses = avg_count / current_mpp  →  approximate pulse count per pass.
        # new_mpp = 400.0 / avg_pulses.
        all_counts = feed_counts + retract_counts
        avg_count  = sum(all_counts) / len(all_counts)
        current_mpp = enc.mm_per_pulse

        if current_mpp <= 0.0:
            raise gcmd.error("SA CAL: Encoder mm_per_pulse is zero or negative — check config.")

        avg_pulses = avg_count / current_mpp
        if avg_pulses <= 0.0:
            raise gcmd.error("SA CAL: Calculated pulse count is zero — encoder may not be working.")

        new_mpp = 400.0 / avg_pulses

        spread = max(all_counts) - min(all_counts)
        gcmd.respond_info(
            "SA CAL: Path %d encoder calibration results:\n"
            "  10-pass average (raw units): %.3f\n"
            "  Spread (max-min):            %.3f\n"
            "  Current  mm_per_pulse:       %.5f\n"
            "  Suggested mm_per_pulse:      %.5f"
            % (path, avg_count, spread, current_mpp, new_mpp))

        if spread / avg_count > 0.05:
            gcmd.respond_info(
                "SA CAL: WARNING — spread/avg = %.1f%%. "
                "High variation may indicate filament slip or loose encoder. "
                "Consider re-seating filament and re-running." % (spread / avg_count * 100))

        # ── Exit position verification ────────────────────────────────────────
        # After 5×(+400, -400) starting from +200, we should be back at +200mm.
        measured_str = self._wait_for_value(
            gcmd,
            "Measure filament from zero reference mark. Should be ~200mm. Actual?",
            "200.5  or  ok  if correct")

        if measured_str.lower() not in ('ok', 'yes', 'good'):
            try:
                actual = float(measured_str)
                if abs(actual - 200.0) > 5.0:
                    gcmd.respond_info(
                        "SA CAL: NOTE — exit position error %.1fmm. "
                        "Consider running SA_CALIBRATE_DRIVE first."
                        % abs(actual - 200.0))
            except ValueError:
                pass  # User typed something non-numeric — ignore

        motion.servo_disengage()

        # Queue for SAVE_CONFIG
        section = 'sa_encoder %d' % path
        self._save_config_value(section, 'mm_per_pulse', '%.5f' % new_mpp)
        gcmd.respond_info(
            "SA CAL: Encoder %d mm_per_pulse=%.5f queued for SAVE_CONFIG." % (path, new_mpp))

        if self._prompt_yes_no(gcmd, "Save encoder %d calibration and restart printer now?" % path):
            self._trigger_save_config(gcmd)
        else:
            gcmd.respond_info("SA CAL: Queued. Run SAVE_CONFIG when ready.")

    # ══════════════════════════════════════════════════════════════════════════
    # Sensorless selector calibration  (SA_CALIBRATE_SELECTOR)
    # ══════════════════════════════════════════════════════════════════════════

    def calibrate_selector_auto(self, gcmd):
        """SA_CALIBRATE_SELECTOR — one-time automated far-end detection.

        Uses TMC5160 stop_enable (GCONF bit 15) to latch the stall signal so it
        survives past M400, then measures total rail length by homing BACK to the
        physical endstop.  Accurate regardless of whether the outward move stopped
        on a clean stall or briefly ground at the hard stop.

        Flow
        ----
        1.  Home to physical endstop (double-touch).
        2.  Look up stepper object for MCU position measurement.
        3.  Set SGT, enable diag1_stall + stop_enable, lower current.
        4.  Single continuous outward move to selector_max_travel + 30mm.
            - stop_enable halts and latches the driver when stallguard fires.
            - If motor reaches far_target without stalling it grinds briefly
              at reduced current — acceptable for one-time calibration.
        5.  Read latched DIAG state (informational).
        6.  Clear stop_enable + diag1_stall; toggle enable pin to release latch.
        7.  Zero position at far wall; record MCU step counter.
        8.  Home BACK to physical endstop — this is the measurement.
        9.  total_travel = MCU step delta × step_dist.
        10. Restore normal current.
        11. Calculate evenly-spaced path positions; confirm; save.

        Tuning
        ------
        Stalls before far end → raise selector_stall_threshold or selector_stall_current
        Never stalls cleanly  → lower selector_stall_threshold or selector_stall_current
        (Positions are still calculated correctly via homing-back even if no stall.)
        """
        owner  = self.owner
        motion = owner.motion
        sn     = owner._sel_name()

        gcmd.respond_info(
            "SA SELECTOR CALIBRATION\n"
            "========================\n"
            "ONE-TIME setup — only repeat after mechanical changes.\n"
            "\n"
            "The selector sweeps to the far mechanical stop, then homes back\n"
            "to measure total rail length.  Path positions are calculated\n"
            "automatically from the measured travel.\n"
            "\n"
            "Requirements:\n"
            "  - SA_HOME must work correctly first.\n"
            "  - No filament loaded — servo must be free.\n"
            "  - [gcode_button selector_stall] in hardware.cfg\n"
            "    (pin: ^!autoloader:SA_SELECTOR_DIAG).\n"
            "\n"
            "Tuning if the carriage stalls mid-travel:\n"
            "  Raise selector_stall_threshold or selector_stall_current.\n")

        # ── Find and validate the stall button ────────────────────────────────
        stall_btn = owner.printer.lookup_object('gcode_button selector_stall', None)
        if stall_btn is None:
            raise gcmd.error(
                "SA CAL: [gcode_button selector_stall] not found in config.\n"
                "Add to hardware.cfg:\n"
                "  [gcode_button selector_stall]\n"
                "  pin: ^!autoloader:SA_SELECTOR_DIAG\n"
                "  press_gcode:\n"
                "  release_gcode:")

        if not self._prompt_yes_no(gcmd, "Ready to proceed?"):
            gcmd.respond_info("SA CAL: Calibration aborted.")
            return

        # ── Step 1: home to physical endstop ──────────────────────────────────
        gcmd.respond_info("SA CAL: Homing to physical endstop (double-touch)...")
        motion.selector_home()
        gcmd.respond_info("SA CAL: Homed at 0.0mm.")

        # ── Step 2: get stepper object for MCU position measurement ───────────
        sel_obj   = owner.printer.lookup_object('manual_stepper sa_selector')
        stepper   = sel_obj.get_steppers()[0]
        step_dist = stepper.get_step_dist()

        # ── Step 3: configure stallguard with latching stop ───────────────────
        threshold     = owner.selector_stall_threshold
        stall_current = owner.selector_stall_current
        stall_speed   = owner.selector_stall_speed

        gcmd.respond_info(
            "SA CAL: Configuring stallguard — SGT=%d, current=%.2fA, speed=%.0fmm/s"
            % (threshold, stall_current, stall_speed))

        owner.gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=%s FIELD=sgt VALUE=%d" % (sn, threshold))
        owner.gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=%s FIELD=diag1_stall VALUE=1" % sn)
        # stop_enable (GCONF bit 15): latch motor halt + DIAG pin HIGH on stall
        # so the signal survives past M400 and can be read after the move.
        owner.gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=%s FIELD=stop_enable VALUE=1" % sn)
        owner.gcode.run_script_from_command(
            "SET_TMC_CURRENT STEPPER=%s CURRENT=%.3f" % (sn, stall_current))
        owner.reactor.pause(owner.reactor.monotonic() + 0.3)

        # Verify DIAG starts RELEASED (no false stall at rest)
        status = stall_btn.get_status(owner.reactor.monotonic())
        if status.get('state') == 'PRESSED':
            self._restore_selector_current(gcmd, sn)
            raise gcmd.error(
                "SA CAL: selector_stall is PRESSED before sweep.\n"
                "Check DIAG1 wiring or raise selector_stall_threshold.")

        # ── Step 4: single continuous outward move ────────────────────────────
        far_target = owner.selector_max_travel + 30.0
        gcmd.respond_info(
            "SA CAL: Sweeping outward to %.0fmm at %.0fmm/s..."
            % (far_target, stall_speed))

        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % sn)
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)
        owner.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s MOVE=%.2f SPEED=%.1f SYNC=1"
            % (sn, far_target, stall_speed))
        owner.gcode.run_script_from_command("M400")
        owner.reactor.pause(owner.reactor.monotonic() + 0.3)

        # ── Step 5: read latched DIAG (informational only) ────────────────────
        status = stall_btn.get_status(owner.reactor.monotonic())
        stall_detected = (status.get('state') == 'PRESSED')
        if stall_detected:
            gcmd.respond_info(
                "SA CAL: Stall detected — motor halted cleanly at far end (DIAG latched).")
        else:
            gcmd.respond_info(
                "SA CAL: No stall latch — motor completed full sweep distance.\n"
                "         Continuing: total travel will be measured by homing back.")

        # ── Step 6: clear stop_enable + release driver latch ─────────────────
        owner.gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=%s FIELD=stop_enable VALUE=0" % sn)
        owner.gcode.run_script_from_command(
            "SET_TMC_FIELD STEPPER=%s FIELD=diag1_stall VALUE=0" % sn)
        # Toggle enable pin to release the driver from its latched-halt state
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=0" % sn)
        owner.reactor.pause(owner.reactor.monotonic() + 0.1)
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % sn)
        owner.reactor.pause(owner.reactor.monotonic() + 0.1)

        # ── Step 7: zero at far wall, record MCU step counter ─────────────────
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)
        mcu_far = stepper.get_mcu_position()

        # ── Step 8: home back to physical endstop — this is the measurement ──
        gcmd.respond_info("SA CAL: Homing back to measure total travel...")
        home_target = -(owner.selector_max_travel + 50.0)
        owner.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s MOVE=%.1f SPEED=%.1f STOP_ON_ENDSTOP=1"
            % (sn, home_target, owner.selector_homing_speed))
        owner.gcode.run_script_from_command("M400")

        # ── Step 9: calculate total travel from MCU step delta ────────────────
        mcu_home     = stepper.get_mcu_position()
        total_travel = abs(mcu_far - mcu_home) * step_dist
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)

        gcmd.respond_info(
            "SA CAL: MCU steps — far=%d  home=%d  delta=%d  step_dist=%.5fmm\n"
            "SA CAL: Total travel measured: %.2fmm"
            % (mcu_far, mcu_home, abs(mcu_far - mcu_home), step_dist, total_travel))

        # ── Step 10: restore normal current ───────────────────────────────────
        self._restore_selector_current(gcmd, sn)
        owner.motion._selector_position = 0.0
        owner.current_path = -1

        # ── Step 11: calculate path positions ────────────────────────────────
        n = owner.num_paths
        if n == 1:
            positions = [0.0]
            spacing   = 0.0
        else:
            if total_travel < (n - 1) * 5.0:
                raise gcmd.error(
                    "SA CAL: Measured travel (%.1fmm) too short for %d paths.\n"
                    "Check mechanical assembly or selector_max_travel setting."
                    % (total_travel, n))
            spacing   = total_travel / float(n - 1)
            positions = [round(i * spacing, 2) for i in range(n)]

        pos_lines = "\n".join(
            "  Path %d: %.2fmm" % (i, p) for i, p in enumerate(positions))
        gcmd.respond_info(
            "SA CAL: Total travel %.2fmm → %d paths, spacing %.2fmm:\n%s"
            % (total_travel, n, spacing, pos_lines))

        if not self._prompt_yes_no(gcmd, "Accept these positions and save?"):
            gcmd.respond_info("SA CAL: Positions NOT saved.  Adjust stall settings and retry.")
            motion.selector_home()
            return

        for i, pos in enumerate(positions):
            owner._selector_positions[i] = pos
            self._save_config_value(
                'stealth_autoloader', 'selector_position_%d' % i, '%.2f' % pos)

        gcmd.respond_info("SA CAL: All %d positions queued." % n)
        motion.selector_home()

        if self._prompt_yes_no(gcmd, "Save and restart printer now?"):
            self._trigger_save_config(gcmd)
        else:
            gcmd.respond_info("SA CAL: Queued.  Run SAVE_CONFIG when ready.")

    def _restore_selector_current(self, gcmd, sn):
        """Restore normal selector current and clear all stallguard TMC fields."""
        owner = self.owner
        try:
            owner.gcode.run_script_from_command(
                "SET_TMC_FIELD STEPPER=%s FIELD=stop_enable VALUE=0" % sn)
            owner.gcode.run_script_from_command(
                "SET_TMC_FIELD STEPPER=%s FIELD=diag1_stall VALUE=0" % sn)
            owner.gcode.run_script_from_command(
                "SET_TMC_CURRENT STEPPER=%s CURRENT=0.600" % sn)
        except Exception as e:
            logging.warning("SACalibration: failed to restore selector current: %s", e)

    # ══════════════════════════════════════════════════════════════════════════
    # Bowden tube length calibration
    # ══════════════════════════════════════════════════════════════════════════

    def calibrate_bowden(self, gcmd):
        """SA_CALIBRATE_BOWDEN TOOL=N — guided Bowden length calibration.

        Requires extruder_sensor_N configured for this path.
        Runs 3 feed-to-sensor trials and averages the encoder distance at
        trigger, saving the result as bowden_length_N.
        """
        owner  = self.owner
        motion = owner.motion
        path   = gcmd.get_int('TOOL', minval=0, maxval=owner.num_paths - 1)

        gcmd.respond_info(
            "SA BOWDEN CALIBRATION — Path %d\n"
            "================================" % path)

        has_extruder_sensor = bool(owner._extruder_sensor_names[path])
        if not has_extruder_sensor:
            raise gcmd.error(
                "SA CAL: No extruder_sensor_%d configured. "
                "Add extruder_sensor_%d = filament_switch_sensor extruder_sensor_%d "
                "to [stealth_autoloader] in hardware.cfg."
                % (path, path, path))

        if not owner._entry_sensor_active(path):
            raise gcmd.error(
                "SA CAL: No filament at entry of path %d. Load a spool first." % path)

        # ── Ask for initial length estimate ───────────────────────────────────
        estimated_str = self._wait_for_value(
            gcmd,
            "Enter estimated Bowden tube length for path %d (mm). "
            "We approach at 90%% of this value first." % path,
            "800")
        try:
            estimated = float(estimated_str)
        except ValueError:
            raise gcmd.error("SA CAL: Invalid length '%s'" % estimated_str)
        if estimated <= 0.0:
            raise gcmd.error("SA CAL: Estimated length must be > 0")

        approach_length = estimated * 0.9
        gcmd.respond_info(
            "SA CAL: Initial fast approach: %.0fmm (90%% of %.0fmm estimate). "
            "Then inching until sensor triggers." % (approach_length, estimated))

        measured_lengths = []

        # ── 3 measurement trials ──────────────────────────────────────────────
        for trial in range(3):
            gcmd.respond_info("SA CAL: === Trial %d/3 ===" % (trial + 1))

            # Select path and engage drive gear
            motion.servo_disengage()
            motion.selector_move_to(owner._selector_positions[path])
            owner.current_path = path
            motion.servo_engage()

            enc = owner._encoder(path)
            enc.set_direction(forward=True)
            enc.reset_distance()

            # Fast approach to 90% of estimate
            gcmd.respond_info(
                "SA CAL: Fast approach to %.0fmm..." % approach_length)
            current_pos = 0.0
            while current_pos < approach_length:
                step = min(owner.feed_step_size * 5.0, approach_length - current_pos)
                motion.drive_move(step)
                current_pos += step
                owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)
                if owner._extruder_sensor_active(path):
                    break

            if owner._extruder_sensor_active(path):
                # Sensor triggered during fast approach (tube is shorter than estimated)
                length = enc.get_distance()
                gcmd.respond_info(
                    "SA CAL: Extruder sensor triggered during fast approach at %.2fmm. "
                    "Tube is shorter than estimated." % length)
            else:
                # Inch forward until sensor triggers
                gcmd.respond_info(
                    "SA CAL: Inching forward until extruder sensor triggers...")
                inch_max = estimated * 0.3  # allow up to 30% overshoot
                inched   = 0.0
                while not owner._extruder_sensor_active(path) and inched < inch_max:
                    motion.drive_move(owner.feed_step_size)
                    inched += owner.feed_step_size
                    owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)

                if not owner._extruder_sensor_active(path):
                    motion.servo_disengage()
                    raise gcmd.error(
                        "SA CAL: Extruder sensor for path %d not triggered after %.0fmm. "
                        "Check sensor wiring or increase estimated length." % (path, estimated))

                length = enc.get_distance()
                gcmd.respond_info(
                    "SA CAL: Extruder sensor triggered at encoder=%.2fmm." % length)

            measured_lengths.append(length)

            # Retract back past encoder so filament clears for next trial
            gcmd.respond_info("SA CAL: Retracting filament for next trial...")
            enc.set_direction(forward=False)
            enc.reset_distance()
            retract_target = length + 20.0  # clear the encoder by 20mm margin

            retracted = 0.0
            while retracted < retract_target:
                motion.drive_move(-owner.feed_step_size)
                retracted += owner.feed_step_size
                owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)

            motion.servo_disengage()
            owner.reactor.pause(owner.reactor.monotonic() + 0.5)

        # ── Average results ───────────────────────────────────────────────────
        avg_length = sum(measured_lengths) / len(measured_lengths)
        spread     = max(measured_lengths) - min(measured_lengths)

        gcmd.respond_info(
            "SA CAL: Bowden length trials: %s\n"
            "  Average: %.2fmm\n"
            "  Spread:  %.2fmm%s"
            % ([round(x, 2) for x in measured_lengths],
               avg_length,
               spread,
               "  ← high — check for sensor bounce or filament inconsistency"
               if spread > 3.0 else ""))

        # ── Save ──────────────────────────────────────────────────────────────
        self._save_config_value(
            'stealth_autoloader', 'bowden_length_%d' % path, '%.2f' % avg_length)
        owner._bowden_lengths[path] = avg_length
        gcmd.respond_info(
            "SA CAL: bowden_length_%d = %.2fmm queued for SAVE_CONFIG." % (path, avg_length))

        if self._prompt_yes_no(
                gcmd, "Save Bowden calibration for path %d and restart printer now?" % path):
            self._trigger_save_config(gcmd)
        else:
            gcmd.respond_info("SA CAL: Queued. Run SAVE_CONFIG when ready.")
