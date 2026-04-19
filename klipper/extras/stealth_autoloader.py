# stealth_autoloader.py — Stealth Autoloader main controller
#
# Single [stealth_autoloader] Klipper config section that instantiates and
# wires together all subsystems:
#   sa_motion.py        — motion primitives (servo, selector, drive)
#   sa_sequences.py     — load / unload sequences
#   sa_calibration.py   — all calibration routines
#
# Motion topology:
#   Selector motor  : moves drive gear carriage to align with the active path
#   Drive motor     : single gear that moves filament once path is selected
#   Engage servo    : clamps drive gear into active path (driven) or releases (neutral)
#   Per-path encoder: one fixed encoder per path — never moves, always counting
#   Entry sensor    : one fixed sensor per path at the roll end
#   Toolhead sensor : detects filament at the nozzle end of each Bowden tube
#   Extruder sensor : detects filament arriving at extruder gears per toolhead
#
# Path states
# ───────────
#   unknown  — not confirmed (after boot or explicit reset)
#   empty    — no filament in path
#   partial  — filament in tube but not loaded to nozzle
#   loaded   — filament loaded all the way to nozzle tip
#
# GCode commands registered here:
#   SA_HOME
#   SA_SELECT     TOOL=N
#   SA_ENGAGE
#   SA_DISENGAGE
#   SA_LOAD       TOOL=N
#   SA_UNLOAD     TOOL=N
#   SA_STATUS
#   SA_BUZZ_DRIVE      [DISTANCE SPEED REPS]
#   SA_BUZZ_SELECTOR   [DISTANCE SPEED REPS]
#   SA_CALIBRATE_SELECTOR          (automated, no TOOL param)
#   SA_CALIBRATE_DRIVE
#   SA_CALIBRATE_ENCODER  TOOL=N
#   SA_CALIBRATE_BOWDEN   TOOL=N
#   SA_ENCODER_QUERY   [TOOL RESET]
#   SA_ENCODER_WATCH   [TOOL DURATION INTERVAL]
#   SA_SET_STATE  TOOL=N STATE=x
#   SA_RESPOND    VALUE=x
#   SA_TEST_SENSORLESS

import sys, os as _os
_extras_dir = _os.path.dirname(_os.path.abspath(__file__))
if _extras_dir not in sys.path:
    sys.path.insert(0, _extras_dir)

import logging
from sa_motion      import SAMotion
from sa_sequences   import SASequences
from sa_calibration import SACalibration

# ══════════════════════════════════════════════════════════════════════════════
# StealthAutoloader
# ══════════════════════════════════════════════════════════════════════════════

class StealthAutoloader:

    # ── Path states ───────────────────────────────────────────────────────────
    STATE_UNKNOWN = 'unknown'
    STATE_EMPTY   = 'empty'
    STATE_PARTIAL = 'partial'
    STATE_LOADED  = 'loaded'

    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode   = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()

        # ── Shared motion hardware names ──────────────────────────────────────
        self.drive_stepper_name    = config.get('drive_stepper')
        self.selector_stepper_name = config.get('selector_stepper')
        self.servo_name            = config.get('servo')

        # ── Servo angles ──────────────────────────────────────────────────────
        self.servo_engaged_angle    = config.getfloat('servo_engaged_angle',    30.0)
        self.servo_disengaged_angle = config.getfloat('servo_disengaged_angle', 160.0)

        # ── Path count ────────────────────────────────────────────────────────
        self.num_paths = config.getint('num_paths', 6)
        if not 1 <= self.num_paths <= 32:
            raise config.error("num_paths must be between 1 and 32")

        # ── Per-path config ───────────────────────────────────────────────────
        self._encoder_names         = []
        self._entry_sensor_names    = []
        self._toolhead_sensor_names = []
        self._extruder_sensor_names = []
        self._selector_positions    = []
        self._extruder_names        = []
        self._bowden_lengths        = []

        for i in range(self.num_paths):
            self._encoder_names.append(
                config.get('encoder_%d' % i, 'sa_encoder %d' % i))

            self._entry_sensor_names.append(
                config.get('entry_sensor_%d' % i, None))

            self._toolhead_sensor_names.append(
                config.get('toolhead_sensor_%d' % i, None))

            self._extruder_sensor_names.append(
                config.get('extruder_sensor_%d' % i, None))

            self._selector_positions.append(
                config.getfloat('selector_position_%d' % i, float(i) * 21.0))

            default_ext = 'extruder' if i == 0 else 'extruder%d' % i
            self._extruder_names.append(
                config.get('extruder_%d' % i, default_ext))

            self._bowden_lengths.append(
                config.getfloat('bowden_length_%d' % i, 800.0))

        # ── Motion parameters ─────────────────────────────────────────────────
        self.tube_length             = config.getfloat('tube_length',             800.0)
        self.nozzle_distance         = config.getfloat('nozzle_distance',          50.0)
        self.purge_length            = config.getfloat('purge_length',             30.0)
        self.sensor_retry_dist       = config.getfloat('sensor_retry_dist',        20.0)
        self.load_temperature        = config.getfloat('load_temperature',        200.0)
        self.engage_max_distance     = config.getfloat('engage_max_distance',      60.0)
        self.slip_tolerance          = config.getfloat('slip_tolerance',           15.0)
        self.feed_speed              = config.getfloat('feed_speed',               50.0)
        self.feed_step_size          = config.getfloat('feed_step_size',           10.0)
        self.selector_speed          = config.getfloat('selector_speed',          200.0)
        self.sensor_delay            = config.getfloat('sensor_polling_delay',      0.2)
        self.servo_move_delay        = config.getfloat('servo_move_delay',          0.3)
        self.stepper_timeout         = config.getfloat('stepper_timeout',         120.0)
        self.selector_max_travel     = config.getfloat('selector_max_travel',     200.0)
        self.selector_homing_speed   = config.getfloat('selector_homing_speed',    50.0)
        self.selector_homing_backoff = config.getfloat('selector_homing_backoff',   5.0)
        # Selector position calibration geometry
        self.selector_end_offset  = config.getfloat('selector_end_offset',  0.0)
        self.path_width           = config.getfloat('path_width',            0.0)
        self.selector_cal_current = config.getfloat('selector_cal_current', 0.4)

        # ── Load positioning ──────────────────────────────────────────────────
        self.load_park_x       = config.getfloat('load_park_x',        175.0)
        self.load_park_y       = config.getfloat('load_park_y',          0.0)
        self.load_park_z       = config.getfloat('load_park_z',        100.0)
        self.load_print_park_x = config.getfloat('load_print_park_x',   0.0)
        self.load_print_park_y = config.getfloat('load_print_park_y', 200.0)
        self.fill_nozzle_length  = config.getfloat('fill_nozzle_length',   70.0)
        self.max_volumetric_flow = config.getfloat('max_volumetric_flow',   8.0)
        self.cooling_pad_enabled  = config.getboolean('cooling_pad_enabled',  True)
        self.clean_nozzle_enabled = config.getboolean('clean_nozzle_enabled', True)

        # ── Tip forming (unload) ──────────────────────────────────────────────
        self.tip_form_temp             = config.getfloat('tip_form_temp',             185.0)
        self.tip_form_push_length      = config.getfloat('tip_form_push_length',       8.0)
        self.tip_form_push_speed       = config.getfloat('tip_form_push_speed',       20.0)
        self.tip_form_retract_speed    = config.getfloat('tip_form_retract_speed',    40.0)
        self.tip_form_heatbreak_dist   = config.getfloat('tip_form_heatbreak_dist',   30.0)
        self.tip_form_heatbreak_speed  = config.getfloat('tip_form_heatbreak_speed',   5.0)
        self.tip_form_slow_speed       = config.getfloat('tip_form_slow_speed',       12.0)
        self.tip_form_dwell            = config.getfloat('tip_form_dwell',             0.0)
        self.nozzle_to_sensor_dist     = config.getfloat('nozzle_to_sensor_dist',    120.0)
        self.min_unload_temp        = config.getfloat('min_unload_temp',        170.0)

        # ── Sensor verify / park ──────────────────────────────────────────────
        self.encoder_to_gear_distance = config.getfloat('encoder_to_gear_distance', 30.0)
        self.wiggle_distance          = config.getfloat('wiggle_distance',           10.0)

        # ── Runtime state ─────────────────────────────────────────────────────
        self.current_path      = -1
        self._selector_homed   = False
        self._servo_is_engaged = False
        self.path_states       = [self.STATE_UNKNOWN] * self.num_paths

        # Calibration state machine (SA_RESPOND dispatches here; cleared on restart)
        self._cal_state = None   # str key e.g. 'sel_confirm', or None
        self._cal_data  = {}     # data bag passed between calibration phases

        # ── Subsystems ────────────────────────────────────────────────────────
        self.motion      = SAMotion(self)
        self.sequences   = SASequences(self)
        self.calibration = SACalibration(self)

        # ── Startup ───────────────────────────────────────────────────────────
        self._register_commands()
        self.printer.register_event_handler('klippy:ready', self._on_ready)
        logging.info("StealthAutoloader: initialized — %d paths", self.num_paths)

    # ══════════════════════════════════════════════════════════════════════════
    # Klipper lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def _on_ready(self):
        self.reactor.register_callback(self._init_hardware)

    def _init_hardware(self, eventtime):
        self._load_variable_overrides()
        self.motion.on_ready()

    def _load_variable_overrides(self):
        """Load calibrated values from save_variables, overriding hardware.cfg defaults."""
        sv = self.printer.lookup_object('save_variables', None)
        if sv is None:
            return
        allvars = sv.allVariables
        for i in range(self.num_paths):
            key = 'selector_position_%d' % i
            if key in allvars:
                try:
                    self._selector_positions[i] = float(allvars[key])
                except (ValueError, TypeError):
                    pass
            key = 'bowden_length_%d' % i
            if key in allvars:
                try:
                    self._bowden_lengths[i] = float(allvars[key])
                except (ValueError, TypeError):
                    pass
        if 'drive_rotation_distance' in allvars:
            try:
                self._apply_drive_rotation_distance(float(allvars['drive_rotation_distance']))
            except (ValueError, TypeError):
                pass
        logging.info("StealthAutoloader: save_variables overrides applied")

    def _apply_drive_rotation_distance(self, new_rd):
        """Apply a calibrated rotation_distance to the drive stepper in memory."""
        try:
            drv_obj  = self.printer.lookup_object(self.drive_stepper_name)
            steppers = drv_obj.get_steppers()
            if steppers:
                s      = steppers[0]
                old_rd = s.get_rotation_distance()[0]
                old_sd = s.get_step_dist()
                new_sd = old_sd * (new_rd / old_rd)
                s.set_step_dist(new_sd)
                logging.info(
                    "StealthAutoloader: drive rotation_distance=%.4f applied "
                    "(step_dist=%.6f)", new_rd, new_sd)
        except Exception as e:
            logging.warning("StealthAutoloader: failed to apply drive_rd: %s", e)

    # ══════════════════════════════════════════════════════════════════════════
    # Hardware name helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _drv_name(self):
        """Short name for drive stepper (last word of drive_stepper_name)."""
        return self.drive_stepper_name.split()[-1]

    def _sel_name(self):
        """Short name for selector stepper (last word of selector_stepper_name)."""
        return self.selector_stepper_name.split()[-1]

    def _servo_short_name(self):
        """Short name for servo (last word of servo_name)."""
        return self.servo_name.split()[-1]

    # ══════════════════════════════════════════════════════════════════════════
    # Hardware sensor accessors
    # ══════════════════════════════════════════════════════════════════════════

    def _encoder(self, path):
        """Return the sa_encoder object for *path*."""
        return self.printer.lookup_object(self._encoder_names[path])

    def _entry_sensor_active(self, path):
        """True if filament is detected at the entry of *path*."""
        name = self._entry_sensor_names[path]
        if not name:
            return False
        try:
            return bool(self.printer.lookup_object(name).get_status(
                self.reactor.monotonic())['filament_detected'])
        except Exception:
            return False

    def _toolhead_sensor_active(self, path):
        """True if filament is detected at the toolhead (nozzle end) of *path*."""
        name = self._toolhead_sensor_names[path]
        if not name:
            return False
        try:
            return bool(self.printer.lookup_object(name).get_status(
                self.reactor.monotonic())['filament_detected'])
        except Exception:
            return False

    def _extruder_sensor_active(self, path):
        """True if filament has arrived at the extruder gears for *path*."""
        name = self._extruder_sensor_names[path]
        if not name:
            return False
        try:
            return bool(self.printer.lookup_object(name).get_status(
                self.reactor.monotonic())['filament_detected'])
        except Exception:
            return False

    def _encoder_distance(self, path):
        """Current encoder distance for *path*, or None on error."""
        try:
            return self._encoder(path).get_distance()
        except Exception:
            return None

    def _encoder_mm_per_pulse(self, path):
        """mm_per_pulse for encoder *path*, or None on error."""
        try:
            return self._encoder(path).mm_per_pulse
        except Exception:
            return None

    # ══════════════════════════════════════════════════════════════════════════
    # GCode command registration
    # ══════════════════════════════════════════════════════════════════════════

    def _register_commands(self):
        cmds = [
            ('SA_HOME',
             self._cmd_home,
             "Home selector to endstop (double-touch)"),
            ('SA_SELECT',
             self._cmd_select,
             "Position selector to path N (no servo change). TOOL=N"),
            ('SA_ENGAGE',
             self._cmd_engage,
             "Engage drive servo — grip filament in selected path"),
            ('SA_DISENGAGE',
             self._cmd_disengage,
             "Disengage drive servo — path returns to neutral"),
            ('SA_LOAD',
             self._cmd_load,
             "Full load sequence. TOOL=N"),
            ('SA_UNLOAD',
             self._cmd_unload,
             "Full unload sequence. TOOL=N"),
            ('SA_STATUS',
             self._cmd_status,
             "Print status for all paths including encoders and sensors"),
            ('SA_BUZZ_DRIVE',
             self._cmd_buzz_drive,
             "Test drive motor. [DISTANCE=5] [SPEED=10] [REPS=3]"),
            ('SA_BUZZ_SELECTOR',
             self._cmd_buzz_selector,
             "Test selector motor. [DISTANCE=10] [SPEED=50] [REPS=3]"),
            ('SA_CALIBRATE_SELECTOR',
             self._cmd_calibrate_selector,
             "Automated selector position calibration (interactive)"),
            ('SA_CALIBRATE_DRIVE',
             self._cmd_calibrate_drive,
             "Interactive drive motor rotation_distance calibration"),
            ('SA_CALIBRATE_ENCODER',
             self._cmd_calibrate_encoder,
             "Interactive encoder mm_per_pulse calibration. TOOL=N"),
            ('SA_CALIBRATE_ENCODER_SPEED',
             self._cmd_calibrate_encoder_speed,
             "Find max encoder-accurate feed speed. TOOL=N"),
            ('SA_CALIBRATE_BOWDEN',
             self._cmd_calibrate_bowden,
             "Guided Bowden tube length calibration. TOOL=N"),
            ('SA_ENCODER_QUERY',
             self._cmd_encoder_query,
             "Snapshot all encoder distances. [TOOL=N] [RESET=1]"),
            ('SA_ENCODER_WATCH',
             self._cmd_encoder_watch,
             "Live encoder delta stream. [TOOL=N] [DURATION=30] [INTERVAL=0.5]"),
            ('SA_SET_STATE',
             self._cmd_set_state,
             "Override path state. TOOL=N STATE=loaded/empty/partial/unknown"),
            ('SA_RESPOND',
             self._cmd_respond,
             "Send a value back to a waiting calibration routine. VALUE=x"),
            ('SA_PARK_FILAMENT',
             self._cmd_park_filament,
             "Park filament at encoder for consistent load start. TOOL=N"),
            ('SA_FILAMENT_INSERT',
             self._cmd_filament_insert,
             "Auto-called by entry sensor insert_gcode. TOOL=N"),
        ]
        for name, fn, desc in cmds:
            self.gcode.register_command(name, fn, desc=desc)

    # ══════════════════════════════════════════════════════════════════════════
    # Command handlers — motion
    # ══════════════════════════════════════════════════════════════════════════

    def _cmd_home(self, gcmd):
        gcmd.respond_info("SA: Homing selector...")
        self.motion.selector_home()
        gcmd.respond_info("SA: Selector homed — position 0.0mm.")

    def _cmd_select(self, gcmd):
        path = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        self.motion.servo_disengage()
        self.motion.selector_move_to(self._selector_positions[path])
        self.current_path = path
        gcmd.respond_info(
            "SA: Path %d selected (%.3fmm from home)."
            % (path, self._selector_positions[path]))

    def _cmd_engage(self, gcmd):
        self.motion.servo_engage()
        gcmd.respond_info("SA: Drive engaged (%.1f°)." % self.servo_engaged_angle)

    def _cmd_disengage(self, gcmd):
        self.motion.servo_disengage()
        gcmd.respond_info(
            "SA: Drive disengaged — neutral (%.1f°)." % self.servo_disengaged_angle)

    def _cmd_load(self, gcmd):
        path = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        self.sequences.do_load(gcmd, path)

    def _cmd_unload(self, gcmd):
        path = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        self.sequences.do_unload(gcmd, path)

    # ══════════════════════════════════════════════════════════════════════════
    # Command handlers — status and diagnostics
    # ══════════════════════════════════════════════════════════════════════════

    def _cmd_status(self, gcmd):
        sel_str = ("path %d" % self.current_path
                   if self.current_path >= 0 else "none / unhomed")
        lines = [
            "╔══ Stealth Autoloader Status ══════════════════════════════╗",
            "  Paths    : %d configured" % self.num_paths,
            "  Selector : %s" % sel_str,
            "  Drive    : %s" % ("ENGAGED" if self._servo_is_engaged else "neutral"),
            "╠══ [N] Entry   TH    Ext  State      Encoder  mm/pulse ═══╣",
        ]
        for i in range(self.num_paths):
            entry   = self._entry_sensor_active(i)
            toolhd  = self._toolhead_sensor_active(i)
            extsens = self._extruder_sensor_active(i)
            state   = self.path_states[i]
            dist    = self._encoder_distance(i)
            mpp     = self._encoder_mm_per_pulse(i)

            if state == self.STATE_LOADED:
                state_str = "loaded   "
            elif state == self.STATE_PARTIAL:
                state_str = "partial  "
            elif state == self.STATE_EMPTY:
                state_str = "empty    "
            else:
                state_str = "unknown  "

            entry_str = "FIL" if entry   else "---"
            th_str    = "FIL" if toolhd  else "---"
            ext_str   = "FIL" if extsens else "---"
            dist_str  = ("%.2fmm" % dist) if dist is not None else "n/a   "
            mpp_str   = ("%.4f"   % mpp)  if mpp  is not None else "n/a"
            marker    = " <" if i == self.current_path else ""

            lines.append(
                "  [%d] %-5s  %-5s %-5s %-10s %-8s %s%s"
                % (i, entry_str, th_str, ext_str, state_str, dist_str, mpp_str, marker))

        lines.append("╚══════════════════════════════════════════════════════════╝")
        gcmd.respond_info("\n".join(lines))

    def _cmd_encoder_query(self, gcmd):
        tool  = gcmd.get_int('TOOL', -1)
        reset = gcmd.get_int('RESET', 0)
        paths = [tool] if tool >= 0 else list(range(self.num_paths))

        if reset:
            for i in paths:
                try:
                    self._encoder(i).reset_distance()
                except Exception:
                    pass

        lines = [
            "SA encoder snapshot%s%s:" % (
                (" (path %d)" % tool) if tool >= 0 else " (all paths)",
                " — counters zeroed" if reset else ""),
            "  Path  Distance    mm/pulse  Entry   TH      Extruder",
        ]
        for i in paths:
            dist    = self._encoder_distance(i)
            mpp     = self._encoder_mm_per_pulse(i)
            entry   = "FILAMENT" if self._entry_sensor_active(i)   else "empty"
            th      = "FILAMENT" if self._toolhead_sensor_active(i) else "empty"
            ext     = "FILAMENT" if self._extruder_sensor_active(i) else "empty"
            dist_s  = ("%.3fmm" % dist) if dist is not None else "n/a"
            mpp_s   = ("%.4f"   % mpp)  if mpp  is not None else "n/a"
            marker  = "  <- active" if i == self.current_path else ""
            lines.append(
                "  [%d]   %-10s  %-8s  %-8s  %-8s  %s%s"
                % (i, dist_s, mpp_s, entry, th, ext, marker))
        gcmd.respond_info("\n".join(lines))

    def _cmd_encoder_watch(self, gcmd):
        tool     = gcmd.get_int(  'TOOL',      -1)
        duration = gcmd.get_float('DURATION',  30.0, minval=1.0,  maxval=300.0)
        interval = gcmd.get_float('INTERVAL',   0.5, minval=0.05, maxval=10.0)

        paths = list(range(self.num_paths))

        # Capture baselines
        prev = []
        for i in paths:
            d = self._encoder_distance(i)
            prev.append(d if d is not None else 0.0)

        header = "  t(s)  " + "  ".join("[%d]     " % i for i in paths)
        gcmd.respond_info(
            "SA encoder watch — %.0fs, every %.2fs. Move filament to test.\n%s"
            % (duration, interval, header))

        end_time = self.reactor.monotonic() + duration
        elapsed  = 0.0

        while self.reactor.monotonic() < end_time:
            self.reactor.pause(self.reactor.monotonic() + interval)
            elapsed += interval
            cur  = []
            for i in paths:
                d = self._encoder_distance(i)
                cur.append(d if d is not None else 0.0)
            cols = []
            for i in paths:
                delta  = cur[i] - prev[i]
                moving = abs(delta) > 0.01
                mark   = "*" if moving else " "
                cols.append("%s[%d]%+.3f" % (mark, i, delta))
            prev = cur
            gcmd.respond_info("%6.1f  %s" % (elapsed, "  ".join(cols)))

        totals = []
        for i in paths:
            d = self._encoder_distance(i)
            totals.append("[%d]: %.3fmm" % (i, d if d is not None else 0.0))
        gcmd.respond_info(
            "SA watch complete. Encoder totals:\n  " + "  ".join(totals))

    # ══════════════════════════════════════════════════════════════════════════
    # Command handlers — motor buzz tests
    # ══════════════════════════════════════════════════════════════════════════

    def _buzz_stepper(self, gcmd, stepper_short_name, distance, speed, reps):
        sn = stepper_short_name
        gcmd.respond_info(
            "SA: Buzzing %s — +/-%.0fmm x %d reps @ %.0fmm/s"
            % (sn, distance, reps, speed))
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % sn)
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s SET_POSITION=0" % sn)
        for _ in range(reps):
            self.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s MOVE=%.1f SPEED=%.1f" % (sn,  distance, speed))
            self.gcode.run_script_from_command("M400")
            self.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s MOVE=%.1f SPEED=%.1f" % (sn, -distance, speed))
            self.gcode.run_script_from_command("M400")
        self.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=0" % sn)
        gcmd.respond_info("SA: Buzz complete — did the motor move?")

    def _cmd_buzz_drive(self, gcmd):
        dist  = gcmd.get_float('DISTANCE',  5.0, minval=1.0, maxval=50.0)
        speed = gcmd.get_float('SPEED',    10.0, minval=1.0, maxval=100.0)
        reps  = gcmd.get_int(  'REPS',        3, minval=1,   maxval=10)
        self._buzz_stepper(gcmd, self._drv_name(), dist, speed, reps)

    def _cmd_buzz_selector(self, gcmd):
        dist  = gcmd.get_float('DISTANCE', 10.0, minval=1.0, maxval=100.0)
        speed = gcmd.get_float('SPEED',    50.0, minval=1.0, maxval=300.0)
        reps  = gcmd.get_int(  'REPS',        3, minval=1,   maxval=10)
        self._buzz_stepper(gcmd, self._sel_name(), dist, speed, reps)

    # ══════════════════════════════════════════════════════════════════════════
    # Command handlers — calibration (delegate to SACalibration)
    # ══════════════════════════════════════════════════════════════════════════

    def _cmd_calibrate_selector(self, gcmd):
        self.calibration.calibrate_selector_auto(gcmd)

    def _cmd_calibrate_drive(self, gcmd):
        self.calibration.calibrate_drive(gcmd)

    def _cmd_calibrate_encoder(self, gcmd):
        self.calibration.calibrate_encoder(gcmd)

    def _cmd_calibrate_encoder_speed(self, gcmd):
        self.calibration.calibrate_encoder_speed(gcmd)

    def _cmd_calibrate_bowden(self, gcmd):
        self.calibration.calibrate_bowden(gcmd)

    # ══════════════════════════════════════════════════════════════════════════
    # Command handlers — state management
    # ══════════════════════════════════════════════════════════════════════════

    def _cmd_set_state(self, gcmd):
        path  = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        state = gcmd.get('STATE').lower().strip()
        valid = [self.STATE_UNKNOWN, self.STATE_EMPTY,
                 self.STATE_PARTIAL, self.STATE_LOADED]
        if state not in valid:
            gcmd.respond_info(
                "SA: Invalid STATE '%s'. Valid values: %s" % (state, ', '.join(valid)))
            return
        self.path_states[path] = state
        gcmd.respond_info("SA: Path %d state set to '%s'." % (path, state))

    def _cmd_respond(self, gcmd):
        """SA_RESPOND VALUE=x — advance the active calibration to its next phase."""
        value = gcmd.get('VALUE').strip()
        if self._cal_state is not None:
            gcmd.respond_info(
                "SA: Responding '%s' (state=%s)" % (value, self._cal_state))
        self.calibration.respond(gcmd, value)

    def _cmd_park_filament(self, gcmd):
        path = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        self.sequences.park_filament(gcmd, path)

    def _cmd_filament_insert(self, gcmd):
        """Called by entry sensor insert_gcode. Parks filament if not printing."""
        path = gcmd.get_int('TOOL', minval=0, maxval=self.num_paths - 1)
        if self._cal_state is not None:
            return  # don't interrupt calibration
        if self.sequences._is_printing():
            gcmd.respond_info(
                "SA: Filament detected at path %d entry (print in progress — not parking)." % path)
            return
        gcmd.respond_info("SA: Filament inserted at path %d — auto-parking..." % path)
        self.sequences.park_filament(gcmd, path)

    # ══════════════════════════════════════════════════════════════════════════
    # Klipper status — readable in macros as printer['stealth_autoloader']
    # ══════════════════════════════════════════════════════════════════════════

    def get_status(self, eventtime):
        enc_distances    = []
        entry_filament   = []
        toolhead_filament = []
        extruder_filament = []
        filament_loaded  = []

        for i in range(self.num_paths):
            d = self._encoder_distance(i)
            enc_distances.append(round(d, 2) if d is not None else -1.0)
            entry_filament.append(self._entry_sensor_active(i))
            toolhead_filament.append(self._toolhead_sensor_active(i))
            extruder_filament.append(self._extruder_sensor_active(i))
            filament_loaded.append(self.path_states[i] == self.STATE_LOADED)

        sel_pos = (self._selector_positions[self.current_path]
                   if self.current_path >= 0 else -1.0)

        return {
            'num_paths'         : self.num_paths,
            'current_path'      : self.current_path,
            'servo_engaged'     : self._servo_is_engaged,
            'path_states'       : list(self.path_states),
            'encoder_dist'      : enc_distances,
            'entry_filament'    : entry_filament,
            'toolhead_filament' : toolhead_filament,
            'extruder_filament' : extruder_filament,
            'filament_loaded'   : filament_loaded,
            'selector_position' : sel_pos,
            'cal_state'         : self._cal_state or '',
        }

    # ══════════════════════════════════════════════════════════════════════════
    # Klipper entry point
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def load_config(config):
        return StealthAutoloader(config)

def load_config(config):
    return StealthAutoloader(config)
