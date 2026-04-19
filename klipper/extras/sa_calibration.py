# sa_calibration.py — Stealth Autoloader calibration routines
#
# Phase-based state machine design:
#   - Each calibration command kicks off phase 0 (automated work + prompt).
#   - SA_RESPOND VALUE=<answer> dispatches to the next phase — no blocking wait loops.
#   - State stored in owner._cal_state / owner._cal_data; cleared on Klipper restart.
#   - Calibrated values are written immediately to save_variables (no SAVE_CONFIG needed).
#   - Values are loaded from save_variables at klippy:ready, overriding hardware.cfg defaults.
#
# Calibration states:
#   sel_confirm
#   drv_path / drv_mark / drv_meas / drv_save
#   enc_zero_N / enc_exit_N
#   bow_est_N

import sys, os as _os, re
_extras_dir = _os.path.dirname(_os.path.abspath(__file__))
if _extras_dir not in sys.path:
    sys.path.insert(0, _extras_dir)

import logging


class SACalibration:

    def __init__(self, owner):
        self.owner = owner

    # ══════════════════════════════════════════════════════════════════════════
    # SA_RESPOND dispatch  (called from StealthAutoloader._cmd_respond)
    # ══════════════════════════════════════════════════════════════════════════

    def respond(self, gcmd, value):
        """Route an SA_RESPOND value to the correct phase handler."""
        owner = self.owner
        state = owner._cal_state

        if state is None:
            gcmd.respond_info("SA: No calibration is waiting for input.")
            return

        val = value.strip()
        if val.lower() in ('abort', 'cancel'):
            self._abort(gcmd)
            return

        try:
            if state.startswith('sel_'):
                self._sel_respond(gcmd, state, val)
            elif state.startswith('drv_'):
                self._drv_respond(gcmd, state, val)
            elif state.startswith('enc_'):
                self._enc_respond(gcmd, state, val)
            elif state.startswith('bow_'):
                self._bow_respond(gcmd, state, val)
            elif state == 'load_purge':
                self.owner.sequences._load_purge_respond(gcmd, val)
            elif state == 'unload_done':
                self.owner.sequences._unload_done_respond(gcmd, val)
            else:
                gcmd.respond_info(
                    "SA CAL: Unknown calibration state '%s' — clearing." % state)
                self._clear()
        except Exception as e:
            logging.exception("SACalibration: error in respond()")
            self._clear()
            raise gcmd.error("SA CAL: %s" % str(e))

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _abort(self, gcmd):
        gcmd.respond_info("SA CAL: Calibration aborted.")
        self.owner.motion.servo_disengage()
        self._clear()

    def _safe_selector_move(self, motion, position_mm):
        """Disengage servo if engaged, move selector, restore servo state."""
        was_engaged = self.owner._servo_is_engaged
        if was_engaged:
            motion.servo_disengage()
        motion.selector_move_to(position_mm)
        if was_engaged:
            motion.servo_engage()

    def _clear(self):
        self.owner._cal_state = None
        self.owner._cal_data  = {}

    def _yes(self, value):
        return value.lower() in ('yes', 'y', '1', 'true', 'ok')

    def _prompt(self, gcmd, message, *commands):
        """Print a message with copy-paste commands clearly separated."""
        lines = [
            "",
            "SA CAL: " + message,
            "",
        ]
        for cmd in commands:
            lines.append("  " + cmd)
        lines.append("")
        gcmd.respond_info("\n".join(lines))

    def _save_variable(self, key, value):
        """Write a calibration value to save_variables immediately — no restart needed."""
        self.owner.gcode.run_script_from_command(
            "SAVE_VARIABLE VARIABLE=%s VALUE=%s" % (key, str(value)))

    def _patch_hardware_cfg(self, section, option, value):
        """Edit a key in hardware.cfg directly — no SAVE_CONFIG needed.

        Returns (True, path) on success, (False, error_msg) on failure.
        Looks for hardware.cfg alongside the primary Klipper config file.
        """
        try:
            config_file = self.owner.printer.get_start_args().get('config_file', '')
            config_dir  = _os.path.dirname(config_file)
            hw_cfg      = _os.path.join(
                config_dir, 'stealth-autoloader', 'hardware.cfg')
            if not _os.path.exists(hw_cfg):
                return False, "hardware.cfg not found at %s" % hw_cfg

            with open(hw_cfg, 'r') as f:
                lines = f.readlines()

            in_section = False
            patched    = False
            new_lines  = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith('['):
                    in_section = (stripped == '[%s]' % section)
                if in_section and re.match(
                        r'^' + re.escape(option) + r'\s*[=:]', stripped):
                    line    = re.sub(r'(\s*[=:]\s*)\S+', r'\g<1>' + value, line)
                    patched = True
                new_lines.append(line)

            if not patched:
                return False, ("'%s' not found in [%s]" % (option, section))

            with open(hw_cfg, 'w') as f:
                f.writelines(new_lines)
            logging.info("SACalibration: patched %s [%s] %s = %s",
                         hw_cfg, section, option, value)
            return True, hw_cfg
        except Exception as e:
            return False, str(e)

    def _restore_selector_current(self, gcmd, sn):
        owner = self.owner
        try:
            owner.gcode.run_script_from_command(
                "SET_TMC_CURRENT STEPPER=%s CURRENT=0.600" % sn)
        except Exception as e:
            logging.warning("SACalibration: failed to restore selector current: %s", e)

    # ══════════════════════════════════════════════════════════════════════════
    # SA_CALIBRATE_SELECTOR
    # ══════════════════════════════════════════════════════════════════════════

    def calibrate_selector_auto(self, gcmd):
        """Phase 0 — automated sweep + measurement, then prompt to accept."""
        owner  = self.owner
        motion = owner.motion
        sn     = owner._sel_name()

        if owner._cal_state is not None:
            raise gcmd.error(
                "SA CAL: Calibration already in progress (state=%s).\n"
                "  SA_RESPOND VALUE=abort" % owner._cal_state)

        gcmd.respond_info(
            "SA SELECTOR CALIBRATION\n"
            "========================\n"
            "Homing → sweep to far stop → home back → calculate positions.\n"
            "No filament loaded. Servo must be free.")

        # ── Step 1: Home ──────────────────────────────────────────────────────
        gcmd.respond_info("SA CAL: Homing...")
        motion.selector_home()

        # ── Step 2: Stepper object for MCU position measurement ───────────────
        sel_obj   = owner.printer.lookup_object('manual_stepper sa_selector')
        stepper   = sel_obj.get_steppers()[0]
        step_dist = stepper.get_step_dist()

        # ── Steps 3+4: Sweep to far wall ─────────────────────────────────────
        # Overshoot move at reduced current — brief grind at far wall is
        # acceptable for one-time calibration. Current is restored immediately
        # after the sweep. Measurement accuracy comes from homing back, not
        # from detecting the far-wall stop.
        far_target = owner.selector_max_travel + 30.0

        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % sn)
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)
        cal_current = owner.selector_cal_current
        owner.gcode.run_script_from_command(
            "SET_TMC_CURRENT STEPPER=%s CURRENT=%.3f" % (sn, cal_current))
        gcmd.respond_info("SA CAL: Sweeping to far wall (%.0fmm) at %.2fA..." % (far_target, cal_current))
        owner.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s MOVE=%.2f SPEED=%.1f SYNC=1"
            % (sn, far_target, owner.selector_homing_speed))
        owner.gcode.run_script_from_command("M400")
        owner.gcode.run_script_from_command(
            "SET_TMC_CURRENT STEPPER=%s CURRENT=0.600" % sn)
        owner.reactor.pause(owner.reactor.monotonic() + 0.3)
        gcmd.respond_info("SA CAL: Sweep complete.")

        # ── Zero at far wall, home back to measure total travel ───────────────
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)
        mcu_far = stepper.get_mcu_position()
        home_target = -(owner.selector_max_travel + 50.0)

        gcmd.respond_info("SA CAL: Homing back to measure total travel...")
        owner.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s MOVE=%.1f SPEED=%.1f STOP_ON_ENDSTOP=1"
            % (sn, home_target, owner.selector_homing_speed))
        owner.gcode.run_script_from_command("M400")

        mcu_home     = stepper.get_mcu_position()
        total_travel = abs(mcu_far - mcu_home) * step_dist
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)

        gcmd.respond_info(
            "SA CAL: MCU steps far=%d  home=%d  delta=%d  step_dist=%.5fmm\n"
            "SA CAL: Total travel: %.2fmm"
            % (mcu_far, mcu_home, abs(mcu_far - mcu_home), step_dist, total_travel))

        # ── Step 8: Restore current and internal state ────────────────────────
        self._restore_selector_current(gcmd, sn)
        owner.motion._selector_position = 0.0
        owner.current_path = -1

        # ── Step 9: Calculate positions ───────────────────────────────────────
        n          = owner.num_paths
        end_offset = owner.selector_end_offset
        path_width = owner.path_width
        usable     = total_travel - end_offset

        if n == 1:
            positions = [0.0]
            spacing   = 0.0
        else:
            if usable < (n - 1) * 5.0:
                raise gcmd.error(
                    "SA CAL: Usable travel %.1fmm (total %.1fmm - offset %.1fmm) "
                    "too short for %d paths. "
                    "Check assembly or reduce selector_end_offset."
                    % (usable, total_travel, end_offset, n))
            spacing   = usable / float(n - 1)
            positions = [round(i * spacing, 2) for i in range(n)]

        offset_note = ""
        if end_offset != 0.0:
            offset_note = ("  end_offset %.2fmm  usable %.2fmm\n"
                           % (end_offset, usable))
        width_note  = ""
        if path_width > 0.0:
            width_note = (
                "  path_width configured %.1fmm  calculated %.2fmm  "
                "delta %.2fmm\n" % (path_width, spacing, abs(spacing - path_width)))

        pos_lines = "\n".join(
            "  Path %d: %.2fmm" % (i, p) for i, p in enumerate(positions))
        gcmd.respond_info(
            "SA CAL: Total travel %.2fmm → %d paths  spacing %.2fmm\n%s%s%s"
            % (total_travel, n, spacing, offset_note, width_note, pos_lines))

        owner._cal_data  = {'positions': positions, 'total_travel': total_travel}
        owner._cal_state = 'sel_confirm'

        self._prompt(gcmd,
            "Accept these positions?",
            "SA_RESPOND VALUE=yes",
            "SA_RESPOND VALUE=no")

    def _sel_respond(self, gcmd, state, value):
        owner = self.owner

        if state == 'sel_confirm':
            if self._yes(value):
                positions = owner._cal_data['positions']
                for i, pos in enumerate(positions):
                    owner._selector_positions[i] = pos
                    self._save_variable('selector_position_%d' % i, '%.2f' % pos)
                self._clear()
                gcmd.respond_info(
                    "SA CAL: Selector positions saved immediately — "
                    "effective now, no restart needed.\n"
                    "Run SA_HOME then SA_SELECT TOOL=N to verify each position.")
                owner.motion.selector_home()
            else:
                self._clear()
                owner.motion.selector_home()
                gcmd.respond_info(
                    "SA CAL: Positions NOT saved.\n"
                    "Adjust assembly or selector_end_offset and retry.")

    # ══════════════════════════════════════════════════════════════════════════
    # SA_CALIBRATE_DRIVE
    # ══════════════════════════════════════════════════════════════════════════

    def calibrate_drive(self, gcmd):
        """Phase 0 — intro, ask which path has filament."""
        owner = self.owner

        if owner._cal_state is not None:
            raise gcmd.error(
                "SA CAL: Calibration already in progress (state=%s).\n"
                "  SA_RESPOND VALUE=abort" % owner._cal_state)

        if not owner._selector_homed:
            gcmd.respond_info("SA CAL: Selector not homed — homing now...")
            owner.motion.selector_home()

        gcmd.respond_info(
            "SA DRIVE CALIBRATION\n"
            "====================\n"
            "Calibrates drive motor rotation_distance — one motor, one-time setup.\n"
            "\n"
            "Requirements: filament loaded past drive gear on one path. Calipers or ruler.")

        owner._cal_data  = {'attempt': 0, 'best_rd': None, 'path': None,
                            'original_rd': None, 'original_sd': None}
        owner._cal_state = 'drv_path'

        self._prompt(gcmd,
            "Which path has filament loaded past the drive gear? (0-%d)"
            % (owner.num_paths - 1),
            "SA_RESPOND VALUE=0",
            "SA_RESPOND VALUE=1  (etc.)")

    def _drv_respond(self, gcmd, state, value):
        owner  = self.owner
        motion = owner.motion
        data   = owner._cal_data

        if state == 'drv_path':
            try:
                path = int(value)
            except ValueError:
                gcmd.respond_info(
                    "SA CAL: Enter a path number (0-%d)." % (owner.num_paths - 1))
                return
            if not (0 <= path < owner.num_paths):
                gcmd.respond_info("SA CAL: Path %d out of range." % path)
                return

            gcmd.respond_info("SA CAL: Selecting path %d..." % path)
            self._safe_selector_move(motion, owner._selector_positions[path])
            owner.current_path = path
            motion.servo_engage()

            drive_obj = owner.printer.lookup_object(owner.drive_stepper_name)
            steppers  = drive_obj.get_steppers()
            best_rd   = steppers[0].get_rotation_distance()[0] if steppers else 22.0
            orig_sd   = steppers[0].get_step_dist() if steppers else None

            data.update({'path': path, 'best_rd': best_rd, 'attempt': 0,
                         'original_rd': best_rd, 'original_sd': orig_sd,
                         'steppers': steppers, 'cmd_mm': 100.0})
            owner._cal_state = 'drv_mark'

            self._prompt(gcmd,
                "Mark the filament at the encoder exit (tape or pen). Then confirm ready.",
                "SA_RESPOND VALUE=yes")

        elif state == 'drv_mark':
            attempt        = data['attempt'] + 1
            data['attempt'] = attempt
            path   = data['path']
            cmd_mm = data.get('cmd_mm', 100.0)

            gcmd.respond_info(
                "SA CAL: Attempt %d/3 — commanding %.1fmm..." % (attempt, cmd_mm))
            enc = owner._encoder(path)
            enc.set_direction(forward=True)
            enc.reset_distance()
            motion.drive_move(cmd_mm, speed=owner.feed_speed * 0.5)
            motion.drive_disable()
            data['last_cmd_mm'] = cmd_mm

            owner._cal_state = 'drv_meas'
            self._prompt(gcmd,
                "Measure from your mark to the new filament end (target: 100mm).",
                "SA_RESPOND VALUE=100.0  (replace with actual mm)")

        elif state == 'drv_meas':
            try:
                measured = float(value)
            except ValueError:
                gcmd.respond_info("SA CAL: Enter a number (e.g. 103.5).")
                return
            if measured <= 0.0:
                gcmd.respond_info("SA CAL: Must be > 0.")
                return

            cmd_mm   = data.get('last_cmd_mm', 100.0)
            orig_rd  = data['original_rd']
            attempt  = data['attempt']
            target   = 100.0
            error    = abs(measured - target)
            pct      = error / target * 100.0

            # True rotation_distance based on original rd and actual ratio this pass
            new_rd   = orig_rd * (measured / cmd_mm)
            # Command this distance next pass so stepper outputs 100mm
            next_cmd = cmd_mm * (target / measured)

            data['best_rd'] = new_rd
            data['cmd_mm']  = next_cmd

            done = (attempt >= 3)
            gcmd.respond_info(
                "SA CAL: Pass %d/3 — commanded %.1fmm  measured %.2fmm  "
                "error %.2fmm (%.1f%%)\n"
                "  rotation_distance: %.4f → %.4f  next_cmd: %.1fmm%s"
                % (attempt, cmd_mm, measured, error, pct, orig_rd, new_rd, next_cmd,
                   "  ✓ done" if done else ""))

            if done:
                motion.servo_disengage()
                owner._cal_state = 'drv_save'
                self._prompt(gcmd,
                    "Save rotation_distance=%.4f?" % new_rd,
                    "SA_RESPOND VALUE=yes",
                    "SA_RESPOND VALUE=no")
            else:
                owner._cal_state = 'drv_mark'
                self._prompt(gcmd,
                    "Re-mark the filament at its new position, then confirm ready.",
                    "SA_RESPOND VALUE=yes")

        elif state == 'drv_save':
            new_rd   = data['best_rd']
            orig_rd  = data.get('original_rd') or new_rd
            self._clear()

            if self._yes(value):
                self._save_variable('drive_rotation_distance', '%.4f' % new_rd)
                ok, result = self._patch_hardware_cfg(
                    'manual_stepper sa_drive', 'rotation_distance', '%.4f' % new_rd)
                if ok:
                    gcmd.respond_info(
                        "SA CAL: rotation_distance=%.4f written to hardware.cfg.\n"
                        "Restart Klipper — 100mm will equal 100mm." % new_rd)
                else:
                    gcmd.respond_info(
                        "SA CAL: rotation_distance=%.4f saved to variables.cfg.\n"
                        "Could not auto-update hardware.cfg (%s).\n"
                        "Manually set rotation_distance: %.4f in "
                        "[manual_stepper sa_drive] then restart Klipper."
                        % (new_rd, result, new_rd))
            else:
                gcmd.respond_info(
                    "SA CAL: Not saved. rotation_distance remains %.4f." % orig_rd)

    # ══════════════════════════════════════════════════════════════════════════
    # SA_CALIBRATE_ENCODER
    # ══════════════════════════════════════════════════════════════════════════

    def calibrate_encoder(self, gcmd):
        """Phase 0 — select path, engage, prompt to mark filament."""
        owner = self.owner
        path  = gcmd.get_int('TOOL', minval=0, maxval=owner.num_paths - 1)

        if owner._cal_state is not None:
            raise gcmd.error(
                "SA CAL: Calibration already in progress (state=%s).\n"
                "  SA_RESPOND VALUE=abort" % owner._cal_state)

        if not owner._selector_homed:
            gcmd.respond_info("SA CAL: Selector not homed — homing now...")
            owner.motion.selector_home()

        gcmd.respond_info(
            "SA ENCODER CALIBRATION — Path %d\n"
            "==================================\n"
            "Feeds until encoder reads 100mm, you measure actual — 3 passes.\n"
            "\n"
            "Requirements: filament through drive gear AND encoder for path %d.\n"
            "~400mm of free filament needed." % (path, path))

        gcmd.respond_info("SA CAL: Selecting path %d..." % path)
        self._safe_selector_move(owner.motion, owner._selector_positions[path])
        owner.current_path = path
        owner.motion.servo_engage()

        enc = owner._encoder(path)
        owner._cal_data  = {
            'path':         path,
            'attempt':      0,
            'best_mpp':     enc.mm_per_pulse,
            'original_mpp': enc.mm_per_pulse,
        }
        owner._cal_state = 'enc_mark_%d' % path

        self._prompt(gcmd,
            "Mark the filament at the encoder exit, then confirm ready.",
            "SA_RESPOND VALUE=yes")

    def _enc_respond(self, gcmd, state, value):
        owner  = self.owner
        motion = owner.motion
        data   = owner._cal_data
        path   = int(state.rsplit('_', 1)[-1])
        enc    = owner._encoder(path)

        if state.startswith('enc_mark_'):
            attempt        = data['attempt'] + 1
            data['attempt'] = attempt
            target         = 100.0
            max_travel     = 600.0
            poll_interval  = 0.05   # seconds between encoder checks
            cal_speed      = owner.feed_speed * 0.5

            # Apply current best mm_per_pulse so encoder counts correctly
            enc.mm_per_pulse = data['best_mpp']
            enc.set_direction(forward=True)
            enc.reset_distance()

            dn = owner._drv_name()
            motion.servo_engage()
            motion._cancel_timeout(dn)
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s ENABLE=1" % dn)

            gcmd.respond_info(
                "SA CAL: Attempt %d/3 — stepping until encoder reads "
                "%.0fmm (mm_per_pulse=%.5f)..." % (attempt, target, data['best_mpp']))

            # Step-by-step: motor stops fully between steps so encoder
            # pulses are delivered one-by-one (no CAN batching issue).
            # Fast approach until 80% of target, then 3mm precision steps.
            fast_step     = 10.0
            slow_step     = 3.0
            slow_threshold = target * 0.8
            travelled     = 0.0

            while enc.get_distance() < target and travelled < max_travel:
                step = slow_step if enc.get_distance() >= slow_threshold else fast_step
                motion.drive_move(step, speed=cal_speed)
                travelled += step

            enc_reading = enc.get_distance()

            if enc_reading < 3.0:
                motion.servo_disengage()
                motion.drive_disable()
                self._clear()
                raise gcmd.error(
                    "SA CAL: Encoder %d not responding — %.2fmm counted after "
                    "%.0fmm travel. Check wiring and filament grip."
                    % (path, enc_reading, max_travel))

            gcmd.respond_info(
                "SA CAL: Motor stopped — encoder reads %.2fmm." % enc_reading)

            # Hold servo + motor torque while user measures
            dn = owner._drv_name()
            motion._cancel_timeout(dn)
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s ENABLE=1" % dn)
            data['enc_reading'] = enc_reading
            owner._cal_state = 'enc_meas_%d' % path

            self._prompt(gcmd,
                "Servo engaged, drive holding. Measure from your mark to "
                "filament end (target 100mm).",
                "SA_RESPOND VALUE=100.0  (replace with actual mm)")

        elif state.startswith('enc_meas_'):
            # Release motor torque but keep servo engaged —
            # user needs grip to reposition filament with the drive knob
            motion.drive_disable()

            try:
                actual = float(value)
            except ValueError:
                gcmd.respond_info("SA CAL: Enter a number (e.g. 199.5).")
                return
            if actual <= 0.0:
                gcmd.respond_info("SA CAL: Must be > 0.")
                return

            current_mpp = data['best_mpp']
            attempt     = data['attempt']
            enc_reading = data['enc_reading']
            # Use actual enc_reading (not assumed target) so formula is valid
            # whether motor stopped at target or was halted by max_travel limit
            new_mpp     = current_mpp * (actual / enc_reading)
            correction  = abs(new_mpp - current_mpp) / current_mpp * 100.0

            data['best_mpp'] = new_mpp
            # Apply immediately so next pass uses corrected mpp
            enc.mm_per_pulse = new_mpp

            done = (attempt >= 3)
            gcmd.respond_info(
                "SA CAL: Pass %d/3 — encoder %.2fmm  actual %.2fmm  "
                "mpp correction %.2f%%\n"
                "  mm_per_pulse: %.5f → %.5f%s"
                % (attempt, enc_reading, actual, correction,
                   current_mpp, new_mpp, "  ✓ done" if done else ""))

            if done:
                motion.servo_disengage()
                owner._cal_state = 'enc_save_%d' % path
                self._prompt(gcmd,
                    "Save mm_per_pulse=%.5f?" % new_mpp,
                    "SA_RESPOND VALUE=yes",
                    "SA_RESPOND VALUE=no")
            else:
                # Servo still engaged — use knob to re-mark filament position
                owner._cal_state = 'enc_mark_%d' % path
                self._prompt(gcmd,
                    "Servo engaged — use knob to reposition filament to new mark, "
                    "then confirm ready.",
                    "SA_RESPOND VALUE=yes")

        elif state.startswith('enc_save_'):
            new_mpp  = data['best_mpp']
            orig_mpp = data.get('original_mpp') or new_mpp
            self._clear()

            if self._yes(value):
                enc.mm_per_pulse = new_mpp
                self._save_variable('encoder_mpp_%d' % path, '%.5f' % new_mpp)
                ok, result = self._patch_hardware_cfg(
                    'sa_encoder %d' % path, 'mm_per_pulse', '%.5f' % new_mpp)
                if ok:
                    gcmd.respond_info(
                        "SA CAL: Encoder %d mm_per_pulse=%.5f written to "
                        "hardware.cfg — restart Klipper to apply." % (path, new_mpp))
                else:
                    gcmd.respond_info(
                        "SA CAL: Encoder %d mm_per_pulse=%.5f saved to "
                        "variables.cfg. Could not auto-update hardware.cfg (%s)."
                        % (path, new_mpp, result))
            else:
                enc.mm_per_pulse = orig_mpp
                gcmd.respond_info(
                    "SA CAL: Not saved. mm_per_pulse remains %.5f." % orig_mpp)

    # ══════════════════════════════════════════════════════════════════════════
    # SA_CALIBRATE_ENCODER_SPEED
    # ══════════════════════════════════════════════════════════════════════════

    def calibrate_encoder_speed(self, gcmd):
        """Find the max feed speed at which the encoder counts accurately.

        Steps through speeds [25, 50, 75, 100, 125, 150, 175, 200] mm/s.
        Each speed: run 100mm forward, compare encoder reading to commanded
        distance.  3 tries per speed — need 2/3 within tolerance to pass.
        Stops at first failing speed.  Saves safe_speed (max_pass * 0.8)
        to variables.cfg as 'encoder_max_speed'.
        """
        owner  = self.owner
        motion = owner.motion
        path   = gcmd.get_int('TOOL', 0, minval=0, maxval=owner.num_paths - 1)

        if owner._cal_state is not None:
            raise gcmd.error(
                "SA CAL: Calibration in progress (state=%s). SA_RESPOND VALUE=abort"
                % owner._cal_state)

        if not owner._selector_homed:
            gcmd.respond_info("SA CAL: Homing selector...")
            motion.selector_home()

        gcmd.respond_info(
            "SA ENCODER SPEED CALIBRATION — Path %d\n"
            "===========================================\n"
            "Requires filament through drive gear and encoder.\n"
            "Tests speeds 25→200mm/s, 3 tries each, 100mm per try.\n"
            "Stops at first failing speed. Safe speed (80%%) saved."
            % path)

        motion.servo_disengage()
        motion.selector_move_to(owner._selector_positions[path])
        owner.current_path = path
        motion.servo_engage()

        enc        = owner._encoder(path)
        test_dist  = 100.0
        tolerance  = 0.05   # 5% max error per try
        test_speeds = [25, 50, 75, 100, 125, 150, 175, 200]
        retract_speed = 25.0
        max_pass   = 0

        for speed in test_speeds:
            trial_errors = []
            passes = 0
            for attempt in range(3):
                enc.set_direction(forward=True)
                enc.reset_distance()
                motion.drive_move(test_dist, speed=float(speed))
                owner.reactor.pause(owner.reactor.monotonic() + 0.15)
                enc_reading = enc.get_distance()
                error = abs(enc_reading - test_dist) / test_dist
                trial_errors.append(error * 100.0)
                if error <= tolerance:
                    passes += 1
                # Retract at safe low speed — accuracy not needed here
                enc.set_direction(forward=False)
                enc.reset_distance()
                motion.drive_move(-test_dist, speed=retract_speed)
                owner.reactor.pause(owner.reactor.monotonic() + 0.2)

            avg_err = sum(trial_errors) / len(trial_errors)
            passed  = passes >= 2
            gcmd.respond_info(
                "  %3dmm/s: %s  (errors: %s  avg %.1f%%)"
                % (speed,
                   "PASS" if passed else "FAIL",
                   [round(e, 1) for e in trial_errors],
                   avg_err))

            if passed:
                max_pass = speed
            else:
                break   # no point testing faster speeds

        motion.servo_disengage()

        if max_pass == 0:
            gcmd.respond_info(
                "SA CAL: FAILED at all speeds. Check encoder wiring / mm_per_pulse.")
            return

        safe_speed = max_pass * 0.80
        self._save_variable('encoder_max_speed', '%.1f' % safe_speed)
        gcmd.respond_info(
            "SA CAL: Max reliable speed %dmm/s → safe speed %.0fmm/s (80%%).\n"
            "Saved as encoder_max_speed — Bowden cal blast speed updated automatically."
            % (max_pass, safe_speed))

    # ══════════════════════════════════════════════════════════════════════════
    # SA_CALIBRATE_BOWDEN
    # ══════════════════════════════════════════════════════════════════════════

    def calibrate_bowden(self, gcmd):
        """Phase 0 — validate sensors, prompt for estimated tube length."""
        owner = self.owner
        path  = gcmd.get_int('TOOL', minval=0, maxval=owner.num_paths - 1)

        if owner._cal_state is not None:
            raise gcmd.error(
                "SA CAL: Calibration already in progress (state=%s).\n"
                "  SA_RESPOND VALUE=abort" % owner._cal_state)

        if not owner._selector_homed:
            gcmd.respond_info("SA CAL: Selector not homed — homing now...")
            owner.motion.selector_home()

        gcmd.respond_info(
            "SA BOWDEN CALIBRATION — Path %d\n"
            "================================" % path)

        if not owner._extruder_sensor_names[path]:
            raise gcmd.error(
                "SA CAL: No extruder_sensor_%d configured.\n"
                "Add to [stealth_autoloader] in hardware.cfg:\n"
                "  extruder_sensor_%d : filament_switch_sensor extruder_sensor_%d"
                % (path, path, path))

        if not owner._entry_sensor_active(path):
            raise gcmd.error(
                "SA CAL: No filament at entry of path %d. Load a spool first." % path)

        owner._cal_data  = {'path': path, 'trials': []}
        owner._cal_state = 'bow_est_%d' % path

        self._prompt(gcmd,
            "Enter estimated Bowden tube length for path %d (mm). "
            "Over-estimate is safer — approach uses 90%% first." % path,
            "SA_RESPOND VALUE=800  (replace with your estimate)")

    def _bow_respond(self, gcmd, state, value):
        owner  = self.owner
        motion = owner.motion
        data   = owner._cal_data
        path   = int(state.rsplit('_', 1)[-1])

        if state.startswith('bow_est_'):
            try:
                estimated = float(value)
            except ValueError:
                gcmd.respond_info("SA CAL: Enter a number (e.g. 800).")
                return
            if estimated <= 0.0:
                gcmd.respond_info("SA CAL: Must be > 0.")
                return

            # Three-phase approach speeds (48V / TMC5160)
            # Blast speed: use calibrated encoder_max_speed if available, else 100mm/s safe default
            sv = owner.printer.lookup_object('save_variables', None)
            saved_max = float(sv.allVariables.get('encoder_max_speed', 0)) if sv else 0
            # encoder_max_speed tested at 100mm near tube entrance (low friction).
            # Bowden blast pushes full tube depth — apply 0.75x for tube friction load.
            blast_speed = (saved_max * 0.75) if saved_max > 0 else 75.0
            quick_speed    = 50.0              # 65–82.5% — no sensor check
            approach_speed = owner.feed_speed  # 82.5%+ — sensor polling

            # Distances scale with user's estimate:
            #   blast = 75%, quick = half of remainder (12.5%), approach = final 12.5%+
            blast_end  = estimated * 0.75
            quick_end  = blast_end + (estimated - blast_end) * 0.5   # midpoint of remainder
            # Sensor polling from quick_end; overshoot budget = 20% beyond estimated
            inch_limit = estimated * 0.20

            gcmd.respond_info(
                "SA CAL: Running 3 trials\n"
                "  Blast  %.0f–%.0fmm @ %.0fmm/s (no sensor)\n"
                "  Quick  %.0f–%.0fmm @ %.0fmm/s (no sensor)\n"
                "  Approach %.0fmm+ @ %.0fmm/s with sensor polling\n"
                "  NOTE: accuracy depends on your estimate being close to actual length."
                % (0, blast_end, blast_speed,
                   blast_end, quick_end, quick_speed,
                   quick_end, approach_speed))

            # Select path and engage servo once — stays engaged for all 3 trials
            motion.servo_disengage()
            motion.selector_move_to(owner._selector_positions[path])
            owner.current_path = path
            motion.servo_engage()

            enc = owner._encoder(path)
            retract_speed = blast_speed * 0.5

            def _retract_to_clear(fast_dist):
                """Retract fast_dist at retract_speed, then 5mm pulses at 25mm/s
                until encoder goes quiet (filament tip clears encoder).
                Max 20 slow pulses (100mm) before giving up."""
                if fast_dist > 0:
                    motion.drive_move(-fast_dist, speed=retract_speed)
                enc.set_direction(forward=False)
                for _ in range(20):
                    enc.reset_distance()
                    motion.drive_move(-5.0, speed=25.0)
                    owner.reactor.pause(owner.reactor.monotonic() + 0.15)
                    if abs(enc.get_distance()) < 0.5:
                        break
                enc.set_direction(forward=True)
                enc.reset_distance()

            # Pre-run: clear any filament stub sitting in encoder before trial 1
            gcmd.respond_info("SA CAL: Clearing encoder — retracting until filament clears...")
            _retract_to_clear(0.0)   # no fast phase — just slow pulses from current position
            gcmd.respond_info("SA CAL: Encoder cleared — starting 3 trials.")

            for trial in range(3):
                gcmd.respond_info("SA CAL: === Trial %d/3 ===" % (trial + 1))

                enc.set_direction(forward=True)
                enc.reset_distance()

                # Phase 1: blast to 75% — single move, no sensor check
                motion.drive_move(blast_end, speed=blast_speed)

                # Phase 2: quick to midpoint of remainder — single move, no sensor check
                motion.drive_move(quick_end - blast_end, speed=quick_speed)

                # Phase 3: sensor polling from quick_end until triggered or overshoot limit
                triggered = False
                inched    = 0.0
                while not triggered and inched < inch_limit:
                    motion.drive_move(owner.feed_step_size, speed=approach_speed)
                    inched += owner.feed_step_size
                    owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)
                    if owner._extruder_sensor_active(path):
                        triggered = True

                if not triggered:
                    motion.servo_disengage()
                    self._clear()
                    raise gcmd.error(
                        "SA CAL: Extruder sensor path %d not triggered within %.0fmm of "
                        "your estimate (%.0fmm). Re-run with a larger estimate."
                        % (path, inch_limit, estimated))

                length = enc.get_distance()
                gcmd.respond_info("SA CAL: Sensor triggered at %.2fmm." % length)
                data['trials'].append(length)

                # Retract 95% fast, then slow-pulse until encoder goes quiet
                # Stops before overshooting drive gears — no fixed overshoot distance
                _retract_to_clear(length * 0.95)
                owner.reactor.pause(owner.reactor.monotonic() + 0.3)

            motion.servo_disengage()

            trials     = data['trials']
            avg_length = sum(trials) / len(trials)
            spread     = max(trials) - min(trials)

            gcmd.respond_info(
                "SA CAL: Bowden path %d — trials %s\n"
                "  Average: %.2fmm  Spread: %.2fmm%s"
                % (path, [round(x, 2) for x in trials], avg_length, spread,
                   "  <- high, check sensor bounce" if spread > 3.0 else ""))

            # Update live state and persist immediately
            owner._bowden_lengths[path] = avg_length
            self._save_variable('bowden_length_%d' % path, '%.2f' % avg_length)
            self._clear()
            gcmd.respond_info(
                "SA CAL: bowden_length_%d=%.2fmm saved — "
                "effective immediately, no restart needed." % (path, avg_length))
