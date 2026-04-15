# stealth_autoloader.py - Stealth Autoloader main controller
#
# Single [stealth_autoloader] config section controls the entire system.
#
# Motion topology:
#   Selector motor  : moves drive gear carriage to align with the active path
#   Drive motor     : single gear that moves filament once path is selected
#   Engage servo    : clamps drive gear into active path (driven) or releases (neutral)
#   Per-path encoder: one fixed encoder per path — never moves, always counting
#                     Klipper's buttons module fires interrupt callbacks for each
#                     encoder independently; all 6 accumulate simultaneously with
#                     no data loss (reactor event queue serialises the callbacks)
#   Entry sensor    : one fixed sensor per path at the roll end
#
# Encoder strategy
# ─────────────────
# Each encoder lives permanently inside its filament path, between the drive
# engagement point and the extruder. When path N is selected and the servo is
# engaged, only encoder N is in the active filament line. Pulses on any other
# encoder during that time indicate ambient vibration, not filament movement.
#
# Path state
# ──────────
#   unknown  – state not confirmed (after boot or explicit reset)
#   empty    – no filament in path (entry sensor off, or confirmed by unload)
#   partial  – filament is in the tube but not loaded to the nozzle
#   loaded   – filament loaded all the way to the nozzle tip
#
# The entry sensor provides real-time "filament present at roll end" status.
# The path state tracks "loaded to nozzle" status and persists across moves.
#
# GCode commands (all registered by Python — not in macros):
#   SA_HOME                                 Home selector motor to endstop
#   SA_SELECT TOOL=N                        Position selector to path N (neutral)
#   SA_ENGAGE                               Engage drive servo
#   SA_DISENGAGE                            Disengage drive servo (neutral)
#   SA_LOAD   TOOL=N                        Full load sequence for path N
#   SA_UNLOAD TOOL=N                        Full unload sequence for path N
#   SA_STATUS                               Print all path states + encoder distances
#   SA_BUZZ_DRIVE                           Test drive motor
#   SA_BUZZ_SELECTOR                        Test selector motor
#   SA_CALIBRATE_ENCODER TOOL=N [DIST=100]  Measure mm_per_pulse for path N encoder
#   SA_CALIBRATE_SELECTOR TOOL=N            Print current selector position for config
#   SA_SET_SELECTOR_HOME                    Zero selector at current location
#   SA_SET_STATE TOOL=N STATE=<state>       Manually override path state

import logging

class StealthAutoloader:

    # ── Path states ───────────────────────────────────────────────────────
    STATE_UNKNOWN = 'unknown'
    STATE_EMPTY   = 'empty'
    STATE_PARTIAL = 'partial'
    STATE_LOADED  = 'loaded'

    def __init__(self, config):
        self.printer  = config.get_printer()
        self.gcode    = self.printer.lookup_object('gcode')
        self.reactor  = self.printer.get_reactor()

        # ── Shared motion hardware names ──────────────────────────────────
        self.drive_stepper_name    = config.get('drive_stepper')
        self.selector_stepper_name = config.get('selector_stepper')
        self.servo_name            = config.get('servo')

        # ── Servo positions ───────────────────────────────────────────────
        self.servo_engaged_angle    = config.getfloat('servo_engaged_angle',    30.0)
        self.servo_disengaged_angle = config.getfloat('servo_disengaged_angle', 160.0)

        # ── Path count ────────────────────────────────────────────────────
        self.num_paths = config.getint('num_paths', 6)
        if not 1 <= self.num_paths <= 32:
            raise config.error("num_paths must be between 1 and 32")

        # ── Per-path config ───────────────────────────────────────────────
        self._encoder_names      = []
        self._entry_sensor_names = []
        self._selector_positions = []
        self._extruder_names     = []

        for i in range(self.num_paths):
            # Each path has its own fixed encoder
            default_enc = 'sa_encoder %d' % i
            self._encoder_names.append(
                config.get('encoder_%d' % i, default_enc))

            self._entry_sensor_names.append(
                config.get('entry_sensor_%d' % i, None))

            self._selector_positions.append(
                config.getfloat('selector_position_%d' % i, float(i) * 21.0))

            default_ext = 'extruder' if i == 0 else 'extruder%d' % i
            self._extruder_names.append(
                config.get('extruder_%d' % i, default_ext))

        # ── Motion parameters ─────────────────────────────────────────────
        self.tube_length         = config.getfloat('tube_length',           800.0)
        self.nozzle_distance     = config.getfloat('nozzle_distance',        50.0)
        self.purge_length        = config.getfloat('purge_length',           30.0)
        self.load_temperature    = config.getfloat('load_temperature',      200.0)
        self.engage_max_distance = config.getfloat('engage_max_distance',    60.0)
        self.slip_tolerance      = config.getfloat('slip_tolerance',         15.0)
        self.feed_speed          = config.getfloat('feed_speed',             50.0)
        self.feed_step_size      = config.getfloat('feed_step_size',         10.0)
        self.selector_speed      = config.getfloat('selector_speed',        200.0)
        self.sensor_delay        = config.getfloat('sensor_polling_delay',    0.2)
        self.servo_move_delay    = config.getfloat('servo_move_delay',        0.3)

        # ── Runtime state ─────────────────────────────────────────────────
        self.current_path      = -1
        self._servo_is_engaged = False
        self.path_states       = [self.STATE_UNKNOWN] * self.num_paths

        # ── Startup ───────────────────────────────────────────────────────
        self._register_commands()
        self.printer.register_event_handler('klippy:ready', self._on_ready)
        logging.info("StealthAutoloader: initialized — %d paths, %d encoders",
                     self.num_paths, self.num_paths)

    # ══════════════════════════════════════════════════════════════════════
    # Klipper lifecycle
    # ══════════════════════════════════════════════════════════════════════

    def _on_ready(self):
        self.reactor.register_callback(self._init_hardware)

    def _init_hardware(self, eventtime):
        try:
            self._servo_disengage()
            logging.info("StealthAutoloader: servo disengaged at startup")
        except Exception as e:
            logging.warning("StealthAutoloader: servo init failed: %s", e)

    # ══════════════════════════════════════════════════════════════════════
    # Hardware accessors
    # ══════════════════════════════════════════════════════════════════════

    def _encoder(self, path):
        """Return the sa_encoder object for the given path."""
        return self.printer.lookup_object(self._encoder_names[path])

    def _entry_sensor_active(self, path):
        """True if filament is present at the entry of path N."""
        name = self._entry_sensor_names[path]
        if not name:
            return False
        try:
            return self.printer.lookup_object(name).get_status(
                self.reactor.monotonic())['filament_detected']
        except Exception:
            return False

    def _encoder_distance(self, path):
        """Return current encoder distance for path, or None on error."""
        try:
            return self._encoder(path).get_distance()
        except Exception:
            return None

    def _encoder_mm_per_pulse(self, path):
        try:
            return self._encoder(path).mm_per_pulse
        except Exception:
            return None

    # ══════════════════════════════════════════════════════════════════════
    # Servo control
    # ══════════════════════════════════════════════════════════════════════

    def _servo_short_name(self):
        return self.servo_name.split()[-1]

    def _servo_engage(self):
        self.gcode.run_script_from_command(
            "SET_SERVO SERVO=%s ANGLE=%.1f"
            % (self._servo_short_name(), self.servo_engaged_angle))
        self.reactor.pause(self.reactor.monotonic() + self.servo_move_delay)
        self._servo_is_engaged = True

    def _servo_disengage(self):
        self.gcode.run_script_from_command(
            "SET_SERVO SERVO=%s ANGLE=%.1f"
            % (self._servo_short_name(), self.servo_disengaged_angle))
        self.reactor.pause(self.reactor.monotonic() + self.servo_move_delay)
        self._servo_is_engaged = False

    # ══════════════════════════════════════════════════════════════════════
    # Selector motor
    # ══════════════════════════════════════════════════════════════════════

    def _sel_name(self):
        return self.selector_stepper_name.split()[-1]

    def _drv_name(self):
        return self.drive_stepper_name.split()[-1]

    def _selector_home(self):
        sn = self._sel_name()
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % sn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)
        self.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s MOVE=-250 SPEED=%.1f STOP_ON_ENDSTOP=1"
            % (sn, self.selector_speed * 0.5))
        self.gcode.run_script_from_command("M400")
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)
        self.current_path = -1

    def _selector_move_to(self, position_mm):
        sn = self._sel_name()
        self.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s ENABLE=1 MOVE=%.3f SPEED=%.1f"
            % (sn, position_mm, self.selector_speed))
        self.gcode.run_script_from_command("M400")

    def _select_path(self, path):
        """Disengage servo, then move selector carriage to path N."""
        self._servo_disengage()
        self._selector_move_to(self._selector_positions[path])
        self.current_path = path

    # ══════════════════════════════════════════════════════════════════════
    # Drive motor
    # ══════════════════════════════════════════════════════════════════════

    def _drive_move(self, distance_mm, speed=None):
        if speed is None:
            speed = self.feed_speed
        dn = self._drv_name()
        self.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s ENABLE=1 MOVE=%.3f SPEED=%.1f"
            % (dn, distance_mm, speed))
        self.gcode.run_script_from_command("M400")

    # ══════════════════════════════════════════════════════════════════════
    # Encoder helpers
    # ══════════════════════════════════════════════════════════════════════

    def _motion_threshold(self, path):
        """Minimum encoder distance that means filament is actually moving."""
        mpp = self._encoder_mm_per_pulse(path)
        return (mpp * 3.0) if mpp else 1.5

    def _check_slip(self, gcmd, path, encoder_dist, stepper_dist):
        if stepper_dist < 50.0:
            return
        pct = abs(encoder_dist - stepper_dist) / stepper_dist * 100.0
        if pct > self.slip_tolerance:
            gcmd.respond_info(
                "SA WARNING path %d: slip — stepper=%.1fmm encoder=%.1fmm (%.1f%%)"
                % (path, stepper_dist, encoder_dist, pct))

    # ══════════════════════════════════════════════════════════════════════
    # Load sequence
    # ══════════════════════════════════════════════════════════════════════

    def do_load(self, gcmd, path):
        enc = self._encoder(path)

        gcmd.respond_info("SA: === LOAD path %d ===" % path)

        # Entry sensor check
        if not self._entry_sensor_active(path):
            gcmd.respond_info(
                "SA: ERROR — no filament at entry of path %d. "
                "Insert filament roll and retry." % path)
            return

        # Position selector, engage drive
        gcmd.respond_info("SA: Selecting path %d (selector → %.1fmm)..."
                          % (path, self._selector_positions[path]))
        self._select_path(path)
        self._servo_engage()

        # Prepare this path's encoder
        enc.set_direction(forward=True)
        enc.reset_distance()

        # ── Phase 1: Engage ───────────────────────────────────────────────
        gcmd.respond_info("SA: Phase 1 — engaging filament with drive gear...")
        driven    = 0.0
        threshold = self._motion_threshold(path)

        while enc.get_distance() < threshold and driven < self.engage_max_distance:
            self._drive_move(self.feed_step_size)
            driven += self.feed_step_size
            self.reactor.pause(self.reactor.monotonic() + self.sensor_delay)

        if enc.get_distance() < threshold:
            self._servo_disengage()
            gcmd.respond_info(
                "SA: ERROR — encoder %d saw no motion after %.0fmm. "
                "Check filament is past the drive gear engagement point."
                % (path, driven))
            return

        gcmd.respond_info("SA: Encoder %d engaged — %.2fmm measured, feeding to extruder..."
                          % (path, enc.get_distance()))

        # ── Phase 2: Feed through tube ────────────────────────────────────
        driven_total = driven
        while enc.get_distance() < self.tube_length:
            self._drive_move(self.feed_step_size)
            driven_total += self.feed_step_size
            self.reactor.pause(self.reactor.monotonic() + self.sensor_delay)
            self._check_slip(gcmd, path, enc.get_distance(), driven_total)

        gcmd.respond_info(
            "SA: Path %d — filament at extruder. "
            "Stepper=%.1fmm  Encoder=%.1fmm"
            % (path, driven_total, enc.get_distance()))

        # Release drive — extruder takes over
        self._servo_disengage()
        self.path_states[path] = self.STATE_PARTIAL

        # ── Phase 3: Heat and extrude to nozzle ───────────────────────────
        extruder = self._extruder_names[path]
        gcmd.respond_info("SA: Heating %s to %.0f°C..." % (extruder, self.load_temperature))
        self.gcode.run_script_from_command(
            "TEMPERATURE_WAIT SENSOR=%s MINIMUM=%.0f" % (extruder, self.load_temperature))

        gcmd.respond_info("SA: Extruding to nozzle tip (%.1fmm)..." % self.nozzle_distance)
        self.gcode.run_script_from_command("M83")
        self.gcode.run_script_from_command("G1 E%.2f F300" % self.nozzle_distance)
        self.gcode.run_script_from_command("M400")

        # ── Phase 4: Purge ────────────────────────────────────────────────
        gcmd.respond_info("SA: Purging (%.1fmm)..." % self.purge_length)
        self.gcode.run_script_from_command("G1 E%.2f F300" % self.purge_length)
        self.gcode.run_script_from_command("M400")

        self.gcode.run_script_from_command("_CLEAN_NOZZLE")
        self.gcode.run_script_from_command("PARK_ON_COOLING_PAD")

        self.path_states[path] = self.STATE_LOADED
        gcmd.respond_info("SA: === LOAD COMPLETE — path %d ===" % path)

    # ══════════════════════════════════════════════════════════════════════
    # Unload sequence
    # ══════════════════════════════════════════════════════════════════════

    def do_unload(self, gcmd, path):
        enc = self._encoder(path)

        gcmd.respond_info("SA: === UNLOAD path %d ===" % path)

        # Retract from nozzle tip
        retract = self.nozzle_distance + self.purge_length
        gcmd.respond_info("SA: Retracting %.1fmm from nozzle..." % retract)
        self.gcode.run_script_from_command("M83")
        self.gcode.run_script_from_command("G1 E-%.2f F300" % retract)
        self.gcode.run_script_from_command("M400")
        self.path_states[path] = self.STATE_PARTIAL

        # Select path, engage drive in reverse
        gcmd.respond_info("SA: Selecting path %d and pulling filament back to entry..."
                          % path)
        self._select_path(path)
        self._servo_engage()

        enc.set_direction(forward=False)
        enc.reset_distance()

        while self._entry_sensor_active(path):
            self._drive_move(-self.feed_step_size)
            self.reactor.pause(self.reactor.monotonic() + self.sensor_delay)

        self._servo_disengage()
        self.path_states[path] = self.STATE_EMPTY
        gcmd.respond_info(
            "SA: === UNLOAD COMPLETE — path %d (%.1fmm retracted) ==="
            % (path, abs(enc.get_distance())))

    # ══════════════════════════════════════════════════════════════════════
    # GCode command registration
    # ══════════════════════════════════════════════════════════════════════

    def _register_commands(self):
        for name, fn, desc in [
            ('SA_HOME',               self._cmd_home,
             "Home selector motor to endstop, zero position"),
            ('SA_SELECT',             self._cmd_select,
             "Position selector to path N. TOOL=N"),
            ('SA_ENGAGE',             self._cmd_engage,
             "Engage drive servo (grip filament in selected path)"),
            ('SA_DISENGAGE',          self._cmd_disengage,
             "Disengage drive servo — path returns to neutral"),
            ('SA_LOAD',               self._cmd_load,
             "Full load sequence. TOOL=N"),
            ('SA_UNLOAD',             self._cmd_unload,
             "Full unload sequence. TOOL=N"),
            ('SA_STATUS',             self._cmd_status,
             "Print status for all paths including encoder and entry sensor"),
            ('SA_BUZZ_DRIVE',         self._cmd_buzz_drive,
             "Test drive motor — confirm motor moves"),
            ('SA_BUZZ_SELECTOR',      self._cmd_buzz_selector,
             "Test selector motor — confirm motor moves"),
            ('SA_CALIBRATE_ENCODER',  self._cmd_calibrate_encoder,
             "Calibrate encoder for path N. TOOL=N [DISTANCE=100]"),
            ('SA_CALIBRATE_SELECTOR', self._cmd_calibrate_selector,
             "Print current selector position for TOOL=N config entry"),
            ('SA_SET_SELECTOR_HOME',  self._cmd_set_selector_home,
             "Zero selector position at current location"),
            ('SA_SET_STATE',          self._cmd_set_state,
             "Override path state. TOOL=N STATE=loaded/empty/partial/unknown"),
        ]:
            self.gcode.register_command(name, fn, desc=desc)

    # ── Command handlers ──────────────────────────────────────────────────

    def _cmd_home(self, gcmd):
        gcmd.respond_info("SA: Homing selector...")
        self._selector_home()
        gcmd.respond_info("SA: Selector homed — position zeroed.")

    def _cmd_select(self, gcmd):
        path = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        self._select_path(path)
        gcmd.respond_info("SA: Path %d selected (%.1fmm from home)."
                          % (path, self._selector_positions[path]))

    def _cmd_engage(self, gcmd):
        self._servo_engage()
        gcmd.respond_info("SA: Drive engaged (%.1f°)." % self.servo_engaged_angle)

    def _cmd_disengage(self, gcmd):
        self._servo_disengage()
        gcmd.respond_info("SA: Drive disengaged — neutral (%.1f°)."
                          % self.servo_disengaged_angle)

    def _cmd_load(self, gcmd):
        path = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        self.do_load(gcmd, path)

    def _cmd_unload(self, gcmd):
        path = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        self.do_unload(gcmd, path)

    def _cmd_status(self, gcmd):
        # Header
        sel_str = ("path %d" % self.current_path
                   if self.current_path >= 0 else "none / unhomed")
        lines = [
            "╔══ Stealth Autoloader Status ════════════════════╗",
            "  Paths    : %d configured" % self.num_paths,
            "  Selector : %s" % sel_str,
            "  Drive    : %s" % ("ENGAGED" if self._servo_is_engaged else "neutral"),
            "╠══ Path  Entry       State      Encoder dist ════╣",
        ]

        for i in range(self.num_paths):
            entry      = self._entry_sensor_active(i)
            state      = self.path_states[i]
            enc_dist   = self._encoder_distance(i)
            mpp        = self._encoder_mm_per_pulse(i)

            # Filament presence indicator
            # entry sensor + loaded state → definitive; entry only → at roll end
            if state == self.STATE_LOADED:
                filament_str = "LOADED ✓"
            elif state == self.STATE_PARTIAL:
                filament_str = "partial"
            elif state == self.STATE_EMPTY:
                filament_str = "empty"
            elif entry:
                filament_str = "at entry"
            else:
                filament_str = "unknown"

            entry_str  = "FILAMENT" if entry else "empty   "
            enc_str    = ("%.2fmm" % enc_dist if enc_dist is not None else "n/a")
            marker     = " ◄" if i == self.current_path else ""
            lines.append("  [%d]   %-8s  %-10s  %-8s  %s%s"
                         % (i, entry_str, filament_str, enc_str,
                            ("mpp=%.4f" % mpp if mpp else ""), marker))

        lines.append("╚════════════════════════════════════════════════╝")
        gcmd.respond_info("\n".join(lines))

    def _cmd_buzz_drive(self, gcmd):
        dn = self._drv_name()
        gcmd.respond_info("SA: Buzzing drive motor (%s)..." % dn)
        for move in [5, -5, 5, -5]:
            self.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s ENABLE=1 MOVE=%d SPEED=10" % (dn, move))
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=0" % dn)
        gcmd.respond_info("SA: Drive buzz done. Did a motor move?")

    def _cmd_buzz_selector(self, gcmd):
        sn = self._sel_name()
        gcmd.respond_info("SA: Buzzing selector motor (%s)..." % sn)
        for move in [10, -10, 10, -10]:
            self.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s ENABLE=1 MOVE=%d SPEED=50" % (sn, move))
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=0" % sn)
        gcmd.respond_info("SA: Selector buzz done. Did a motor move?")

    def _cmd_calibrate_encoder(self, gcmd):
        path     = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        distance = gcmd.get_float('DISTANCE', 100.0, minval=10.0)
        enc      = self._encoder(path)
        dn       = self._drv_name()

        gcmd.respond_info(
            "SA: Calibrating encoder %d — commanding %.1fmm drive.\n"
            "Ensure filament is loaded past drive gear and selector is on path %d."
            % (path, distance, path))

        # Select path and engage
        self._select_path(path)
        self._servo_engage()

        enc.set_direction(forward=True)
        enc.reset_distance()

        self.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s ENABLE=1 MOVE=%.1f SPEED=%.1f"
            % (dn, distance, self.feed_speed * 0.5))
        self.gcode.run_script_from_command("M400")

        self._servo_disengage()

        enc_dist = enc.get_distance()
        if enc_dist < 1.0:
            gcmd.respond_info(
                "SA: ERROR — encoder %d measured < 1mm. "
                "Check encoder wiring and that filament is gripped." % path)
            return

        suggested = enc.mm_per_pulse * (distance / enc_dist)
        gcmd.respond_info(
            "SA: Encoder %d calibration result:\n"
            "  Stepper commanded : %.2fmm\n"
            "  Encoder measured  : %.2fmm\n"
            "  Current  mm_per_pulse : %.4f\n"
            "  Suggested mm_per_pulse: %.4f\n"
            "\nUpdate [sa_encoder %d].mm_per_pulse in hardware.cfg and restart."
            % (path, distance, enc_dist, enc.mm_per_pulse, suggested, path))

    def _cmd_calibrate_selector(self, gcmd):
        path = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        sn   = self._sel_name()
        gcmd.respond_info(
            "SA: Selector calibration for path %d.\n"
            "1. Jog selector to path %d position:\n"
            "     MANUAL_STEPPER STEPPER=%s ENABLE=1 MOVE=<mm> SPEED=30\n"
            "2. Run: SA_SET_SELECTOR_HOME  (if this is path 0, zeros here)\n"
            "   OR note the current position and update selector_position_%d\n"
            "   in [stealth_autoloader] in hardware.cfg."
            % (path, path, sn, path))

    def _cmd_set_selector_home(self, gcmd):
        sn = self._sel_name()
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)
        self.current_path = -1
        gcmd.respond_info("SA: Selector position zeroed at current location.")

    def _cmd_set_state(self, gcmd):
        path  = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        state = gcmd.get('STATE').lower().strip()
        valid = [self.STATE_UNKNOWN, self.STATE_EMPTY,
                 self.STATE_PARTIAL, self.STATE_LOADED]
        if state not in valid:
            gcmd.respond_info("SA: Invalid STATE '%s'. Valid: %s" % (state, valid))
            return
        self.path_states[path] = state
        gcmd.respond_info("SA: Path %d state → '%s'." % (path, state))

    # ══════════════════════════════════════════════════════════════════════
    # Klipper status — readable in macros as printer['stealth_autoloader']
    # ══════════════════════════════════════════════════════════════════════

    def get_status(self, eventtime):
        enc_distances   = []
        entry_filament  = []
        filament_loaded = []

        for i in range(self.num_paths):
            d = self._encoder_distance(i)
            enc_distances.append(round(d, 2) if d is not None else -1.0)

            has_entry = self._entry_sensor_active(i)
            entry_filament.append(has_entry)

            # Simple boolean: "is filament loaded to nozzle on this path?"
            filament_loaded.append(self.path_states[i] == self.STATE_LOADED)

        return {
            'num_paths'      : self.num_paths,
            'current_path'   : self.current_path,
            'servo_engaged'  : self._servo_is_engaged,
            'path_states'    : list(self.path_states),
            'encoder_dist'   : enc_distances,
            'entry_filament' : entry_filament,      # True = filament present at roll end
            'filament_loaded': filament_loaded,     # True = loaded all the way to nozzle
        }

    @staticmethod
    def load_config(config):
        return StealthAutoloader(config)

def load_config(config):
    return StealthAutoloader(config)
