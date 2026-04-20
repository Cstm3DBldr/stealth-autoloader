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
        """Park active toolhead out of the way for drive work.

        No-print: raise to load_park_z then park on cooling pad.
                  Purge position (load_park_x/y) is applied AFTER heating.
        Mid-print: raise to z_safe, move to safe side position.
        """
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
            gcmd.respond_info("SA: Raising Z to %.1f and parking on cooling pad..." % owner.load_park_z)
            owner.gcode.run_script_from_command("G0 Z%.3f F600" % owner.load_park_z)
            owner.gcode.run_script_from_command("PARK_ON_COOLING_PAD")
        owner.gcode.run_script_from_command("M400")

    def _move_to_purge_position(self, gcmd, is_printing):
        """After heating, move toolhead to the purge/extrude position."""
        owner = self.owner
        if not is_printing:
            gcmd.respond_info(
                "SA: Moving to purge position X%.1f Y%.1f..."
                % (owner.load_park_x, owner.load_park_y))
            owner.gcode.run_script_from_command(
                "G0 X%.3f Y%.3f F5000" % (owner.load_park_x, owner.load_park_y))
            owner.gcode.run_script_from_command("M400")

    def _switch_tool(self, gcmd, path):
        """Switch to toolhead *path* if not already active."""
        if self._current_tool() != path:
            gcmd.respond_info("SA: Switching to toolhead T%d..." % path)
            self.owner.gcode.run_script_from_command("T%d" % path)
            self.owner.gcode.run_script_from_command("M400")

    def _ensure_selector(self, gcmd, path):
        """Always home selector before moving to *path* — guarantees accurate position."""
        owner = self.owner
        motion = owner.motion
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

    def _restore_state(self, gcmd, path, is_printing, after_unload=False):
        """After load/unload: resume print or clean + park + heater off.

        after_unload=True  — nozzle is empty and cold; skip clean+cooling-pad,
                             just park at the load position and switch T0.
        after_unload=False — nozzle is hot+primed; clean then park on cooling pad.
        """
        owner = self.owner
        if is_printing:
            gcmd.respond_info("SA: Resuming print...")
            owner.gcode.run_script_from_command("RESUME")
        else:
            # Re-assert load height — macros below must not lower Z
            owner.gcode.run_script_from_command(
                "G0 Z%.3f F600" % owner.load_park_z)
            owner.gcode.run_script_from_command("M400")
            gcmd.respond_info("SA: Turning off heater...")
            owner.gcode.run_script_from_command(
                "SET_HEATER_TEMPERATURE HEATER=%s TARGET=0"
                % owner._extruder_names[path])
            if after_unload:
                # Nozzle is empty — park on cooling pad if enabled, else load position
                if owner.cooling_pad_enabled:
                    gcmd.respond_info("SA: Parking on cooling pad...")
                    owner.gcode.run_script_from_command("PARK_ON_COOLING_PAD")
                else:
                    gcmd.respond_info("SA: Parking at load position...")
                    owner.gcode.run_script_from_command(
                        "G0 X%.3f Y%.3f F5000"
                        % (owner.load_park_x, owner.load_park_y))
                    owner.gcode.run_script_from_command("M400")
            else:
                # Nozzle is hot+primed — wipe then cool
                if owner.clean_nozzle_enabled:
                    gcmd.respond_info("SA: Cleaning nozzle...")
                    try:
                        owner.gcode.run_script_from_command("SA_CLEAN_NOZZLE")
                    except Exception as e:
                        gcmd.respond_info(
                            "SA: WARNING — SA_CLEAN_NOZZLE failed (%s). "
                            "Define it in macros.cfg." % str(e))
                if owner.cooling_pad_enabled:
                    gcmd.respond_info("SA: Parking on cooling pad...")
                    owner.gcode.run_script_from_command("PARK_ON_COOLING_PAD")
            owner.gcode.run_script_from_command("T0")

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

    def _extrude_mm(self, total_mm, speed_mmm, chunk=49.0):
        """Extrude *total_mm* in ≤49mm chunks to stay under max_extrude_only_distance.

        Handles both positive (extrude) and negative (retract) values.
        """
        owner = self.owner
        remaining = abs(total_mm)
        sign = 1.0 if total_mm >= 0 else -1.0
        while remaining > 0.0:
            move = min(remaining, chunk)
            owner.gcode.run_script_from_command(
                "G1 E%.2f F%d" % (sign * move, speed_mmm))
            remaining -= move
        owner.gcode.run_script_from_command("M400")

    def _fill_and_purge(self, gcmd, path):
        """Fill nozzle + purge at volumetric flow rate. Extruder must be hot."""
        owner = self.owner
        f = self._extrude_speed_mmm()
        gcmd.respond_info(
            "SA: Filling nozzle %.1fmm + purge %.1fmm at %dmm/min..."
            % (owner.fill_nozzle_length, owner.purge_length, f))
        owner.gcode.run_script_from_command("M83")
        self._extrude_mm(owner.fill_nozzle_length, f)
        self._extrude_mm(owner.purge_length, f)

    def _sync_feed_to_toolhead_sensor(self, gcmd, path):
        """Run drive motor and extruder together at feed_speed until toolhead sensor fires.

        Covers the dead zone between extruder sensor and extruder gear engagement
        (~20mm), then continues until filament is confirmed at the toolhead.

        Servo must be engaged before calling.  Disengages servo on return.
        Falls back to fill_nozzle_length fixed extrusion if no toolhead sensor.
        """
        owner  = self.owner
        motion = owner.motion
        dn     = owner._drv_name()

        has_sensor = bool(owner._toolhead_sensor_names[path])
        sync_speed = owner.feed_speed
        sync_f     = int(sync_speed * 60)
        step       = owner.feed_step_size
        # Safety ceiling: 2× fill_nozzle_length or 200mm, whichever is larger
        max_dist   = max(owner.fill_nozzle_length * 2.0, 200.0)

        if not has_sensor:
            gcmd.respond_info(
                "SA: No toolhead sensor — extruding %.1fmm to fill nozzle..."
                % owner.fill_nozzle_length)
            owner.gcode.run_script_from_command("M83")
            self._extrude_mm(owner.fill_nozzle_length, self._extrude_speed_mmm())
            motion.servo_disengage()
            return True

        if owner._toolhead_sensor_active(path):
            gcmd.respond_info("SA: Toolhead sensor already active on path %d." % path)
            motion.servo_disengage()
            return True

        gcmd.respond_info(
            "SA: Sync feed — drive + extruder at %.0fmm/s until toolhead sensor (path %d)..."
            % (sync_speed, path))

        owner.gcode.run_script_from_command("M83")
        motion._cancel_timeout(dn)
        owner.gcode.run_script_from_command("MANUAL_STEPPER STEPPER=%s ENABLE=1" % dn)

        driven    = 0.0
        triggered = False

        while driven < max_dist:
            # SYNC=0 starts drive move immediately without waiting for the extruder queue.
            # G1 E queues right after — both execute in parallel, same distance and speed.
            owner.gcode.run_script_from_command(
                "MANUAL_STEPPER STEPPER=%s SET_POSITION=0 MOVE=%.2f SPEED=%.1f SYNC=0"
                % (dn, step, sync_speed))
            owner.gcode.run_script_from_command(
                "G1 E%.2f F%d" % (step, sync_f))
            owner.gcode.run_script_from_command("M400")
            driven += step
            owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)

            if owner._toolhead_sensor_active(path):
                gcmd.respond_info(
                    "SA: Toolhead sensor triggered after %.1fmm sync feed on path %d."
                    % (driven, path))
                triggered = True
                break

        motion._arm_timeout(dn)
        motion.servo_disengage()

        if not triggered:
            gcmd.respond_info(
                "SA: ERROR — toolhead sensor path %d not triggered after %.0fmm. "
                "Check sensor wiring or re-run SA_CALIBRATE_BOWDEN TOOL=%d. "
                "Aborting load." % (path, max_dist, path))
            return False

        return True

    # ═══════════════════════════════════════════════════════════════════════════
    # Post-load purge prompt (state machine phase)
    # ═══════════════════════════════════════════════════════════════════════════

    def _prompt_purge(self, gcmd, path):
        """Print the post-load purge-more / park prompt."""
        owner = self.owner
        n = owner.num_paths - 1
        gcmd.respond_info(
            "SA: Load complete — path %d. Filament purging at nozzle.\n"
            "\n"
            "  SA_RESPOND VALUE=more      — purge %.0fmm again\n"
            "  SA_RESPOND VALUE=park      — clean nozzle and park on cooling pad\n"
            "  SA_RESPOND VALUE=load:N    — switch to path N and load (0-%d)\n"
            "  SA_RESPOND VALUE=unload:N  — switch to path N and unload (0-%d)\n"
            "  SA_RESPOND VALUE=exit      — disengage servo, heater off, leave toolhead in place"
            % (path, owner.purge_length, n, n))

    def _load_purge_respond(self, gcmd, value):
        """Handle SA_RESPOND during the load_purge state."""
        owner = self.owner
        data  = owner._cal_data
        path        = data['path']
        is_printing = data['is_printing']

        v = value.strip().lower()
        n = owner.num_paths

        def _parse_target(s):
            try:
                t = int(s)
                if 0 <= t < n:
                    return t
                gcmd.respond_info(
                    "SA: Path %d out of range (0-%d)." % (t, n - 1))
            except ValueError:
                gcmd.respond_info("SA: Unknown path '%s'." % s)
            return None

        if v == 'exit':
            owner._cal_state = None
            owner._cal_data  = {}
            owner.motion.servo_disengage()
            owner.gcode.run_script_from_command(
                "SET_HEATER_TEMPERATURE HEATER=%s TARGET=0"
                % owner._extruder_names[path])
            gcmd.respond_info(
                "SA: Exited — servo disengaged, heater off. "
                "Toolhead left in current position.")
            return
        elif v == 'more':
            f = self._extrude_speed_mmm()
            gcmd.respond_info("SA: Purging %.1fmm more..." % owner.purge_length)
            owner.gcode.run_script_from_command("M83")
            self._extrude_mm(owner.purge_length, f)
            self._prompt_purge(gcmd, path)
        elif v.startswith('load:') or v.startswith('unload:'):
            action, _, n_str = v.partition(':')
            target = _parse_target(n_str)
            if target is not None:
                owner._cal_state = None
                owner._cal_data  = {}
                owner.gcode.run_script_from_command(
                    "SET_HEATER_TEMPERATURE HEATER=%s TARGET=0"
                    % owner._extruder_names[path])
                if action == 'load':
                    self.do_load(gcmd, target)
                else:
                    self.do_unload(gcmd, target)
            else:
                self._prompt_purge(gcmd, path)
        else:
            owner._cal_state = None
            owner._cal_data  = {}
            gcmd.respond_info("SA: === LOAD COMPLETE — path %d ===" % path)
            self._restore_state(gcmd, path, is_printing)

    def _prompt_unload_park(self, gcmd, path):
        """Print the post-unload options prompt."""
        owner = self.owner
        n = owner.num_paths - 1
        gcmd.respond_info(
            "SA: Unload complete — path %d. What next?\n"
            "\n"
            "  SA_RESPOND VALUE=park        — clean nozzle and park on cooling pad\n"
            "  SA_RESPOND VALUE=load        — load new filament on path %d (same path)\n"
            "  SA_RESPOND VALUE=load:N      — load filament on path N (0-%d)\n"
            "  SA_RESPOND VALUE=unload:N    — unload filament on path N (0-%d)\n"
            "  SA_RESPOND VALUE=exit        — disengage servo, heater off, leave toolhead in place"
            % (path, path, n, n))

    def _unload_done_respond(self, gcmd, value):
        """Handle SA_RESPOND during the unload_done state."""
        owner = self.owner
        data  = owner._cal_data
        path        = data['path']
        is_printing = data['is_printing']
        owner._cal_state = None
        owner._cal_data  = {}

        v = value.strip().lower()
        n = owner.num_paths

        def _parse_target(s):
            try:
                t = int(s)
                if 0 <= t < n:
                    return t
                gcmd.respond_info(
                    "SA: Path %d out of range (0-%d) — parking instead." % (t, n - 1))
            except ValueError:
                gcmd.respond_info("SA: Unknown response '%s' — parking instead." % s)
            return None

        if v == 'exit':
            owner.motion.servo_disengage()
            owner.gcode.run_script_from_command(
                "SET_HEATER_TEMPERATURE HEATER=%s TARGET=0"
                % owner._extruder_names[path])
            gcmd.respond_info(
                "SA: Exited — servo disengaged, heater off. "
                "Toolhead left in current position.")
            return
        elif v == 'load':
            self.do_load(gcmd, path)
        elif v.startswith('load:'):
            target = _parse_target(v[5:])
            if target is not None:
                self.do_load(gcmd, target)
            else:
                self._restore_state(gcmd, path, is_printing, after_unload=True)
        elif v.startswith('unload:'):
            target = _parse_target(v[7:])
            if target is not None:
                self.do_unload(gcmd, target)
            else:
                self._restore_state(gcmd, path, is_printing, after_unload=True)
        else:
            self._restore_state(gcmd, path, is_printing, after_unload=True)

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

        # Stale partial state: filament was parked but user pulled the roll
        if owner.path_states[path] == 'partial' and not has_entry:
            gcmd.respond_info(
                "SA: Parked filament on path %d was removed (entry sensor inactive). "
                "Setting path to empty." % path)
            owner.path_states[path] = 'empty'
            return

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
        self._park(gcmd, is_printing)
        self._switch_tool(gcmd, path)
        self._ensure_selector(gcmd, path)

        # ══════════════════════════════════════════════════════════════════════
        # Branch A: ALL 3 SENSORS — filament loaded to nozzle area
        # Skip to heat + purge only
        # ══════════════════════════════════════════════════════════════════════
        if has_entry and has_extruder and has_toolhead:
            gcmd.respond_info(
                "SA: All sensors active — heating and verifying toolhead...")
            self._heat_for_load(gcmd, path)
            self._move_to_purge_position(gcmd, is_printing)
            self._wiggle_check_toolhead(gcmd, path)

            gcmd.respond_info("SA: Purging %.1fmm..." % owner.purge_length)
            f = self._extrude_speed_mmm()
            owner.gcode.run_script_from_command("M83")
            self._extrude_mm(owner.purge_length, f)

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
            no_motion = 0
            while owner._extruder_sensor_active(path) and retracted < limit:
                prev = abs(enc.get_distance())
                motion.drive_move(-owner.feed_step_size, speed=owner.feed_speed)
                owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)
                moved = abs(enc.get_distance()) - prev
                retracted += owner.feed_step_size
                if moved < owner.feed_step_size * 0.2:
                    no_motion += 1
                    if no_motion >= 3:
                        gcmd.respond_info(
                            "SA: ERROR — encoder not moving for 3 steps on path %d "
                            "(%.0fmm driven, %.1fmm encoder). "
                            "Drive gear lost grip or filament jammed."
                            % (path, retracted, abs(enc.get_distance())))
                        motion.servo_disengage()
                        return
                else:
                    no_motion = 0

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
        # Branch C: ENTRY ONLY — filament in bowden (or fresh insert or parked)
        # ══════════════════════════════════════════════════════════════════════
        elif has_entry and not has_extruder and not has_toolhead:
            if owner.path_states[path] == 'partial':
                # Filament parked at drive gears from previous unload.
                # Skip park+clear: engage and verify grip, then blast through bowden.
                gcmd.respond_info(
                    "SA: Parked filament on path %d — engaging and verifying grip..."
                    % path)
                motion.servo_engage()
            else:
                # Fresh filament inserted: park at encoder for consistent start.
                gcmd.respond_info(
                    "SA: Entry sensor only — parking filament for consistent start...")
                motion.servo_engage()
                self._park_filament_at_encoder(gcmd, path)
                self._retract_to_clear(gcmd, path)

            if not self._engage_check(gcmd, path):
                return

            if not self._blast_and_approach(gcmd, path):
                return

            # Keep servo engaged — common section needs it for sync feed
            owner.path_states[path] = 'partial'
            motion.save_position()
            # Fall through to heat + sync feed + purge

        # ══════════════════════════════════════════════════════════════════════
        # Common: cooling pad → heat → fill nozzle → purge → restore
        # ══════════════════════════════════════════════════════════════════════
        # Heat (cooling pad already set by _park for no-print loads)
        self._heat_for_load(gcmd, path)
        # Move to purge position (no-print: cooling pad → X175 Y0)
        self._move_to_purge_position(gcmd, is_printing)

        # Re-engage servo if Branch B disengaged it; Branch C is already engaged
        if not owner._servo_is_engaged:
            motion.servo_engage()

        # Sync drive + extruder together until toolhead sensor confirms grip.
        # Handles dead zone (extruder sensor → extruder gears, ~20mm) and beyond.
        # Disengages servo on return.
        if not self._sync_feed_to_toolhead_sensor(gcmd, path):
            return

        # Ensure servo is disengaged before extruder-only fill+purge
        motion.servo_disengage()

        # Fill nozzle (extruder gears → nozzle tip) then initial purge
        self._fill_and_purge(gcmd, path)

        owner.path_states[path] = 'loaded'

        # Set up purge confirmation — _restore_state is deferred until user responds
        owner._cal_state = 'load_purge'
        owner._cal_data  = {'path': path, 'is_printing': is_printing}
        self._prompt_purge(gcmd, path)

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
        self._park(gcmd, is_printing)
        self._switch_tool(gcmd, path)
        self._ensure_selector(gcmd, path)

        extruder_name = owner._extruder_names[path]

        # ══════════════════════════════════════════════════════════════════════
        # Branch A: TOOLHEAD ACTIVE — filament at/near nozzle
        # Temp check → tip form → fast retract past gears → drive to entry
        # ══════════════════════════════════════════════════════════════════════
        if has_toolhead:
            current_temp = self._extruder_temp(path)

            # Bring to tip_form_temp if too cold or too hot
            if current_temp < owner.tip_form_temp - 10:
                gcmd.respond_info(
                    "SA: Heating %s to %.0f°C for tip forming..."
                    % (extruder_name, owner.tip_form_temp))
                owner.gcode.run_script_from_command(
                    "SET_TOOL_TEMPERATURE T=%d TARGET=%.0f WAIT=1"
                    % (path, owner.tip_form_temp))
            elif current_temp > owner.tip_form_temp + 10:
                gcmd.respond_info(
                    "SA: Cooling %s to %.0f°C for tip forming..."
                    % (extruder_name, owner.tip_form_temp))
                owner.gcode.run_script_from_command(
                    "SET_HEATER_TEMPERATURE HEATER=%s TARGET=%.0f"
                    % (extruder_name, owner.tip_form_temp))
                owner.gcode.run_script_from_command(
                    "TEMPERATURE_WAIT SENSOR=%s MAXIMUM=%.0f"
                    % (extruder_name, owner.tip_form_temp + 5))

            # Move away from cooling pad to purge position before tip form
            self._move_to_purge_position(gcmd, is_printing)

            # Tip form — 3-phase retract:
            #   Phase 1 fast  : nozzle → heatbreak start (clears melt zone)
            #   Phase 2 hb    : through heatbreak at 5mm/s (optional dwell at midpoint)
            #   Phase 3 slow  : heatbreak end → sensor clearance+5%
            #
            # Total = nozzle_to_sensor_dist × 1.05
            # Slow speed tuned so phase-3 time ≈ previous slow phase × 1.25 (25% longer)
            owner.gcode.run_script_from_command("M83")
            push_f = int(owner.tip_form_push_speed * 60)
            gcmd.respond_info(
                "SA: Tip form push %.1fmm at %dmm/min..."
                % (owner.tip_form_push_length, push_f))
            self._extrude_mm(owner.tip_form_push_length, push_f)

            # Phase 1 — fast: nozzle to heatbreak start
            fast_dist = owner.tip_form_heatbreak_dist + owner.tip_form_push_length
            fast_f    = int(owner.tip_form_retract_speed * 60)
            gcmd.respond_info(
                "SA: Fast retract %.1fmm at %dmm/min (nozzle → heatbreak)..."
                % (fast_dist, fast_f))
            self._extrude_mm(-fast_dist, fast_f)

            # Phase 2 — heatbreak: 5mm/s, optional dwell at midpoint (45mm from nozzle)
            hb_half  = owner.tip_form_heatbreak_dist * 0.5
            hb_f     = int(owner.tip_form_heatbreak_speed * 60)
            gcmd.respond_info(
                "SA: Heatbreak retract %.1fmm at %dmm/min (first half)..."
                % (hb_half, hb_f))
            self._extrude_mm(-hb_half, hb_f)
            if owner.tip_form_dwell > 0:
                gcmd.respond_info(
                    "SA: Tip dwell %.1fs at heatbreak midpoint..."
                    % owner.tip_form_dwell)
                owner.reactor.pause(
                    owner.reactor.monotonic() + owner.tip_form_dwell)
            gcmd.respond_info(
                "SA: Heatbreak retract %.1fmm at %dmm/min (second half)..."
                % (hb_half, hb_f))
            self._extrude_mm(-hb_half, hb_f)

            # Phase 3 — slow: heatbreak exit through gears to sensor clearance+5%
            total_target = owner.nozzle_to_sensor_dist * 1.05
            covered      = fast_dist + owner.tip_form_heatbreak_dist
            slow_dist    = total_target - covered
            slow_f       = int(owner.tip_form_slow_speed * 60)
            if slow_dist > 0:
                gcmd.respond_info(
                    "SA: Slow retract %.1fmm at %dmm/min "
                    "(gears → sensor clearance %.0fmm)..."
                    % (slow_dist, slow_f, total_target))
                self._extrude_mm(-slow_dist, slow_f)

            owner.path_states[path] = 'partial'

            # Heater off — tip is past heatbreak (60mm+), safe to cool now
            gcmd.respond_info("SA: Heater off — filament clear of melt zone.")
            owner.gcode.run_script_from_command(
                "SET_HEATER_TEMPERATURE HEATER=%s TARGET=0" % extruder_name)

            # Engage drive servo — filament tip is past the heatbreak and extruder
            # gears.  From here all retraction is done by the drive motor.
            motion.servo_engage()

            enc = owner._encoder(path)
            enc.set_direction(forward=False)
            enc.reset_distance()

            # If extruder sensor still triggered: filament tip has passed the gears
            # but hasn't fully cleared the sensor.  The extruder motor can no longer
            # grip it — sync drive + extruder together in one continuous move.
            has_ext_sensor = bool(owner._extruder_sensor_names[path])
            if has_ext_sensor and owner._extruder_sensor_active(path):
                gcmd.respond_info(
                    "SA: Extruder sensor still active — sync drive+extruder "
                    "to pull filament clear...")

                dn       = owner._drv_name()
                sync_spd = owner.tip_form_slow_speed
                sync_f   = int(sync_spd * 60)
                max_sync = owner.sensor_retry_dist * 3   # e.g. 60mm

                owner.gcode.run_script_from_command("M83")
                motion._cancel_timeout(dn)
                owner.gcode.run_script_from_command(
                    "MANUAL_STEPPER STEPPER=%s ENABLE=1" % dn)

                # Single continuous move — drive starts async, extruder follows,
                # M400 inside _extrude_mm waits for both to complete.
                owner.gcode.run_script_from_command(
                    "MANUAL_STEPPER STEPPER=%s SET_POSITION=0 "
                    "MOVE=%.2f SPEED=%.1f SYNC=0"
                    % (dn, -max_sync, sync_spd))
                self._extrude_mm(-max_sync, sync_f)
                motion._arm_timeout(dn)

                # Encoder grip check
                enc_dist = abs(enc.get_distance())
                if enc_dist < max_sync * 0.3:
                    gcmd.respond_info(
                        "SA: WARNING — low encoder motion (%.1fmm / %.0fmm) "
                        "on path %d. Drive gear may not have grip."
                        % (enc_dist, max_sync, path))

                if owner._extruder_sensor_active(path):
                    motion.servo_disengage()
                    gcmd.respond_info(
                        "SA: ERROR — extruder sensor path %d still active after "
                        "%.0fmm sync retract.\n"
                        "Check for jam near extruder_sensor_%d or verify "
                        "sensor wiring.\n"
                        "Clear manually then re-run SA_UNLOAD TOOL=%d."
                        % (path, max_sync, path, path))
                    owner._cal_state = None
                    owner._cal_data  = {}
                    return

                gcmd.respond_info(
                    "SA: Extruder sensor cleared (encoder: %.1fmm)." % enc_dist)
            else:
                gcmd.respond_info("SA: Extruder sensor already clear.")

            # ── Zero at extruder sensor ──────────────────────────────────────────
            # Push filament forward slowly until the extruder sensor re-triggers.
            # This gives an exact reference position so the bowden retract distance
            # is based on the calibrated bowden_length from a known point.
            if has_ext_sensor:
                gcmd.respond_info(
                    "SA: Zeroing — pushing forward to extruder sensor...")
                enc.set_direction(forward=True)
                enc.reset_distance()
                zeroed   = False
                max_zero = owner.sensor_retry_dist * 3   # 60mm ceiling
                while abs(enc.get_distance()) < max_zero:
                    motion.drive_move(5.0, speed=owner.tip_form_slow_speed)
                    owner.reactor.pause(
                        owner.reactor.monotonic() + owner.sensor_delay)
                    if owner._extruder_sensor_active(path):
                        zeroed = True
                        gcmd.respond_info(
                            "SA: Zero confirmed — %.1fmm forward to sensor."
                            % abs(enc.get_distance()))
                        break
                if not zeroed:
                    gcmd.respond_info(
                        "SA: WARNING — extruder sensor did not re-trigger "
                        "after %.0fmm forward. "
                        "Proceeding with bowden_length as estimate." % max_zero)

            # ── Fast bowden blast — pull filament 95% of bowden length ──────────
            # Filament tip is now clear of the extruder sensor.  One fast continuous
            # drive move brings the tip to just inside the drive gear area.
            # The entry sensor is on the roll side of the drive gears so the
            # filament exits the gears before the entry sensor ever clears —
            # we park here and let the user physically remove the filament.
            sv          = owner.printer.lookup_object('save_variables', None)
            saved_max   = float(sv.allVariables.get('encoder_max_speed', 0)) if sv else 0
            blast_spd   = (saved_max * 0.75) if saved_max > 0 else owner.feed_speed
            bowden      = owner._bowden_lengths[path]
            blast_dist  = bowden * 0.95
            gcmd.respond_info(
                "SA: Blast retract %.0fmm at %.0fmm/s "
                "(95%% of bowden %.0fmm)..."
                % (blast_dist, blast_spd, bowden))
            enc.reset_distance()
            motion.drive_move(-blast_dist, speed=blast_spd)

            # Park precisely at drive gear encoder (servo still engaged)
            gcmd.respond_info("SA: Positioning filament precisely at drive gear...")
            self._park_filament_at_encoder(gcmd, path)

            motion.servo_disengage()
            owner.path_states[path] = 'partial'
            motion.save_position()
            gcmd.respond_info(
                "SA: Filament parked at drive gear — path %d. "
                "Pull from roll end to remove." % path)

            if is_printing:
                gcmd.respond_info("SA: Resuming print...")
                owner.gcode.run_script_from_command("RESUME")
            else:
                owner._cal_state = 'unload_done'
                owner._cal_data  = {'path': path, 'is_printing': is_printing}
                self._prompt_unload_park(gcmd, path)
            return  # Branch A complete — do not fall through to entry-sensor loop

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
        retracted = 0.0
        limit     = owner._bowden_lengths[path] + 100.0
        no_motion = 0

        while owner._entry_sensor_active(path) and retracted < limit:
            prev = abs(enc.get_distance())
            motion.drive_move(-owner.feed_step_size)
            owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)
            moved = abs(enc.get_distance()) - prev
            retracted += owner.feed_step_size
            if moved < owner.feed_step_size * 0.2:
                no_motion += 1
                if no_motion >= 3:
                    gcmd.respond_info(
                        "SA: ERROR — encoder not moving for 3 steps on path %d "
                        "(%.0fmm driven, %.1fmm encoder). "
                        "Drive gear lost grip or filament jammed."
                        % (path, retracted, abs(enc.get_distance())))
                    motion.servo_disengage()
                    owner.path_states[path] = 'unknown'
                    return
            else:
                no_motion = 0

        if owner._entry_sensor_active(path):
            gcmd.respond_info(
                "SA: WARNING — entry sensor still active after %.0fmm on path %d. "
                "Check for jam." % (retracted, path))

        motion.servo_disengage()
        owner.path_states[path] = 'empty'
        motion.save_position()
        gcmd.respond_info(
            "SA: Drive retract complete — path %d (%.1fmm retracted)."
            % (path, abs(enc.get_distance())))

        if is_printing:
            gcmd.respond_info("SA: Resuming print...")
            owner.gcode.run_script_from_command("RESUME")
        else:
            owner._cal_state = 'unload_done'
            owner._cal_data  = {'path': path, 'is_printing': is_printing}
            self._prompt_unload_park(gcmd, path)
