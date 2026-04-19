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
    """Load and unload sequences for the Stealth Autoloader."""

    def __init__(self, owner):
        self.owner = owner

    # ═══════════════════════════════════════════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════════════════════════════════════════

    def _is_homed(self):
        """True if X Y Z are all homed."""
        try:
            th = self.owner.printer.lookup_object('toolhead')
            homed = th.get_kinematics().get_status(
                self.owner.reactor.monotonic()).get('homed_axes', '')
            return ('x' in homed and 'y' in homed and 'z' in homed)
        except Exception:
            return False

    def _is_printing(self):
        """True if PRINT_START.printing variable is set."""
        try:
            ps = self.owner.printer.lookup_object('gcode_macro PRINT_START')
            return bool(ps.variables.get('printing', False))
        except Exception:
            return False

    def _current_tool(self):
        """Active toolchanger tool number, or -1."""
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

    def _extruder_temp(self, path):
        """Current temperature of the extruder for *path*, or 0 on error."""
        extruder_name = self.owner._extruder_names[path]
        try:
            ext_obj = self.owner.printer.lookup_object(extruder_name)
            return ext_obj.get_status(self.owner.reactor.monotonic())['temperature']
        except Exception:
            return 0.0

    def _park(self, gcmd, is_printing):
        """Park active toolhead. Mid-print = safe side park; idle = load park."""
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
        """Switch to toolhead *path* if not already active."""
        if self._current_tool() != path:
            gcmd.respond_info("SA: Switching to toolhead T%d..." % path)
            self.owner.gcode.run_script_from_command("T%d" % path)
            self.owner.gcode.run_script_from_command("M400")

    def _ensure_selector(self, gcmd, path):
        """Home selector if needed, then move to *path* with servo disengaged."""
        owner = self.owner
        motion = owner.motion
        if not owner._selector_homed:
            gcmd.respond_info("SA: Homing selector...")
            motion.selector_home()
        motion.servo_disengage()
        motion.selector_move_to(owner._selector_positions[path])
        owner.current_path = path

    def _extrude_speed_mmm(self):
        """Volumetric-flow-limited extrusion speed in mm/min."""
        return int((self.owner.max_volumetric_flow / _FILAMENT_AREA) * 60)

    def _heat_for_load(self, gcmd, path):
        """Heat extruder to load_temperature and wait."""
        owner = self.owner
        extruder_name = owner._extruder_names[path]
        gcmd.respond_info("SA: Heating %s to %.0f°C..." % (extruder_name, owner.load_temperature))
        owner.gcode.run_script_from_command(
            "SET_TOOL_TEMPERATURE T=%d TARGET=%.0f WAIT=1" % (path, owner.load_temperature))

    def _restore_state(self, gcmd, path, is_printing):
        """After load/unload: resume print or park + heater off."""
        owner = self.owner
        if is_printing:
            gcmd.respond_info("SA: Resuming print...")
            owner.gcode.run_script_from_command("RESUME")
        else:
            owner.gcode.run_script_from_command(
                "SET_HEATER_TEMPERATURE HEATER=%s TARGET=0"
                % owner._extruder_names[path])
            owner.gcode.run_script_from_command("T0")
            if owner.cooling_pad_enabled:
                owner.gcode.run_script_from_command("PARK_ON_COOLING_PAD")

    # ═══════════════════════════════════════════════════════════════════════════
    # Wiggle checks
    # ═══════════════════════════════════════════════════════════════════════════

    def _wiggle_check_encoder(self, gcmd, path):
        """Retract *wiggle_distance* mm and check encoder for motion.

        Returns True if filament is confirmed real; False if no encoder motion
        (possible broken piece or jam).
        Servo must already be engaged before calling this.
        """
        owner = self.owner
        motion = owner.motion
        enc = owner._encoder(path)
        retract = owner.wiggle_distance

        gcmd.respond_info("SA: Wiggle check — retracting %.1fmm on path %d..." % (retract, path))
        enc.set_direction(forward=False)
        enc.reset_distance()
        motion.drive_move(-retract, speed=25.0)
        owner.reactor.pause(owner.reactor.monotonic() + 0.3)
        distance = abs(enc.get_distance())
        gcmd.respond_info("SA: Wiggle encoder: %.2fmm motion detected." % distance)

        # Push back to restore position
        enc.set_direction(forward=True)
        motion.drive_move(retract, speed=25.0)
        owner.reactor.pause(owner.reactor.monotonic() + 0.15)

        if distance < 0.5:
            gcmd.respond_info(
                "SA: WARNING — no encoder motion on path %d. "
                "Filament may be broken or missing in tube." % path)
            return False
        return True

    def _wiggle_check_toolhead(self, gcmd, path, retract_mm=5.0):
        """Verify toolhead sensor by small extruder retract + re-feed.

        Extruder must be at temperature before calling.
        Returns True if sensor responds correctly (or is plausibly real).
        """
        owner = self.owner
        gcmd.respond_info(
            "SA: Wiggle check toolhead sensor path %d (%.1fmm retract)..."
            % (path, retract_mm))

        owner.gcode.run_script_from_command("M83")
        owner.gcode.run_script_from_command("G1 E-%.2f F300" % retract_mm)
        owner.gcode.run_script_from_command("M400")
        owner.reactor.pause(owner.reactor.monotonic() + 0.2)

        still_triggered = owner._toolhead_sensor_active(path)

        # Push back
        owner.gcode.run_script_from_command("G1 E%.2f F300" % retract_mm)
        owner.gcode.run_script_from_command("M400")
        owner.reactor.pause(owner.reactor.monotonic() + 0.2)

        if still_triggered:
            gcmd.respond_info(
                "SA: WARNING — toolhead sensor path %d still active after %.1fmm retract. "
                "Possible jam near sensor — proceeding." % (path, retract_mm))
            return True

        re_triggered = owner._toolhead_sensor_active(path)
        if re_triggered:
            gcmd.respond_info("SA: Toolhead sensor confirmed on path %d." % path)
            return True

        gcmd.respond_info(
            "SA: WARNING — toolhead sensor path %d did not retrigger after push-back. "
            "Filament may be short or sensor fault." % path)
        return False

    # ═══════════════════════════════════════════════════════════════════════════
    # Park filament
    # ═══════════════════════════════════════════════════════════════════════════

    def _park_filament_at_encoder(self, gcmd, path):
        """Park filament tip at a consistent position just before the encoder.

        Two-pass encoder detection gives a repeatable starting point for blast.
        Servo must already be engaged before calling this.
        """
        owner = self.owner
        motion = owner.motion
        enc = owner._encoder(path)
        mpp = enc.mm_per_pulse or 1.5
        park_retract = owner.encoder_to_gear_distance * 0.25

        gcmd.respond_info("SA: Parking filament at encoder — path %d..." % path)

        # Retract until encoder goes quiet (filament tip cleared encoder)
        enc.set_direction(forward=False)
        for _ in range(60):                 # 300mm max
            enc.reset_distance()
            motion.drive_move(-5.0, speed=25.0)
            owner.reactor.pause(owner.reactor.monotonic() + 0.15)
            if abs(enc.get_distance()) < 0.5:
                break

        # Pass 1 — feed until encoder detects (find encoder entry point)
        enc.set_direction(forward=True)
        for _ in range(30):                 # 60mm max
            enc.reset_distance()
            motion.drive_move(2.0, speed=15.0)
            owner.reactor.pause(owner.reactor.monotonic() + 0.1)
            if enc.get_distance() >= mpp:
                break

        # Pull back past encoder
        motion.drive_move(-(park_retract + 6.0), speed=20.0)
        owner.reactor.pause(owner.reactor.monotonic() + 0.2)

        # Pass 2 — feed until encoder detects again (confirm consistent position)
        enc.set_direction(forward=True)
        for _ in range(20):
            enc.reset_distance()
            motion.drive_move(2.0, speed=15.0)
            owner.reactor.pause(owner.reactor.monotonic() + 0.1)
            if enc.get_distance() >= mpp:
                break

        # Final park retract — %.1f mm before encoder
        motion.drive_move(-park_retract, speed=20.0)
        owner.reactor.pause(owner.reactor.monotonic() + 0.2)

        gcmd.respond_info(
            "SA: Filament parked %.1fmm before encoder (path %d)."
            % (park_retract, path))

    def park_filament(self, gcmd, path):
        """Public: park filament on *path* — selects path, parks, disengages.

        Called by SA_PARK_FILAMENT and auto-insert handler.
        """
        owner = self.owner
        motion = owner.motion

        if not owner._entry_sensor_active(path):
            gcmd.respond_info(
                "SA: No filament at entry of path %d — nothing to park." % path)
            return

        self._ensure_selector(gcmd, path)
        motion.servo_engage()
        self._park_filament_at_encoder(gcmd, path)
        motion.servo_disengage()
        owner.path_states[path] = 'partial'
        motion.save_position()
        gcmd.respond_info(
            "SA: Filament parked on path %d. "
            "Run SA_LOAD TOOL=%d to load." % (path, path))

    # ═══════════════════════════════════════════════════════════════════════════
    # Drive phases (shared helpers to avoid duplication)
    # ═══════════════════════════════════════════════════════════════════════════

    def _retract_to_clear(self, gcmd, path):
        """Retract-to-clear encoder: 5mm steps until encoder goes quiet."""
        owner = self.owner
        motion = owner.motion
        enc = owner._encoder(path)
        gcmd.respond_info("SA: Clearing encoder for consistent start...")
        enc.set_direction(forward=False)
        for _ in range(20):
            enc.reset_distance()
            motion.drive_move(-5.0, speed=25.0)
            owner.reactor.pause(owner.reactor.monotonic() + 0.15)
            if abs(enc.get_distance()) < 0.5:
                break

    def _engage_check(self, gcmd, path):
        """Feed until encoder confirms grip. Returns False if grip not achieved."""
        owner = self.owner
        motion = owner.motion
        enc = owner._encoder(path)
        gcmd.respond_info("SA: Confirming grip on filament...")
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
            return False

        gcmd.respond_info("SA: Grip confirmed (%.2fmm)." % enc.get_distance())
        return True

    def _blast_and_approach(self, gcmd, path):
        """Blast 98% + sensor-polling approach. Returns False on sensor miss."""
        owner = self.owner
        motion = owner.motion
        enc = owner._encoder(path)

        sv = owner.printer.lookup_object('save_variables', None)
        saved_max   = float(sv.allVariables.get('encoder_max_speed', 0)) if sv else 0
        blast_speed = (saved_max * 0.75) if saved_max > 0 else 75.0
        target      = owner._bowden_lengths[path]

        remaining = (target * 0.98) - enc.get_distance()
        if remaining > 0:
            gcmd.respond_info("SA: Blasting %.1fmm at %.0fmm/s..." % (remaining, blast_speed))
            motion.drive_move(remaining, speed=blast_speed)

        gcmd.respond_info(
            "SA: Blast complete (enc=%.1fmm). Approaching extruder sensor..."
            % enc.get_distance())

        has_sensor   = bool(owner._extruder_sensor_names[path])
        overshoot    = target * 0.10
        inched       = 0.0
        triggered    = False

        while not triggered and inched < overshoot:
            if has_sensor and owner._extruder_sensor_active(path):
                triggered = True
                break
            if enc.get_distance() >= target and not has_sensor:
                break
            motion.drive_move(owner.feed_step_size)
            inched += owner.feed_step_size
            owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)

        if has_sensor and not triggered:
            motion.servo_disengage()
            gcmd.respond_info(
                "SA: ERROR — extruder sensor path %d not triggered. "
                "Re-run SA_CALIBRATE_BOWDEN TOOL=%d." % (path, path))
            return False

        gcmd.respond_info(
            "SA: Filament at extruder (enc=%.1fmm). Releasing drive gear."
            % enc.get_distance())
        return True

    def _fill_and_purge(self, gcmd, path):
        """Fill nozzle + purge at volumetric flow rate. Extruder must be hot."""
        owner = self.owner
        f = self._extrude_speed_mmm()
        gcmd.respond_info(
            "SA: Filling nozzle %.1fmm + purge %.1fmm at %dmm/min..."
            % (owner.fill_nozzle_length, owner.purge_length, f))
        owner.gcode.run_script_from_command("M83")
        owner.gcode.run_script_from_command(
            "G1 E%.2f F%d" % (owner.fill_nozzle_length, f))
        owner.gcode.run_script_from_command("M400")
        owner.gcode.run_script_from_command(
            "G1 E%.2f F%d" % (owner.purge_length, f))
        owner.gcode.run_script_from_command("M400")

    # ═══════════════════════════════════════════════════════════════════════════
    # Load sequence
    # ═══════════════════════════════════════════════════════════════════════════

    def do_load(self, gcmd, path):
        """Full filament load sequence for *path*.

        Sensor state determines which phases are skipped:
          entry only            → park at encoder → blast → approach → heat → fill+purge
          entry + extruder      → wiggle verify → retract past extruder → heat → fill+purge
          entry + extruder + th → wiggle toolhead → heat → purge only
          empty / broken        → abort with message
        """
        owner  = self.owner
        motion = owner.motion

        gcmd.respond_info("SA: === LOAD path %d ===" % path)

        # ── Sensor state ──────────────────────────────────────────────────────
        has_entry    = owner._entry_sensor_active(path)
        has_extruder = owner._extruder_sensor_active(path)
        has_toolhead = owner._toolhead_sensor_active(path)

        gcmd.respond_info("SA: Sensors — entry:%s extruder:%s toolhead:%s" % (
            "Y" if has_entry else "N",
            "Y" if has_extruder else "N",
            "Y" if has_toolhead else "N"))

        if not has_entry and not has_extruder and not has_toolhead:
            gcmd.respond_info(
                "SA: No filament on path %d. Insert roll and retry." % path)
            return

        if not has_entry and (has_extruder or has_toolhead):
            gcmd.respond_info(
                "SA: ERROR — extruder/toolhead sensor active without entry on path %d. "
                "Possible broken filament piece in tube." % path)
            return

        # ── Pre-flight ────────────────────────────────────────────────────────
        if not self._is_homed():
            gcmd.respond_info(
                "SA: Printer not homed. Run G28 first, then retry SA_LOAD TOOL=%d." % path)
            return

        is_printing = self._is_printing()
        gcmd.respond_info("SA: Print state: %s." % ("PRINTING" if is_printing else "idle"))
        self._switch_tool(gcmd, path)
        self._park(gcmd, is_printing)
        self._ensure_selector(gcmd, path)

        # ══════════════════════════════════════════════════════════════════════
        # Branch A: ALL 3 SENSORS — filament loaded to nozzle area
        # Skip to heat + purge only
        # ══════════════════════════════════════════════════════════════════════
        if has_entry and has_extruder and has_toolhead:
            gcmd.respond_info(
                "SA: All sensors active — heating and verifying toolhead...")
            self._heat_for_load(gcmd, path)

            if owner.cooling_pad_enabled:
                owner.gcode.run_script_from_command("PARK_ON_COOLING_PAD")
                owner.gcode.run_script_from_command("M400")

            self._wiggle_check_toolhead(gcmd, path)

            gcmd.respond_info("SA: Purging %.1fmm..." % owner.purge_length)
            f = self._extrude_speed_mmm()
            owner.gcode.run_script_from_command("M83")
            owner.gcode.run_script_from_command(
                "G1 E%.2f F%d" % (owner.purge_length, f))
            owner.gcode.run_script_from_command("M400")

            owner.path_states[path] = 'loaded'
            gcmd.respond_info(
                "SA: === LOAD COMPLETE (resumed from nozzle) — path %d ===" % path)
            self._restore_state(gcmd, path, is_printing)
            return

        # ══════════════════════════════════════════════════════════════════════
        # Branch B: ENTRY + EXTRUDER — filament at extruder gears
        # Wiggle verify, retract clear of extruder sensor, then heat+fill+purge
        # ══════════════════════════════════════════════════════════════════════
        if has_entry and has_extruder and not has_toolhead:
            gcmd.respond_info("SA: Filament at extruder gears — wiggle verifying...")
            motion.servo_engage()

            if not self._wiggle_check_encoder(gcmd, path):
                gcmd.respond_info(
                    "SA: ERROR — no encoder motion on path %d. "
                    "Filament may be broken. Check tube." % path)
                motion.servo_disengage()
                return

            # Retract until extruder sensor clears
            gcmd.respond_info("SA: Retracting to clear extruder sensor...")
            enc = owner._encoder(path)
            enc.set_direction(forward=False)
            enc.reset_distance()
            limit     = 200.0
            retracted = 0.0
            while owner._extruder_sensor_active(path) and retracted < limit:
                motion.drive_move(-owner.feed_step_size, speed=owner.feed_speed)
                retracted += owner.feed_step_size
                owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)

            if owner._extruder_sensor_active(path):
                gcmd.respond_info(
                    "SA: ERROR — could not clear extruder sensor after %.0fmm on path %d. "
                    "Check for jam." % (limit, path))
                motion.servo_disengage()
                return

            gcmd.respond_info(
                "SA: Confirmed — extruder sensor cleared (%.1fmm retracted). "
                "Filament tip just before extruder gears." % retracted)

            motion.servo_disengage()
            owner.path_states[path] = 'partial'
            motion.save_position()
            # Fall through to heat + fill + purge

        # ══════════════════════════════════════════════════════════════════════
        # Branch C: ENTRY ONLY — filament in bowden (or fresh insert)
        # Park at encoder, full blast + approach, then heat+fill+purge
        # ══════════════════════════════════════════════════════════════════════
        elif has_entry and not has_extruder and not has_toolhead:
            gcmd.respond_info(
                "SA: Entry sensor only — parking filament for consistent start...")
            motion.servo_engage()

            self._park_filament_at_encoder(gcmd, path)
            self._retract_to_clear(gcmd, path)

            if not self._engage_check(gcmd, path):
                return

            if not self._blast_and_approach(gcmd, path):
                return

            motion.servo_disengage()
            owner.path_states[path] = 'partial'
            motion.save_position()
            # Fall through to heat + fill + purge

        # ══════════════════════════════════════════════════════════════════════
        # Common: cooling pad → heat → fill nozzle → purge → restore
        # ══════════════════════════════════════════════════════════════════════
        if owner.cooling_pad_enabled:
            gcmd.respond_info("SA: Moving to cooling pad while heating...")
            owner.gcode.run_script_from_command("PARK_ON_COOLING_PAD")
            owner.gcode.run_script_from_command("M400")

        self._heat_for_load(gcmd, path)
        self._fill_and_purge(gcmd, path)

        owner.path_states[path] = 'loaded'
        gcmd.respond_info("SA: === LOAD COMPLETE — path %d ===" % path)
        self._restore_state(gcmd, path, is_printing)

    # ═══════════════════════════════════════════════════════════════════════════
    # Unload sequence
    # ═══════════════════════════════════════════════════════════════════════════

    def do_unload(self, gcmd, path):
        """Full filament unload sequence for *path*.

        Sensor state determines what's done:
          all sensors / toolhead active → temp check, tip form, fast retract, drive to entry
          entry + extruder only          → wiggle verify, drive retract to entry
          entry only                     → drive retract to entry (no heat needed)
          empty                          → nothing to do
        """
        owner  = self.owner
        motion = owner.motion

        gcmd.respond_info("SA: === UNLOAD path %d ===" % path)

        # ── Sensor state ──────────────────────────────────────────────────────
        has_entry    = owner._entry_sensor_active(path)
        has_extruder = owner._extruder_sensor_active(path)
        has_toolhead = owner._toolhead_sensor_active(path)

        gcmd.respond_info("SA: Sensors — entry:%s extruder:%s toolhead:%s" % (
            "Y" if has_entry else "N",
            "Y" if has_extruder else "N",
            "Y" if has_toolhead else "N"))

        if not has_entry and not has_extruder and not has_toolhead:
            gcmd.respond_info("SA: Path %d appears empty — nothing to unload." % path)
            owner.path_states[path] = 'empty'
            return

        # ── Pre-flight ────────────────────────────────────────────────────────
        if not self._is_homed():
            gcmd.respond_info(
                "SA: Printer not homed. Run G28 first, then retry SA_UNLOAD TOOL=%d." % path)
            return

        is_printing = self._is_printing()
        gcmd.respond_info("SA: Print state: %s." % ("PRINTING" if is_printing else "idle"))
        self._switch_tool(gcmd, path)
        self._park(gcmd, is_printing)
        self._ensure_selector(gcmd, path)

        extruder_name = owner._extruder_names[path]

        # ══════════════════════════════════════════════════════════════════════
        # Branch A: TOOLHEAD ACTIVE — filament at/near nozzle
        # Temp check → tip form → fast retract past gears → drive to entry
        # ══════════════════════════════════════════════════════════════════════
        if has_toolhead:
            current_temp = self._extruder_temp(path)

            if current_temp < owner.min_unload_temp:
                gcmd.respond_info(
                    "SA: ERROR — %s too cold (%.0f°C) to unload from nozzle. "
                    "Heat to at least %.0f°C first."
                    % (extruder_name, current_temp, owner.min_unload_temp))
                return

            # Adjust to tip form temp
            if current_temp > owner.tip_form_temp + 10:
                gcmd.respond_info(
                    "SA: Cooling %s to %.0f°C for tip forming..."
                    % (extruder_name, owner.tip_form_temp))
                owner.gcode.run_script_from_command(
                    "SET_HEATER_TEMPERATURE HEATER=%s TARGET=%.0f"
                    % (extruder_name, owner.tip_form_temp))
                owner.gcode.run_script_from_command(
                    "TEMPERATURE_WAIT SENSOR=%s MAXIMUM=%.0f"
                    % (extruder_name, owner.tip_form_temp + 5))
            elif current_temp < owner.tip_form_temp - 10:
                gcmd.respond_info(
                    "SA: Heating %s to %.0f°C for tip forming..."
                    % (extruder_name, owner.tip_form_temp))
                owner.gcode.run_script_from_command(
                    "SET_TOOL_TEMPERATURE T=%d TARGET=%.0f WAIT=1"
                    % (path, owner.tip_form_temp))

            # Cooling push — shapes pointed tip
            push_f = int(owner.tip_form_push_speed * 60)
            gcmd.respond_info(
                "SA: Tip form push %.1fmm at %dmm/min..."
                % (owner.tip_form_push_length, push_f))
            owner.gcode.run_script_from_command("M83")
            owner.gcode.run_script_from_command(
                "G1 E%.2f F%d" % (owner.tip_form_push_length, push_f))
            owner.gcode.run_script_from_command("M400")

            # Fast retract past extruder gears
            retract_dist = (owner.fill_nozzle_length + owner.purge_length
                            + owner.tip_form_push_length)
            retract_f = int(owner.tip_form_retract_speed * 60)
            gcmd.respond_info(
                "SA: Fast retract %.1fmm at %dmm/min..." % (retract_dist, retract_f))
            owner.gcode.run_script_from_command(
                "G1 E-%.2f F%d" % (retract_dist, retract_f))
            owner.gcode.run_script_from_command("M400")
            owner.path_states[path] = 'partial'

            # Heater off — filament is out of melt zone
            owner.gcode.run_script_from_command(
                "SET_HEATER_TEMPERATURE HEATER=%s TARGET=0" % extruder_name)

            # Engage drive to pull remaining bowden length
            motion.servo_engage()

        # ══════════════════════════════════════════════════════════════════════
        # Branch B: ENTRY + EXTRUDER — filament at gears, not at nozzle
        # Wiggle verify, drive retract to entry
        # ══════════════════════════════════════════════════════════════════════
        elif has_entry and has_extruder and not has_toolhead:
            gcmd.respond_info(
                "SA: Filament at extruder gears — wiggle verifying...")
            motion.servo_engage()

            if not self._wiggle_check_encoder(gcmd, path):
                gcmd.respond_info(
                    "SA: ERROR — no encoder motion on path %d. "
                    "Filament may be broken." % path)
                motion.servo_disengage()
                return

            gcmd.respond_info("SA: Filament confirmed. Retracting to entry sensor...")

        # ══════════════════════════════════════════════════════════════════════
        # Branch C: ENTRY ONLY — filament in bowden
        # ══════════════════════════════════════════════════════════════════════
        elif has_entry and not has_extruder and not has_toolhead:
            gcmd.respond_info(
                "SA: Filament in bowden only — retracting to entry sensor...")
            motion.servo_engage()

        # ── Drive retract until entry sensor clears ───────────────────────────
        enc = owner._encoder(path)
        enc.set_direction(forward=False)
        enc.reset_distance()

        while owner._entry_sensor_active(path):
            motion.drive_move(-owner.feed_step_size)
            owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)

        motion.servo_disengage()
        owner.path_states[path] = 'empty'
        motion.save_position()
        gcmd.respond_info(
            "SA: === UNLOAD COMPLETE — path %d (%.1fmm retracted by drive) ==="
            % (path, abs(enc.get_distance())))

        # ── State restoration ─────────────────────────────────────────────────
        if is_printing:
            gcmd.respond_info("SA: Resuming print...")
            owner.gcode.run_script_from_command("RESUME")
        else:
            owner.gcode.run_script_from_command("T0")
            if owner.cooling_pad_enabled:
                owner.gcode.run_script_from_command("PARK_ON_COOLING_PAD")
