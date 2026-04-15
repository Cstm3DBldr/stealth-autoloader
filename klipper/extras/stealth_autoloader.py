# stealth_autoloader.py - Stealth Autoloader main controller
#
# Single [stealth_autoloader] config section controls the entire system.
#
# Motion topology (ERCF V2 mechanical base, adapted for fixed multi-toolhead):
#   - Selector motor  : positions to the active filament path (like ERCF selector carriage)
#   - Drive motor     : single feed gear that moves filament once path is selected
#   - Engage servo    : clamps drive gear into the selected path ("driven")
#                       or releases it ("neutral") — filament flows freely when neutral
#   - Drive encoder   : single hall-effect encoder on the drive gear output shaft
#                       measures actual filament movement regardless of which path
#   - Entry sensors   : one per path, fixed position, detect filament at the roll end
#
# KEY DIFFERENCE from ERCF: filament never leaves the path during tool changes.
# The printer changes toolheads; this system only acts when a roll runs out
# or a manual load/unload is requested between prints.
#
# GCode commands (all registered by the Python backend):
#   SA_HOME                               Home selector motor to endstop
#   SA_SELECT TOOL=N                      Position selector to path N (servo stays neutral)
#   SA_ENGAGE                             Engage drive servo (drive gear grips filament)
#   SA_DISENGAGE                          Disengage drive servo (neutral — filament flows free)
#   SA_LOAD   TOOL=N                      Full load sequence for path N
#   SA_UNLOAD TOOL=N                      Full unload sequence for path N
#   SA_STATUS                             Print system state for all paths
#   SA_BUZZ_DRIVE                         Test drive motor (confirms motor wiring)
#   SA_BUZZ_SELECTOR                      Test selector motor (confirms motor wiring)
#   SA_CALIBRATE_ENCODER [DISTANCE=100]   Measure mm_per_pulse, print suggested value
#   SA_CALIBRATE_SELECTOR TOOL=N          Print current selector position to set in config
#   SA_SET_STATE TOOL=N STATE=<state>     Manually override path state
#   SA_SET_SELECTOR_HOME                  Zero selector position at current location

import logging

class StealthAutoloader:

    # ── Path states ──────────────────────────────────────────────────────
    STATE_UNKNOWN = 'unknown'   # state not confirmed
    STATE_EMPTY   = 'empty'     # no filament in path
    STATE_PARTIAL = 'partial'   # filament in tube, not loaded to nozzle
    STATE_LOADED  = 'loaded'    # filament loaded to nozzle tip

    def __init__(self, config):
        self.printer  = config.get_printer()
        self.gcode    = self.printer.lookup_object('gcode')
        self.reactor  = self.printer.get_reactor()

        # ── Hardware object names (resolved lazily at runtime) ────────────
        self.drive_stepper_name    = config.get('drive_stepper')
        self.selector_stepper_name = config.get('selector_stepper')
        self.servo_name            = config.get('servo')
        self.encoder_name          = config.get('encoder')

        # ── Servo positions ───────────────────────────────────────────────
        # engaged    = drive gear grips filament — motion is transferred
        # disengaged = drive gear releases — filament path is in neutral
        self.servo_engaged_angle    = config.getfloat('servo_engaged_angle',    30.0)
        self.servo_disengaged_angle = config.getfloat('servo_disengaged_angle', 160.0)

        # ── Path count ────────────────────────────────────────────────────
        self.num_paths = config.getint('num_paths', 6)
        if not 1 <= self.num_paths <= 32:
            raise config.error("num_paths must be between 1 and 32")

        # ── Per-path config ───────────────────────────────────────────────
        self._entry_sensor_names = []
        self._selector_positions = []
        self._extruder_names     = []

        for i in range(self.num_paths):
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
        self.current_path  = -1                               # -1 = not selected
        self._servo_is_engaged = False
        self.path_states   = [self.STATE_UNKNOWN] * self.num_paths

        # ── Register all gcode commands ───────────────────────────────────
        self._register_commands()

        self.printer.register_event_handler('klippy:ready', self._on_ready)
        logging.info("StealthAutoloader: initialized — %d paths configured", self.num_paths)

    # ══════════════════════════════════════════════════════════════════════
    # Klipper lifecycle
    # ══════════════════════════════════════════════════════════════════════

    def _on_ready(self):
        # Schedule servo disengage after Klipper finishes connecting all MCUs
        self.reactor.register_callback(self._init_hardware)

    def _init_hardware(self, eventtime):
        try:
            self._servo_disengage()
        except Exception as e:
            logging.warning("StealthAutoloader: could not init servo at startup: %s", e)

    # ══════════════════════════════════════════════════════════════════════
    # Hardware accessors (lazy lookup — resolved after klippy:ready)
    # ══════════════════════════════════════════════════════════════════════

    def _encoder(self):
        return self.printer.lookup_object(self.encoder_name)

    def _entry_sensor_active(self, path):
        """Return True if filament is present at the entry of the given path."""
        name = self._entry_sensor_names[path]
        if not name:
            return False
        try:
            return self.printer.lookup_object(name).get_status(
                self.reactor.monotonic())['filament_detected']
        except Exception:
            return False

    # ══════════════════════════════════════════════════════════════════════
    # Servo control
    # ══════════════════════════════════════════════════════════════════════

    def _servo_name_short(self):
        """Return just the instance name, e.g. 'servo sa_engage' → 'sa_engage'."""
        return self.servo_name.split()[-1]

    def _servo_engage(self):
        """Clamp drive gear into the selected filament path."""
        self.gcode.run_script_from_command(
            "SET_SERVO SERVO=%s ANGLE=%.1f" % (
                self._servo_name_short(), self.servo_engaged_angle))
        self.reactor.pause(self.reactor.monotonic() + self.servo_move_delay)
        self._servo_is_engaged = True

    def _servo_disengage(self):
        """Release drive gear — path returns to neutral."""
        self.gcode.run_script_from_command(
            "SET_SERVO SERVO=%s ANGLE=%.1f" % (
                self._servo_name_short(), self.servo_disengaged_angle))
        self.reactor.pause(self.reactor.monotonic() + self.servo_move_delay)
        self._servo_is_engaged = False

    # ══════════════════════════════════════════════════════════════════════
    # Selector motor
    # ══════════════════════════════════════════════════════════════════════

    def _selector_stepper_name(self):
        return self.selector_stepper_name.split()[-1]

    def _drive_stepper_name(self):
        return self.drive_stepper_name.split()[-1]

    def _selector_home(self):
        """Home selector to endstop and zero position."""
        sn = self._selector_stepper_name()
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % sn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)
        # Move negative (toward endstop) until triggered
        self.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s MOVE=-250 SPEED=%.1f STOP_ON_ENDSTOP=1"
            % (sn, self.selector_speed * 0.5))
        self.gcode.run_script_from_command("M400")
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)
        self.current_path = -1

    def _selector_move_to(self, position_mm):
        """Move selector to absolute mm position from home."""
        sn = self._selector_stepper_name()
        self.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s ENABLE=1 MOVE=%.3f SPEED=%.1f"
            % (sn, position_mm, self.selector_speed))
        self.gcode.run_script_from_command("M400")

    def _select_path(self, path):
        """Disengage servo then move selector to the target path position."""
        self._servo_disengage()
        self._selector_move_to(self._selector_positions[path])
        self.current_path = path

    # ══════════════════════════════════════════════════════════════════════
    # Drive motor + encoder helpers
    # ══════════════════════════════════════════════════════════════════════

    def _drive_move(self, distance_mm, speed=None):
        """Command the drive motor to move distance_mm (+ = toward toolhead)."""
        if speed is None:
            speed = self.feed_speed
        dn = self._drive_stepper_name()
        self.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s ENABLE=1 MOVE=%.3f SPEED=%.1f"
            % (dn, distance_mm, speed))
        self.gcode.run_script_from_command("M400")

    def _encoder_motion_threshold(self):
        """Minimum encoder distance that counts as 'filament is actually moving'."""
        try:
            return self._encoder().mm_per_pulse * 3.0
        except Exception:
            return 1.5

    def _check_slip(self, gcmd, encoder_dist, stepper_dist):
        """Warn if encoder and stepper commanded distance differ by > slip_tolerance %."""
        if stepper_dist < 50.0:
            return
        pct = abs(encoder_dist - stepper_dist) / stepper_dist * 100.0
        if pct > self.slip_tolerance:
            gcmd.respond_info(
                "SA WARNING: slip detected — stepper=%.1fmm encoder=%.1fmm (%.1f%%)"
                % (stepper_dist, encoder_dist, pct))

    # ══════════════════════════════════════════════════════════════════════
    # Load sequence
    # ══════════════════════════════════════════════════════════════════════

    def do_load(self, gcmd, path):
        enc = self._encoder()

        gcmd.respond_info("SA: === LOAD path %d ===" % path)

        # Pre-check: filament must be present at entry
        if not self._entry_sensor_active(path):
            gcmd.respond_info(
                "SA: ERROR — no filament detected at entry of path %d. "
                "Insert filament and retry." % path)
            return

        # Position selector, engage drive gear
        gcmd.respond_info("SA: Selecting path %d and engaging drive..." % path)
        self._select_path(path)
        self._servo_engage()

        # Prepare encoder for forward measurement
        enc.set_direction(forward=True)
        enc.reset_distance()

        # ── Phase 1: Engage — confirm filament is gripped ─────────────────
        gcmd.respond_info("SA: Phase 1 — engaging filament with drive gear...")
        driven = 0.0
        threshold = self._encoder_motion_threshold()

        while enc.get_distance() < threshold and driven < self.engage_max_distance:
            self._drive_move(self.feed_step_size)
            driven += self.feed_step_size
            self.reactor.pause(self.reactor.monotonic() + self.sensor_delay)

        if enc.get_distance() < threshold:
            self._servo_disengage()
            gcmd.respond_info(
                "SA: ERROR — no encoder motion after %.0fmm on path %d. "
                "Check filament is inserted past drive gear and encoder is wired."
                % (driven, path))
            return

        gcmd.respond_info("SA: Drive engaged — feeding %.0fmm to extruder..." % self.tube_length)

        # ── Phase 2: Feed filament through tube to extruder ───────────────
        driven_total = driven
        while enc.get_distance() < self.tube_length:
            self._drive_move(self.feed_step_size)
            driven_total += self.feed_step_size
            self.reactor.pause(self.reactor.monotonic() + self.sensor_delay)
            self._check_slip(gcmd, enc.get_distance(), driven_total)

        gcmd.respond_info(
            "SA: Filament at extruder — %.1fmm driven, %.1fmm encoder"
            % (driven_total, enc.get_distance()))

        # Release drive gear — extruder takes over from here
        self._servo_disengage()

        # ── Phase 3: Heat extruder and feed to nozzle ─────────────────────
        extruder = self._extruder_names[path]
        gcmd.respond_info("SA: Heating %s to %.0f°C..." % (extruder, self.load_temperature))
        self.gcode.run_script_from_command(
            "TEMPERATURE_WAIT SENSOR=%s MINIMUM=%.0f" % (extruder, self.load_temperature))

        gcmd.respond_info("SA: Feeding through extruder gear to nozzle tip (%.1fmm)..."
                          % self.nozzle_distance)
        self.gcode.run_script_from_command("M83")
        self.gcode.run_script_from_command("G1 E%.2f F300" % self.nozzle_distance)
        self.gcode.run_script_from_command("M400")

        # ── Phase 4: Purge ────────────────────────────────────────────────
        gcmd.respond_info("SA: Purging nozzle (%.1fmm)..." % self.purge_length)
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
        enc = self._encoder()

        gcmd.respond_info("SA: === UNLOAD path %d ===" % path)

        # Retract from nozzle tip back through extruder gear
        gcmd.respond_info("SA: Retracting from nozzle (%.1fmm)..."
                          % (self.nozzle_distance + self.purge_length))
        self.gcode.run_script_from_command("M83")
        self.gcode.run_script_from_command(
            "G1 E-%.2f F300" % (self.nozzle_distance + self.purge_length))
        self.gcode.run_script_from_command("M400")

        # Select path, engage drive in reverse direction
        gcmd.respond_info("SA: Selecting path %d and retracting to entry..." % path)
        self._select_path(path)
        self._servo_engage()

        enc.set_direction(forward=False)
        enc.reset_distance()

        # Retract until entry sensor no longer detects filament
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
        cmds = [
            ('SA_HOME',
             self._cmd_home,
             "Home selector motor to endstop, zero position"),
            ('SA_SELECT',
             self._cmd_select,
             "Position selector to path. TOOL=N"),
            ('SA_ENGAGE',
             self._cmd_engage,
             "Engage drive servo (grip filament in selected path)"),
            ('SA_DISENGAGE',
             self._cmd_disengage,
             "Disengage drive servo (neutral — filament flows freely)"),
            ('SA_LOAD',
             self._cmd_load,
             "Full load sequence. TOOL=N"),
            ('SA_UNLOAD',
             self._cmd_unload,
             "Full unload sequence. TOOL=N"),
            ('SA_STATUS',
             self._cmd_status,
             "Print autoloader state for all paths"),
            ('SA_BUZZ_DRIVE',
             self._cmd_buzz_drive,
             "Test drive motor — confirm which motor moves"),
            ('SA_BUZZ_SELECTOR',
             self._cmd_buzz_selector,
             "Test selector motor — confirm which motor moves"),
            ('SA_CALIBRATE_ENCODER',
             self._cmd_calibrate_encoder,
             "Calibrate encoder mm/pulse. DISTANCE=100"),
            ('SA_CALIBRATE_SELECTOR',
             self._cmd_calibrate_selector,
             "Report current selector position for TOOL=N config entry"),
            ('SA_SET_SELECTOR_HOME',
             self._cmd_set_selector_home,
             "Zero selector position at its current location"),
            ('SA_SET_STATE',
             self._cmd_set_state,
             "Override path state. TOOL=N STATE=loaded/empty/partial/unknown"),
        ]
        for name, fn, desc in cmds:
            self.gcode.register_command(name, fn, desc=desc)

    # ── Command handlers ──────────────────────────────────────────────────

    def _cmd_home(self, gcmd):
        gcmd.respond_info("SA: Homing selector...")
        self._selector_home()
        gcmd.respond_info("SA: Selector homed — position zeroed.")

    def _cmd_select(self, gcmd):
        path = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        gcmd.respond_info("SA: Moving selector to path %d (%.1fmm)..."
                          % (path, self._selector_positions[path]))
        self._select_path(path)
        gcmd.respond_info("SA: Path %d selected." % path)

    def _cmd_engage(self, gcmd):
        self._servo_engage()
        gcmd.respond_info("SA: Drive engaged (servo at %.1f°)." % self.servo_engaged_angle)

    def _cmd_disengage(self, gcmd):
        self._servo_disengage()
        gcmd.respond_info("SA: Drive disengaged — neutral (servo at %.1f°)."
                          % self.servo_disengaged_angle)

    def _cmd_load(self, gcmd):
        path = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        self.do_load(gcmd, path)

    def _cmd_unload(self, gcmd):
        path = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        self.do_unload(gcmd, path)

    def _cmd_status(self, gcmd):
        try:
            enc = self._encoder()
            enc_info = "%.2fmm (%.4f mm/pulse)" % (enc.get_distance(), enc.mm_per_pulse)
        except Exception:
            enc_info = "unavailable"

        lines = [
            "=== Stealth Autoloader Status ===",
            "Paths : %d configured" % self.num_paths,
            "Selector : path %s" % (str(self.current_path) if self.current_path >= 0 else "none / unhomed"),
            "Drive    : %s" % ("ENGAGED" if self._servo_is_engaged else "neutral"),
            "Encoder  : %s" % enc_info,
            "",
            "  Path  State       Entry",
        ]
        for i in range(self.num_paths):
            entry   = "FILAMENT" if self._entry_sensor_active(i) else "empty"
            state   = self.path_states[i]
            selpos  = "%.1fmm" % self._selector_positions[i]
            marker  = " <-- selected" if i == self.current_path else ""
            lines.append("  [%d]   %-10s  %-8s  sel=%s%s"
                         % (i, state, entry, selpos, marker))
        gcmd.respond_info("\n".join(lines))

    def _cmd_buzz_drive(self, gcmd):
        dn = self._drive_stepper_name()
        gcmd.respond_info("SA: Buzzing drive motor (%s)..." % dn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % dn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s MOVE=5  SPEED=10" % dn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s MOVE=-5 SPEED=10" % dn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s MOVE=5  SPEED=10" % dn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s MOVE=-5 SPEED=10" % dn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=0" % dn)
        gcmd.respond_info("SA: Drive buzz complete. Did a motor move?")

    def _cmd_buzz_selector(self, gcmd):
        sn = self._selector_stepper_name()
        gcmd.respond_info("SA: Buzzing selector motor (%s)..." % sn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % sn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s MOVE=10  SPEED=50" % sn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s MOVE=-10 SPEED=50" % sn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s MOVE=10  SPEED=50" % sn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s MOVE=-10 SPEED=50" % sn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=0" % sn)
        gcmd.respond_info("SA: Selector buzz complete. Did a motor move?")

    def _cmd_calibrate_encoder(self, gcmd):
        distance = gcmd.get_float('DISTANCE', 100.0, minval=10.0)
        enc = self._encoder()
        dn  = self._drive_stepper_name()

        gcmd.respond_info(
            "SA: Encoder calibration — feeding %.1fmm.\n"
            "Ensure filament is loaded past drive gear." % distance)

        enc.set_direction(forward=True)
        enc.reset_distance()

        self._servo_engage()
        self.gcode.run_script_from_command(
            "MANUAL_STEPPER STEPPER=%s ENABLE=1 MOVE=%.1f SPEED=%.1f"
            % (dn, distance, self.feed_speed * 0.5))
        self.gcode.run_script_from_command("M400")
        self._servo_disengage()

        enc_dist = enc.get_distance()
        if enc_dist < 1.0:
            gcmd.respond_info(
                "SA: ERROR — encoder measured < 1mm. Check encoder wiring and that "
                "filament is engaged with the drive gear.")
            return

        # Suggested mm_per_pulse: scale current value so encoder would read `distance`
        suggested = enc.mm_per_pulse * (distance / enc_dist)
        gcmd.respond_info(
            "SA: Calibration result:\n"
            "  Stepper commanded : %.2fmm\n"
            "  Encoder measured  : %.2fmm\n"
            "  Current mm_per_pulse  : %.4f\n"
            "  Suggested mm_per_pulse: %.4f\n"
            "\nUpdate [sa_encoder].mm_per_pulse in hardware.cfg and restart Klipper."
            % (distance, enc_dist, enc.mm_per_pulse, suggested))

    def _cmd_calibrate_selector(self, gcmd):
        path = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        sn   = self._selector_stepper_name()
        gcmd.respond_info(
            "SA: Selector calibration for path %d.\n"
            "1. Move selector to path %d manually:\n"
            "     MANUAL_STEPPER STEPPER=%s ENABLE=1 MOVE=<mm> SPEED=30\n"
            "2. Then run:  SA_CALIBRATE_SELECTOR_SAVE TOOL=%d\n"
            "   (reads current position and prints the value to set in hardware.cfg)"
            % (path, path, sn, path))

    def _cmd_set_selector_home(self, gcmd):
        sn = self._selector_stepper_name()
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
        gcmd.respond_info("SA: Path %d state set to '%s'." % (path, state))

    # ══════════════════════════════════════════════════════════════════════
    # Klipper status — readable in macros as printer['stealth_autoloader']
    # ══════════════════════════════════════════════════════════════════════

    def get_status(self, eventtime):
        return {
            'num_paths'    : self.num_paths,
            'current_path' : self.current_path,
            'servo_engaged': self._servo_is_engaged,
            'path_states'  : list(self.path_states),
        }

    @staticmethod
    def load_config(config):
        return StealthAutoloader(config)

def load_config(config):
    return StealthAutoloader(config)
