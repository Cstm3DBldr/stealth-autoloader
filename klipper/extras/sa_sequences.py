# sa_sequences.py — Stealth Autoloader load/unload sequences
#
# High-level filament feed sequences that orchestrate motion primitives
# from sa_motion.py and sensor reads from stealth_autoloader.py.

import sys, os as _os
_extras_dir = _os.path.dirname(_os.path.abspath(__file__))
if _extras_dir not in sys.path:
    sys.path.insert(0, _extras_dir)

import logging

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

    # ══════════════════════════════════════════════════════════════════════════
    # Load sequence
    # ══════════════════════════════════════════════════════════════════════════

    def do_load(self, gcmd, path):
        """Full filament load sequence for *path*.

        Phases
        ------
        1. Pre-flight: verify filament at entry sensor.
        2. Select path: disengage servo, position selector, engage servo.
        3. Retract to clear: step back until encoder goes quiet — consistent start.
        4. Engage: feed until encoder confirms grip.
        5. Blast: feed 98% of bowden_length at encoder_max_speed*0.75 — no sensor check.
        6. Approach: feed remaining 2% + overshoot at feed_speed with extruder sensor polling.
        7. Heat & extrude to nozzle, purge.
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

        # ── Phase 1: select path ──────────────────────────────────────────────
        gcmd.respond_info("SA: Selecting path %d (%.1fmm from home)..."
                          % (path, owner._selector_positions[path]))
        motion.servo_disengage()
        motion.selector_move_to(owner._selector_positions[path])
        owner.current_path = path
        motion.servo_engage()

        enc = owner._encoder(path)

        # ── Phase 2: retract to clear encoder — consistent start position ─────
        gcmd.respond_info("SA: Clearing encoder for consistent start...")
        enc.set_direction(forward=False)
        for _ in range(20):
            enc.reset_distance()
            motion.drive_move(-5.0, speed=25.0)
            owner.reactor.pause(owner.reactor.monotonic() + 0.15)
            if abs(enc.get_distance()) < 0.5:
                break

        # ── Phase 3: engage — feed until encoder confirms grip ────────────────
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

        # ── Phase 4: blast — 98% of bowden_length at calibrated speed ─────────
        sv = owner.printer.lookup_object('save_variables', None)
        saved_max  = float(sv.allVariables.get('encoder_max_speed', 0)) if sv else 0
        blast_speed = (saved_max * 0.75) if saved_max > 0 else 75.0
        target_length = owner._bowden_lengths[path]
        blast_target  = target_length * 0.98

        # Blast from current encoder position to 98% mark (single move)
        remaining_blast = blast_target - enc.get_distance()
        if remaining_blast > 0:
            motion.drive_move(remaining_blast, speed=blast_speed)

        gcmd.respond_info(
            "SA: Blast complete (enc=%.1fmm). Approaching extruder sensor..."
            % enc.get_distance())

        # ── Phase 5: approach — sensor polling for final 2% + overshoot ───────
        has_extruder_sensor = bool(owner._extruder_sensor_names[path])
        overshoot_limit     = target_length * 0.10   # 10% overshoot budget
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

        # Release drive — extruder motor takes over from here
        motion.servo_disengage()
        owner.path_states[path] = 'partial'
        motion.save_position()

        # ── Phase 6: heat and extrude to nozzle ──────────────────────────────
        extruder = owner._extruder_names[path]
        gcmd.respond_info("SA: Heating %s to %.0f°C..." % (extruder, owner.load_temperature))
        owner.gcode.run_script_from_command(
            "TEMPERATURE_WAIT SENSOR=%s MINIMUM=%.0f"
            % (extruder, owner.load_temperature))

        gcmd.respond_info("SA: Extruding to nozzle tip (%.1fmm)..." % owner.nozzle_distance)
        owner.gcode.run_script_from_command("M83")
        owner.gcode.run_script_from_command("G1 E%.2f F300" % owner.nozzle_distance)
        owner.gcode.run_script_from_command("M400")

        # ── Phase 7: purge ────────────────────────────────────────────────────
        gcmd.respond_info("SA: Purging %.1fmm..." % owner.purge_length)
        owner.gcode.run_script_from_command("G1 E%.2f F300" % owner.purge_length)
        owner.gcode.run_script_from_command("M400")

        owner.path_states[path] = 'loaded'
        gcmd.respond_info("SA: === LOAD COMPLETE — path %d ===" % path)

    # ══════════════════════════════════════════════════════════════════════════
    # Unload sequence
    # ══════════════════════════════════════════════════════════════════════════

    def do_unload(self, gcmd, path):
        """Full filament unload sequence for *path*.

        Steps
        -----
        1. Retract from nozzle tip via extruder motor (nozzle_distance + purge_length).
        2. Select the path (disengage servo, move selector, engage servo).
        3. Drive in reverse until the entry sensor clears.
        4. Disengage servo, mark path empty.
        """
        owner  = self.owner
        motion = owner.motion

        gcmd.respond_info("SA: === UNLOAD path %d ===" % path)

        # Step 1: pull filament back from nozzle via extruder
        retract = owner.nozzle_distance + owner.purge_length
        gcmd.respond_info("SA: Retracting %.1fmm from nozzle tip via extruder..." % retract)
        owner.gcode.run_script_from_command("M83")
        owner.gcode.run_script_from_command("G1 E-%.2f F300" % retract)
        owner.gcode.run_script_from_command("M400")
        owner.path_states[path] = 'partial'

        # Step 2: select path and engage drive gear
        gcmd.respond_info(
            "SA: Selecting path %d and pulling filament to entry sensor..." % path)
        motion.servo_disengage()
        motion.selector_move_to(owner._selector_positions[path])
        owner.current_path = path
        motion.servo_engage()

        # Step 3: drive in reverse until entry sensor clears
        enc = owner._encoder(path)
        enc.set_direction(forward=False)
        enc.reset_distance()

        while owner._entry_sensor_active(path):
            motion.drive_move(-owner.feed_step_size)
            owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)

        # Step 4: release drive gear
        motion.servo_disengage()
        owner.path_states[path] = 'empty'
        motion.save_position()
        gcmd.respond_info(
            "SA: === UNLOAD COMPLETE — path %d (%.1fmm retracted by drive) ==="
            % (path, abs(enc.get_distance())))
