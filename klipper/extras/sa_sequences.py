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
        3. Engage phase: drive until encoder confirms filament is gripped
           (up to engage_max_distance mm).
        4. Bowden feed: drive through tube until extruder_sensor triggers
           (preferred) or encoder reaches bowden_length.  Slip is monitored.
        5. Heat & extrude: heat the extruder then push filament to nozzle.
        6. Purge: purge purge_length mm to clear the previous colour.
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

        # Reset encoder for this feed
        enc = owner._encoder(path)
        enc.set_direction(forward=True)
        enc.reset_distance()

        # ── Phase 2: engage filament with drive gear ──────────────────────────
        gcmd.respond_info("SA: Phase 2 — engaging filament with drive gear...")
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
                "SA: ERROR — no encoder motion after %.0fmm. "
                "Check filament position and encoder %d wiring." % (driven, path))
            return

        gcmd.respond_info(
            "SA: Encoder %d engaged (%.2fmm counted). "
            "Feeding through Bowden tube..." % (path, enc.get_distance()))

        # ── Phase 3: feed through Bowden tube ────────────────────────────────
        has_extruder_sensor = bool(owner._extruder_sensor_names[path])
        target_length       = owner._bowden_lengths[path]
        driven_total        = driven

        while True:
            # Primary stop condition: extruder sensor (most accurate)
            if has_extruder_sensor and owner._extruder_sensor_active(path):
                gcmd.respond_info(
                    "SA: Extruder sensor triggered at encoder=%.1fmm. "
                    "Filament arrived at extruder." % enc.get_distance())
                break

            # Fallback stop: encoder reached configured Bowden length
            if enc.get_distance() >= target_length:
                gcmd.respond_info(
                    "SA: Target length %.1fmm reached (encoder=%.1fmm)."
                    % (target_length, enc.get_distance()))
                break

            motion.drive_move(owner.feed_step_size)
            driven_total += owner.feed_step_size
            owner.reactor.pause(owner.reactor.monotonic() + owner.sensor_delay)

            # Slip check after first 50mm of driven travel
            if driven_total > 50.0:
                pct = abs(enc.get_distance() - driven_total) / driven_total * 100.0
                if pct > owner.slip_tolerance:
                    gcmd.respond_info(
                        "SA WARNING path %d: slip %.1f%% "
                        "(stepper=%.1fmm enc=%.1fmm)"
                        % (path, pct, driven_total, enc.get_distance()))

        gcmd.respond_info(
            "SA: Path %d — Bowden feed complete. "
            "Stepper=%.1fmm  Encoder=%.1fmm"
            % (path, driven_total, enc.get_distance()))

        # Release drive — extruder motor takes over from here
        motion.servo_disengage()
        owner.path_states[path] = 'partial'
        motion.save_position()

        # ── Phase 4: heat and extrude to nozzle ──────────────────────────────
        extruder = owner._extruder_names[path]
        gcmd.respond_info("SA: Heating %s to %.0f°C..." % (extruder, owner.load_temperature))
        owner.gcode.run_script_from_command(
            "TEMPERATURE_WAIT SENSOR=%s MINIMUM=%.0f"
            % (extruder, owner.load_temperature))

        gcmd.respond_info("SA: Extruding to nozzle tip (%.1fmm)..." % owner.nozzle_distance)
        owner.gcode.run_script_from_command("M83")
        owner.gcode.run_script_from_command("G1 E%.2f F300" % owner.nozzle_distance)
        owner.gcode.run_script_from_command("M400")

        # ── Phase 5: purge ────────────────────────────────────────────────────
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
