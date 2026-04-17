# Stealth Autoloader

A Klipper firmware extra for an automated multi-filament loader built around the BTT MMB CAN V2.0 board.

Supports up to 32 filament paths with a single shared drive motor, a carriage-style selector motor, a latching servo to engage the drive gear, and one fixed optical encoder per path. Optional per-path entry, toolhead, and extruder entry sensors enable fully automated loading, accurate Bowden tube length detection, and real-time slip monitoring.

---

## Installation

```bash
wget -O - https://raw.githubusercontent.com/Cstm3DBldr/stealth-autoloader/main/install.sh | bash
```

After the command finishes, add this line to `printer.cfg`:

```
[include stealth-autoloader/*.cfg]
```

Then save and restart Klipper.

### Uninstall

```bash
cd ~/stealth-autoloader
./install.sh --uninstall
rm -rf ~/stealth-autoloader
```

---

## Hardware overview

| Component | Hardware | Notes |
|---|---|---|
| MCU | BTT MMB CAN V2.0 | STM32G0B1, CAN bus |
| Drive motor | M3 — TMC5160 | Single gear, shared across all paths |
| Selector motor | M1 — TMC5160 | Moves carriage to active path, endstop homing |
| Engage servo | BTT servo header | Latching servo, PWM cut after move |
| Path encoders | 6x fixed optical | Never move; one per path |
| Entry sensors | 6x switch sensors | At roll end of each path |
| Toolhead sensors | 6x EBB36 PB5 | Nozzle end of each Bowden tube |
| Extruder sensors | 6x EBB36 PB8 | At extruder gears, used for load targeting |

---

## File structure

```
klipper/extras/
    stealth_autoloader.py   Main controller, config parsing, GCode registration
    sa_motion.py            All motion primitives (servo, selector, drive, timeouts)
    sa_sequences.py         Load and unload sequences
    sa_calibration.py       All calibration routines

stealth-autoloader/
    hardware.cfg            MCU, stepper, servo, encoder, sensor, and controller config
```

---

## Path states

Each of the 6 paths has a state that persists across moves:

| State | Meaning |
|---|---|
| `unknown` | Not confirmed — state after boot or explicit reset |
| `empty` | No filament in path |
| `partial` | Filament in Bowden tube but not loaded to nozzle |
| `loaded` | Filament loaded all the way to nozzle tip |

---

## GCode commands reference

### Motion commands

#### `SA_HOME`
Homes the selector carriage to the endstop using a double-touch sequence (fast approach, back-off, slow re-approach). Always run this before any path selection after power-on or if the carriage position is uncertain.

```
SA_HOME
```

#### `SA_SELECT TOOL=N`
Disengages the servo and moves the selector carriage to path N (neutral, no filament gripped).

```
SA_SELECT TOOL=2
```

#### `SA_ENGAGE`
Engages the drive servo — grips filament in the currently selected path. Run `SA_SELECT TOOL=N` first.

```
SA_ENGAGE
```

#### `SA_DISENGAGE`
Disengages the drive servo — path returns to neutral.

```
SA_DISENGAGE
```

### Load / unload

#### `SA_LOAD TOOL=N`
Full automated load sequence for path N:
1. Check entry sensor — abort if no filament.
2. Select path, engage drive gear.
3. Feed until encoder confirms grip.
4. Feed through Bowden tube until extruder sensor triggers (or bowden_length reached).
5. Monitor encoder slip throughout.
6. Release drive gear.
7. Heat extruder to `load_temperature`.
8. Extrude `nozzle_distance` mm to push filament to nozzle tip.
9. Purge `purge_length` mm.

```
SA_LOAD TOOL=0
```

#### `SA_UNLOAD TOOL=N`
Full automated unload sequence for path N:
1. Retract `nozzle_distance + purge_length` mm via extruder motor.
2. Select path, engage drive gear.
3. Drive in reverse until entry sensor clears.
4. Disengage drive gear.

```
SA_UNLOAD TOOL=3
```

### Status and diagnostics

#### `SA_STATUS`
Prints a full status table showing all paths — entry/toolhead/extruder sensor states, path state, encoder distance, and mm_per_pulse.

```
SA_STATUS
```

#### `SA_ENCODER_QUERY [TOOL=N] [RESET=1]`
Snapshot of encoder distances and sensor states. Optional `RESET=1` zeros counters before reading.

```
SA_ENCODER_QUERY
SA_ENCODER_QUERY TOOL=2
SA_ENCODER_QUERY RESET=1
SA_ENCODER_QUERY TOOL=2 RESET=1
```

#### `SA_ENCODER_WATCH [TOOL=N] [DURATION=30] [INTERVAL=0.5]`
Live encoder delta stream. Prints one line per interval showing movement on each encoder since the previous tick. Useful for confirming which encoder responds to which path.

- Paths with any movement are marked with `*`.
- Press `CTRL+C` (SSH) or `ESTOP` to abort early.

```
SA_ENCODER_WATCH
SA_ENCODER_WATCH TOOL=2
SA_ENCODER_WATCH DURATION=60 INTERVAL=1.0
```

### Motor tests

#### `SA_BUZZ_DRIVE [DISTANCE=5] [SPEED=10] [REPS=3]`
Oscillates the drive motor back and forth to confirm it moves. Safe to run without filament loaded. The motor is disabled after the test.

```
SA_BUZZ_DRIVE
SA_BUZZ_DRIVE DISTANCE=10 SPEED=20 REPS=5
```

#### `SA_BUZZ_SELECTOR [DISTANCE=10] [SPEED=50] [REPS=3]`
Oscillates the selector motor back and forth. Run after `SA_HOME` so position is known.

```
SA_BUZZ_SELECTOR
SA_BUZZ_SELECTOR DISTANCE=20 SPEED=100
```

### State management

#### `SA_SET_STATE TOOL=N STATE=x`
Manually override the path state. Valid states: `unknown`, `empty`, `partial`, `loaded`.

```
SA_SET_STATE TOOL=0 STATE=empty
SA_SET_STATE TOOL=2 STATE=loaded
```

---

## Calibration commands

All interactive calibration commands use a console response pattern: the routine prints a prompt, then waits for you to send `SA_RESPOND VALUE=<answer>`. Send `SA_RESPOND VALUE=abort` at any point to cancel.

### `SA_RESPOND VALUE=x`
Delivers a console response to a waiting calibration routine. Only meaningful when a calibration is running.

```
SA_RESPOND VALUE=yes
SA_RESPOND VALUE=103.5
SA_RESPOND VALUE=abort
```

---

### Drive motor calibration — `SA_CALIBRATE_DRIVE`

Calibrates `rotation_distance` for the drive motor. One motor drives all paths, so this only needs to be done once.

**What it does:** Commands 100mm of filament movement, asks you to measure the actual distance, then calculates the correct `rotation_distance`. Repeats up to 3 times if error is greater than 1mm.

**Requirements:** Filament loaded past the drive gear on at least one path. A ruler or calipers.

**Process:**
1. Run `SA_CALIBRATE_DRIVE`.
2. When prompted, enter the path number that has filament loaded.
3. Mark the filament at the encoder exit (piece of tape works well).
4. Send `SA_RESPOND VALUE=yes` when ready.
5. The routine commands 100mm.
6. Measure from your mark to the new filament position.
7. Send `SA_RESPOND VALUE=<measured mm>` (e.g., `SA_RESPOND VALUE=103.5`).
8. Repeat up to 3 passes if needed.
9. Send `SA_RESPOND VALUE=yes` to save and restart, or `no` to queue for later.

**Result:** Updates `rotation_distance` in `[manual_stepper sa_drive]` via `SAVE_CONFIG`.

---

### Encoder calibration — `SA_CALIBRATE_ENCODER TOOL=N`

Calibrates `mm_per_pulse` for one path encoder using 5 x 400mm feed/retract cycles.

**Requirements:** Filament loaded past drive gear and through encoder for path N. Enough free filament for ~2000mm total travel.

**Process:**
1. Run `SA_CALIBRATE_ENCODER TOOL=N`.
2. Position filament flush with encoder exit as a zero reference point.
3. Send `SA_RESPOND VALUE=yes`.
4. The routine runs 5 feed + 5 retract cycles automatically.
5. When prompted, measure the final filament exit distance (should be ~200mm from start).
6. Send `SA_RESPOND VALUE=<measurement>` or `SA_RESPOND VALUE=ok` if correct.
7. Confirm save and restart.

**Result:** Updates `mm_per_pulse` in `[sa_encoder N]` via `SAVE_CONFIG`.

---

### Selector calibration — `SA_CALIBRATE_SELECTOR`

Fully automated one-time calibration. The routine homes the carriage, sweeps to the far mechanical stop using TMC5160 stallguard, then homes back to measure the total rail length precisely. Path positions are calculated automatically from the measured travel — no manual measurement needed.

**Requirements:** `SA_HOME` must work correctly first. No filament loaded. `[gcode_button selector_stall]` must be present in `hardware.cfg` (pin `^!autoloader:SA_SELECTOR_DIAG`).

**Process:**
1. Run `SA_CALIBRATE_SELECTOR`.
2. Send `SA_RESPOND VALUE=yes` when prompted to confirm ready.
3. The carriage homes, then sweeps outward. The TMC5160 `stop_enable` bit latches the stall signal so it is reliably detected after the move. If no stall is detected the carriage briefly contacts the hard stop at reduced current (0.4 A), which is harmless.
4. The routine homes back to the physical endstop and measures total travel from the MCU step delta.
5. Calculated positions are displayed. Send `SA_RESPOND VALUE=yes` to accept or `no` to cancel.
6. Send `SA_RESPOND VALUE=yes` to save and restart, or `no` to queue for later.

**Tuning if the carriage stalls mid-travel (before reaching the far end):**
- Raise `selector_stall_threshold` (less sensitive stallguard)
- Raise `selector_stall_current` (more traversal torque)

**Result:** Updates `selector_position_0` through `selector_position_N` in `[stealth_autoloader]` via `SAVE_CONFIG`.

**Manual alternative:** Jog the selector manually with `MANUAL_STEPPER STEPPER=sa_selector ENABLE=1 MOVE=<mm> SPEED=30`, use `SA_ENCODER_WATCH` to watch while manually checking carriage alignment, and update `selector_position_N` values in `hardware.cfg` by hand.

---

### Bowden tube calibration — `SA_CALIBRATE_BOWDEN TOOL=N`

Calibrates the exact Bowden tube length for one path by detecting when filament reaches the extruder gears. Requires `extruder_sensor_N` configured.

**Requirements:** `extruder_sensor_N` wired and configured. Filament loaded to entry sensor on path N.

**Process:**
1. Run `SA_CALIBRATE_BOWDEN TOOL=N`.
2. Enter the estimated tube length when prompted (over-estimate is safer).
3. The routine runs 3 trials: fast approach to 90% of estimate, then slow inch-forward until the extruder sensor triggers.
4. Results are averaged.
5. Confirm save and restart.

**Result:** Updates `bowden_length_N` in `[stealth_autoloader]` via `SAVE_CONFIG`. The load sequence uses this value as the target for the Bowden feed phase.

---

## Calibration sequence (first-time setup)

Recommended order for a new installation:

1. **Flash and connect** the BTT MMB CAN V2.0 board.
2. **Update `canbus_uuid`** in `hardware.cfg`.
3. **Test motors:** `SA_BUZZ_DRIVE` then `SA_BUZZ_SELECTOR` — confirm both move.
4. **Test servo:** `SA_ENGAGE` then `SA_DISENGAGE` — confirm servo moves.
5. **Home selector:** `SA_HOME` — confirm endstop triggers and carriage returns.
6. **Calibrate selector:** `SA_CALIBRATE_SELECTOR` — auto-calculates path positions.
7. **Load filament** on path 0 past the drive gear.
8. **Calibrate drive motor:** `SA_CALIBRATE_DRIVE` — sets `rotation_distance`.
9. **Calibrate encoders:** `SA_CALIBRATE_ENCODER TOOL=N` for each path.
10. **Calibrate Bowden lengths:** `SA_CALIBRATE_BOWDEN TOOL=N` for each path (requires extruder sensors).
11. **Test full load:** `SA_LOAD TOOL=0` — verify complete sequence.

---

## Klipper status object

The autoloader state is accessible in macros as `printer['stealth_autoloader']`:

```jinja
{% set sa = printer['stealth_autoloader'] %}
{% if sa.filament_loaded[0] %}
  { action_respond_info("Path 0 is loaded") }
{% endif %}
```

Available keys:

| Key | Type | Description |
|---|---|---|
| `num_paths` | int | Number of configured paths |
| `current_path` | int | Active path index, -1 if unhomed |
| `servo_engaged` | bool | True if drive servo is engaged |
| `path_states` | list[str] | State string per path |
| `encoder_dist` | list[float] | Current encoder distance per path (mm) |
| `entry_filament` | list[bool] | Filament at roll end per path |
| `toolhead_filament` | list[bool] | Filament at toolhead per path |
| `extruder_filament` | list[bool] | Filament at extruder gears per path |
| `filament_loaded` | list[bool] | True if path state is `loaded` |
| `selector_position` | float | Current path position in mm, -1 if unhomed |

---

## Configuration reference

All parameters are set in the `[stealth_autoloader]` section of `hardware.cfg`.

### Hardware references

| Key | Default | Description |
|---|---|---|
| `drive_stepper` | required | Full name, e.g. `manual_stepper sa_drive` |
| `selector_stepper` | required | Full name, e.g. `manual_stepper sa_selector` |
| `servo` | required | Full name, e.g. `servo sa_engage` |

### Servo

| Key | Default | Description |
|---|---|---|
| `servo_engaged_angle` | `30` | Degrees — drive gear grips filament |
| `servo_disengaged_angle` | `160` | Degrees — drive gear releases |

### Per-path (N = 0 to num_paths-1)

| Key | Default | Description |
|---|---|---|
| `encoder_N` | `sa_encoder N` | Encoder section name |
| `entry_sensor_N` | none | Entry sensor section name (optional) |
| `toolhead_sensor_N` | none | Toolhead sensor section name (optional) |
| `extruder_sensor_N` | none | Extruder sensor section name (optional, required for bowden cal) |
| `selector_position_N` | `N * 21.0` | Selector position in mm from home |
| `extruder_N` | `extruder` (0) / `extruderN` | Extruder name for heating |
| `bowden_length_N` | `800.0` | Bowden tube length mm (calibrate with SA_CALIBRATE_BOWDEN) |

### Motion parameters

| Key | Default | Description |
|---|---|---|
| `num_paths` | `6` | Number of paths (1-32) |
| `tube_length` | `800` | Legacy Bowden length (mm), used if bowden_length_N not set |
| `nozzle_distance` | `50` | Distance from extruder gears to nozzle tip (mm) |
| `purge_length` | `30` | Purge extrusion after nozzle is reached (mm) |
| `engage_max_distance` | `60` | Max drive travel before expecting encoder motion (mm) |
| `load_temperature` | `200` | Minimum hotend temp before extruder moves (C) |
| `feed_speed` | `50` | Drive motor speed (mm/s) |
| `selector_speed` | `200` | Selector motor speed (mm/s) |
| `feed_step_size` | `10` | Drive step per loop iteration (mm) |
| `slip_tolerance` | `15` | Encoder vs stepper slip warning threshold (%) |
| `sensor_polling_delay` | `0.2` | Seconds between sensor checks in feed loops |
| `servo_move_delay` | `0.3` | Seconds to wait after servo command |
| `stepper_timeout` | `120` | Seconds before idle steppers auto-disable |
| `selector_max_travel` | `200.0` | Max mm for selector far-end detection / cal sweep |
| `selector_homing_speed` | `50.0` | Homing approach speed (mm/s) |
| `selector_homing_backoff` | `5.0` | Back-off distance before slow re-approach (mm) |
| `selector_stall_current` | `0.4` | Motor current (A) during SA_CALIBRATE_SELECTOR sweep — lower than run_current |
| `selector_stall_threshold` | `3` | TMC5160 SGT value for stallguard sensitivity (raise to reduce false triggers) |
| `selector_stall_speed` | `50.0` | Sweep speed (mm/s) during SA_CALIBRATE_SELECTOR |

---

## Sensor wiring

### Entry sensors (`^!autoloader:SA_ENTRY_N`)
Pull-up + invert — sensors read HIGH (active) when empty on this hardware. If your sensors read the opposite, remove the `!` from `switch_pin`.

### Toolhead sensors (`^etN:PB5`)
One per BTT EBB36 toolhead board. Pull-up only (not inverted). Detects filament at nozzle end of Bowden tube.

### Extruder entry sensors (`^etN:PB8`)
One per BTT EBB36 toolhead board. Detects filament arriving at extruder gears. Enables sensor-based load targeting (more reliable than a fixed tube length). Required for `SA_CALIBRATE_BOWDEN`.

---

## Stepper idle timeout

All steppers are automatically disabled after `stepper_timeout` seconds (default 120s) following the last move. This prevents heat build-up and motor noise. The timeout is reset on every motion command and cancelled immediately when a new move starts.

To disable auto-timeout behavior, set `stepper_timeout: 0` in `hardware.cfg`. Note: this is a per-extra timeout independent of Klipper's main `[idle_timeout]`.

---

## Troubleshooting

### Encoder shows no motion during load
- Check filament is actually past the drive gear engagement point.
- Run `SA_ENGAGE` manually then push filament by hand while watching `SA_ENCODER_WATCH TOOL=N`.
- Verify `mm_per_pulse` is non-zero in `[sa_encoder N]`.
- Run `SA_CALIBRATE_ENCODER TOOL=N` to re-calibrate.

### Selector does not home
- Confirm endstop wiring: `QUERY_ENDSTOPS` should show `sa_selector:open` when clear and `sa_selector:TRIGGERED` when pressed.
- Check `endstop_pin` in `[manual_stepper sa_selector]`.
- Reduce `selector_homing_speed` if the carriage overshoots.

### Filament not reaching extruder
- Check `bowden_length_N` matches your actual tube length.
- If extruder sensor is wired, run `SA_CALIBRATE_BOWDEN TOOL=N`.
- Watch `SA_ENCODER_WATCH` during a manual load to see where motion stops.

### Slip warnings during load
- Increase `engage_max_distance` if warnings appear early in the load.
- Check that the servo engaged angle actually grips the filament (`SA_ENGAGE` then try to pull filament by hand).
- Run `SA_CALIBRATE_DRIVE` to verify `rotation_distance` is accurate.
- Reduce `feed_speed` for the initial Bowden feed.

### SA_RESPOND timeout during calibration
- The calibration routine waits 300 seconds (5 minutes) by default for `SA_RESPOND`.
- If you miss the window, re-run the calibration command.
- Calibration can always be aborted mid-sequence with `SA_RESPOND VALUE=abort`.
