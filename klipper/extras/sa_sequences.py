# sa_sequences.py — Stealth Autoloader load/unload sequences
#
# High-level filament feed sequences that orchestrate motion primitives
# from sa_motion.py and sensor reads from stealth_autoloader.py.

import sys, os as _os
_extras_dir = _os.path.dirname(_os.path.abspath(__file__))
if _extras_dir not in sys.path:
    sys.path.insert(0, _extras_dir)

import logging
import math

# filament cross-section for 1.75mm diameter
_FILAMENT_AREA = math.pi * (1.75 / 2.0) ** 2   # ~2.405 mm²

# ══════════════════════════════════════════════════════════════════════════════
# SASequences
# ══════════════════════════════════════════════════════════════════════════════

class SASequences:
    """Load and unload sequences for the Stealth Autoloader.

    ``owner`` is the StealthAutoloader instance.  All hardware access and
    parameters are read from owner attributes.
    """

    def __init__(self, owner):
        self.owner = owner

    # ── internal helpers ──────────────────────────────────────────────────────

    def _is_homed(self):
        """True if XYZ are all homed."""
        try:
            th = self.owner.printer.lookup_object('toolhead')
            homed = th.get_kinematics().get_status(
                self.owner.reactor.monotonic()).get('homed_axes', '')
            return ('x' in homed and 'y' in homed and 'z' in homed)
        except Exception:
            return False

    def _is_printing(self):
        """True if a print is currently in progress."""
        try:
            ps = self.owner.printer.lookup_object('gcode_macro PRINT_START')
            return bool(ps.variables.get('printing', False))
        except Exception:
            return False

    def _current_tool(self):
        """Active toolchanger tool number, or -1 if unavailable."""
        try:
            tc = self.owner.printer.lookup_object('toolchanger')
            return int(tc.get_status(self.owner.reactor.monotonic()).get('tool_number', -1))
        except Exception:
            return -1

    def _z_safe(self):
        """Current Z + 2mm, capped at axis maximum."""
        try:
            th  = self.owner.printer.lookup_object('toolhead')
            pos = th.get_position()
            kin = th.get_kinematics().get_status(self.owner.reactor.monotonic())
            max_z = kin.get('axis_maximum', [0, 0, 300])[2]
            return min(pos[2] + 2.0, max_z)
        except Exception:
            return 50.0

    def _park(self, gcmd, is_printing):
        """Park the active toolhead. Mid-print uses a safe side position; no-print uses load park."""
        owner = self.owner
        if is_printing:
            z = self._z_safe()
            gcmd.respond_info(
                "SA: Mid-print park X%.1f Y%.1f Z%.1f..."
                % (owner.load_print_park_x, owner.load_print_park_y, z))
            owner.gcode.run_script_from_command("G0 Z%.3f F600" % z)
            owner.gcode.run_script_from_command(
                "G0 X%.3f Y%.3f F5000"
                % (owner.load_print_park_x, owner.load_print_park_y))
        else:
            gcmd.respond_info(
                "SA: Parking X%.1f Y%.1f Z%.1f..."
                % (owner.load_park_x, owner.load_park_y, owner.load_park_z))
            owner.gcode.run_script_from_command(
                "G0 Z%.3f F600" % owner.load_park_z)
            owner.gcode.run_script_from_command(
                "G0 X%.3f Y%.3f F5000"
                % (owner.load_park_x, owner.load_park_y))
        owner.gcode.run_script_from_command("M400")

    def _switch_tool(self, gcmd, path):
        """Switch to toolhead *path* if it is not already active."""
        owner = self.owner
        if self._current_tool() != path:
            gcmd.respond_info("SA: Switching to toolhead T%d..." % path)
            owner.gcode.run_script_from_command("T%d" % path)
            owner.gcode.run_script_from_command("M400")

    def _extrude_speed_mmm(self):
        """Return volumetric-flow-limited extrusion speed in mm/min."""
        return int((self.owner.max_volumetric_flow / _FILAMENT_AREA) * 60)

    # ══════════════════════════════════════════════════════════════════════════
    # Load sequence
    # ══════════════════════════════════════════════════════════════════════════

    def do_load(self, gcmd, path):
        """Full filament load sequence for *path*.

        Phases
        ------
        0.  Entry sensor — confirm filament present.
        1.  Homed check — abort if printer not homed.
        2.  Detect print state.
        3.  Switch to correct toolhead.
        4.  Park toolhead (mid-print safe park or no-print load park).
        5.  Select path — disengage servo, position selector, engage servo.
        6.  Retract-to-clear encoder — consistent start position.
        7.  Engage — feed until encoder confirms grip.
        8.  Blast — 98% of bowden_length at encoder_max_speed×0.75.
        9.  Approach — final 2% + overshoot with extruder sensor polling.
        10. Park on cooling pad while heating (if cooling_pad_enabled).
        11. Heat to load_temperature.
        12. Fill nozzle at volumetric flow rate.
        13. Purge.
        14. State restoration — resume print or park + heaters off.
        """
        owner  = self.owner
        motion = owner.motion

        gcmd.respond_info("SA: === LOAD path %d ===" % path)

        # ── Phase 0: entry sensor check ───────────────────────────────────────
        if not owner._entry_sensor_active(path):
            gcmd.respond_info(
                "SA: No filament at entry of path %d. "
                "Insert roll and retry." % path)
            return

        # ── Phase 1: homed check ──────────────────────────────────────────────
        if not self._is_homed():
            gcmd.respond_info(
                "SA: Printer is not homed. Run G28 first, then retry SA_LOAD TOOL=%d." % path)
            return

        # ── Phase 2: detect print state ───────────────────────────────────────
        is_printing = self._is_printing()
        gcmd.respond_info(
            "SA: Print state: %s." % ("PRINTING" if is_printing else "idle"))

        # ── Phase 3: switch to correct toolhead ───────────────────────────────
        self._switch_tool(gcmd, path)

        # ── Phase 4: park toolhead ────────────────────────────────────────────
        self._park(gcmd, is_printing)

        # ── Phase 5: select path ──────────────────────────────────────────────
        gcmd.respond_info("SA: Selecting path %d (%.1fmm from home)..."
                          % (path, owner._selector_positions[path]))
        motion.servo_disengage()
        motion.selector_move_to(owner._selector_positions[path])
        owner.current_path = path
        motion.servo_engage()

        enc = owner._encoder(path)

        # ── Phase 6: retract to clear encoder — consistent start position ─────
        gcmd.respond_info("SA: Clearing encoder for consistent start...")
        enc.set_direction(forward=False)
        for _ in range(20):
            enc.reset_distance()
            motion.drive_move(-5.0, speed=25.0)
            owner.reactor.pause(owner.reactor.monotonic() + 0.15)
            if abs(enc.get_distance()) < 0.5:
                break

        # ── Phase 7: engage — feed until encoder confirms grip ────────────────
        gcmd.respond_info("SA: Engaging filament with drive gear...")
        enc.set_direction(forward=True)
        enc.reset_distance()
        driven    = 0.0
        mpp       = enc.mm_per_pulse
        threshold = (mpp * 3.0) if mpp else 1.5

        while enc.get_distance() < threshold and driven < owner.engage_max_distance:
            motion.drive_move(owner.feed_step_size)
            driven += owner.feed_step_size
            owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)

        if enc.get_distance() < threshold:
            motion.servo_disengage()
            gcmd.respond_info(
                "SA: ERROR — encoder %d not responding after %.0fmm. "
                "Check filament position and encoder wiring." % (path, driven))
            return

        gcmd.respond_info("SA: Grip confirmed (%.2fmm). Blasting through tube..." % enc.get_distance())

        # ── Phase 8: blast — 98% of bowden_length at calibrated speed ─────────
        sv = owner.printer.lookup_object('save_variables', None)
        saved_max   = float(sv.allVariables.get('encoder_max_speed', 0)) if sv else 0
        blast_speed = (saved_max * 0.75) if saved_max > 0 else 75.0
        target_length = owner._bowden_lengths[path]
        blast_target  = target_length * 0.98

        remaining_blast = blast_target - enc.get_distance()
        if remaining_blast > 0:
            motion.drive_move(remaining_blast, speed=blast_speed)

        gcmd.respond_info(
            "SA: Blast complete (enc=%.1fmm). Approaching extruder sensor..."
            % enc.get_distance())

        # ── Phase 9: approach — sensor polling for final 2% + overshoot ───────
        has_extruder_sensor = bool(owner._extruder_sensor_names[path])
        overshoot_limit     = target_length * 0.10
        inched              = 0.0
        triggered           = False

        while not triggered and inched < overshoot_limit:
            if has_extruder_sensor and owner._extruder_sensor_active(path):
                triggered = True
                break
            if enc.get_distance() >= target_length and not has_extruder_sensor:
                break
            motion.drive_move(owner.feed_step_size)
            inched += owner.feed_step_size
            owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)

        if has_extruder_sensor and not triggered:
            motion.servo_disengage()
            gcmd.respond_info(
                "SA: ERROR — extruder sensor path %d not triggered. "
                "Check sensor or re-run SA_CALIBRATE_BOWDEN TOOL=%d." % (path, path))
            return

        gcmd.respond_info(
            "SA: Filament at extruder (enc=%.1fmm). Releasing drive gear."
            % enc.get_distance())

        motion.servo_disengage()
        owner.path_states[path] = 'partial'
        motion.save_position()

        # ── Phase 10: park on cooling pad while heating ───────────────────────
        if owner.cooling_pad_enabled:
            gcmd.respond_info("SA: Moving to cooling pad while heating...")
            owner.gcode.run_script_from_command("PARK_ON_COOLING_PAD")
            owner.gcode.run_script_from_command("M400")

        # ── Phase 11: heat to load temperature ───────────────────────────────
        extruder = owner._extruder_names[path]
        gcmd.respond_info("SA: Heating %s to %.0f°C..." % (extruder, owner.load_temperature))
        owner.gcode.run_script_from_command(
            "TEMPERATURE_WAIT SENSOR=%s MINIMUM=%.0f"
            % (extruder, owner.load_temperature))

        # ── Phase 12: fill nozzle at volumetric flow rate ─────────────────────
        f = self._extrude_speed_mmm()
        gcmd.respond_info(
            "SA: Filling nozzle — %.1fmm at %dmm/min (%.1fmm³/s)..."
            % (owner.fill_nozzle_length, f, owner.max_volumetric_flow))
        owner.gcode.run_script_from_command("M83")
        owner.gcode.run_script_from_command(
            "G1 E%.2f F%d" % (owner.fill_nozzle_length, f))
        owner.gcode.run_script_from_command("M400")

        # ── Phase 13: purge ───────────────────────────────────────────────────
        gcmd.respond_info("SA: Purging %.1fmm..." % owner.purge_length)
        owner.gcode.run_script_from_command(
            "G1 E%.2f F%d" % (owner.purge_length, f))
        owner.gcode.run_script_from_command("M400")

        owner.path_states[path] = 'loaded'
        gcmd.respond_info("SA: === LOAD COMPLETE — path %d ===" % path)

        # ── Phase 14: state restoration ───────────────────────────────────────
        if is_printing:
            gcmd.respond_info("SA: Resuming print...")
            owner.gcode.run_script_from_command("RESUME")
        else:
            gcmd.respond_info("SA: Cooling down...")
            owner.gcode.run_script_from_command(
                "SET_HEATER_TEMPERATURE HEATER=%s TARGET=0" % extruder)
            owner.gcode.run_script_from_command("T0")
            if owner.cooling_pad_enabled:
                owner.gcode.run_script_from_command("PARK_ON_COOLING_PAD")

    # ══════════════════════════════════════════════════════════════════════════
    # Unload sequence
    # ══════════════════════════════════════════════════════════════════════════

    def do_unload(self, gcmd, path):
        """Full filament unload sequence for *path*.

        Steps
        -----
        0.  Homed check — abort if printer not homed.
        1.  Detect print state; switch to correct toolhead; park.
        2.  Tip forming — cool to tip_form_temp, cooling push, fast retract.
        3.  Select path; engage drive gear.
        4.  Drive in reverse until entry sensor clears.
        5.  Disengage servo; mark path empty.
        6.  State restoration — resume print or cooling pad park.
        """
        owner  = self.owner
        motion = owner.motion

        gcmd.respond_info("SA: === UNLOAD path %d ===" % path)

        # ── Step 0: homed check ───────────────────────────────────────────────
        if not self._is_homed():
            gcmd.respond_info(
                "SA: Printer is not homed. Run G28 first, then retry SA_UNLOAD TOOL=%d." % path)
            return

        # ── Step 1: detect print state & park ────────────────────────────────
        is_printing = self._is_printing()
        gcmd.respond_info(
            "SA: Print state: %s." % ("PRINTING" if is_printing else "idle"))

        self._switch_tool(gcmd, path)
        self._park(gcmd, is_printing)

        # ── Step 2: tip forming ───────────────────────────────────────────────
        extruder = owner._extruder_names[path]
        gcmd.respond_info(
            "SA: Tip forming on %s — cooling to %.0f°C..."
            % (extruder, owner.tip_form_temp))

        # Drop to tip-form temp
        owner.gcode.run_script_from_command(
            "SET_HEATER_TEMPERATURE HEATER=%s TARGET=%.0f"
            % (extruder, owner.tip_form_temp))
        owner.gcode.run_script_from_command(
            "TEMPERATURE_WAIT SENSOR=%s MAXIMUM=%.0f"
            % (extruder, owner.tip_form_temp + 5))

        # Cooling push — slow extrusion shapes tip while filament cools
        push_f = int(owner.tip_form_push_speed * 60)
        gcmd.respond_info(
            "SA: Tip form push %.1fmm at %dmm/min..." % (owner.tip_form_push_length, push_f))
        owner.gcode.run_script_from_command("M83")
        owner.gcode.run_script_from_command(
            "G1 E%.2f F%d" % (owner.tip_form_push_length, push_f))
        owner.gcode.run_script_from_command("M400")

        # Fast retract past extruder gears — clears nozzle + fill + purge + push
        retract_dist  = (owner.fill_nozzle_length + owner.purge_length
                         + owner.tip_form_push_length)
        retract_f     = int(owner.tip_form_retract_speed * 60)
        gcmd.respond_info(
            "SA: Fast retract %.1fmm at %dmm/min..." % (retract_dist, retract_f))
        owner.gcode.run_script_from_command(
            "G1 E-%.2f F%d" % (retract_dist, retract_f))
        owner.gcode.run_script_from_command("M400")
        owner.path_states[path] = 'partial'

        # Turn off heater — filament is out of the melt zone
        owner.gcode.run_script_from_command(
            "SET_HEATER_TEMPERATURE HEATER=%s TARGET=0" % extruder)

        # ── Step 3: select path and engage drive gear ─────────────────────────
        gcmd.respond_info(
            "SA: Selecting path %d — pulling filament to entry sensor..." % path)
        motion.servo_disengage()
        motion.selector_move_to(owner._selector_positions[path])
        owner.current_path = path
        motion.servo_engage()

        # ── Step 4: drive in reverse until entry sensor clears ────────────────
        enc = owner._encoder(path)
        enc.set_direction(forward=False)
        enc.reset_distance()

        while owner._entry_sensor_active(path):
            motion.drive_move(-owner.feed_step_size)
            owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)

        # ── Step 5: release drive gear ────────────────────────────────────────
        motion.servo_disengage()
        owner.path_states[path] = 'empty'
        motion.save_position()
        gcmd.respond_info(
            "SA: === UNLOAD COMPLETE — path %d (%.1fmm retracted by drive) ==="
            % (path, abs(enc.get_distance())))

        # ── Step 6: state restoration ─────────────────────────────────────────
        if is_printing:
            gcmd.respond_info("SA: Resuming print...")
            owner.gcode.run_script_from_command("RESUME")
        else:
            owner.gcode.run_script_from_command("T0")
            if owner.cooling_pad_enabled:
                owner.gcode.run_script_from_command("PARK_ON_COOLING_PAD")
