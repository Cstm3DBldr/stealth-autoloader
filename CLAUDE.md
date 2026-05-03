# Stealth Autoloader — Claude Code Project Instructions

## What This Project Is
A filament auto-load and auto-unload system for a Voron StealthChanger 3D printer
with 6 independent toolheads. Each toolhead has its own filament path; this system
automates loading when a roll runs out and swapping filament color between prints.

This is a NEW original project. It is NOT a port or fork of any existing project.

**Use case:** Multi-toolhead printer. Tool changes are handled by the toolchanger
(mechanical head swap). This system only acts when:
- A roll runs out → auto-load new filament to that toolhead
- Manual swap between prints → unload old, load new filament

**NOT an MMU:** Filament never leaves the path during a tool change. No gate
selector that moves to filament. No color changes mid-print on a single toolhead.

---

## Printer Access
- SSH:         pi@192.168.1.214
- Config path: ~/printer_data/config/stealth-autoloader/
- Repo path:   ~/stealth-autoloader/

## GitHub
- Repo: https://github.com/Cstm3DBldr/stealth-autoloader.git
- Branch: main
- Commit and push after any change that works on the printer

## Operational Permissions (set by user)
Claude has full autonomous control of this printer and repository. No need to ask
before deploying or pushing — just do it and report the result.

- **Deploy after every code change** — SCP files, restart Klipper, verify it loads.
- **Commit and push to main** after every successful deploy.
- **This is a spare/test printer** — mechanical risk is acceptable for calibration and testing.
- **Update README.md** whenever commands, config parameters, or calibration procedures change.
- **Update CLAUDE.md** whenever the user adds new rules, preferences, or project context.
- **No confirmation prompts needed** for SCP, SSH restart, git commit, or git push.

---

## Project File Structure

| File | Purpose |
|---|---|
| `stealth-autoloader/stealth-autoloader.cfg` | Aggregator. printer.cfg pulls only this file: `[include stealth-autoloader/stealth-autoloader.cfg]`. Pulls in the others below |
| `stealth-autoloader/pin_aliases.cfg` | ONLY physical hardware pins and aliases. One [board_pins] per MCU. No polarity, no hardware config |
| `stealth-autoloader/hardware.cfg` | ONLY hardware sections: [mcu], [tmc5160], [manual_stepper], [servo], [sa_encoder], [filament_switch_sensor], [gcode_button selector_stall] |
| `stealth-autoloader/parameters.cfg` | The single `[stealth_autoloader]` section — all user-tunable values (servo angles, speeds, tip-form, park, selector cal, bowden lengths, sensor/encoder/extruder/stepper references). Klipper requires the section in one file |
| `stealth-autoloader/macros.cfg` | Thin gcode wrappers around Python backend commands |
| `klipper/extras/stealth_autoloader.py` | Main controller — config parsing, GCode registration, status object |
| `klipper/extras/sa_motion.py` | Motion primitives (servo, selector, drive, idle timeouts) |
| `klipper/extras/sa_sequences.py` | Load and unload sequences |
| `klipper/extras/sa_calibration.py` | All calibration routines (drive, encoder, selector, bowden) |
| `klipper/extras/sa_encoder.py` | Encoder driver — pulse counting via Klipper buttons module |
| `moonraker/sa_moonraker.py` | Moonraker component — REST endpoints + status broadcast |
| `web/mainsail/AutoloaderPanel.vue` | Mainsail UI panel |
| `web/fluidd/AutoloaderPanel.vue` | Fluidd UI panel |
| `KlipperScreen/panels/sa_*.py` | KlipperScreen touchscreen panels |
| `KlipperScreen/sa_filament_db.py` | Filament profile DB loader (shared with Moonraker) |
| `filaments/brands/*.cfg` | Per-brand filament profile files |
| `References/hardware_pinouts/` | Board pinout images — local + GitHub only, NOT on printer |
| `CLAUDE.md` | This file |

On the printer, Python extras and the Moonraker component are symlinked from the repo:
- `~/klipper/klippy/extras/stealth_autoloader.py` → `~/stealth-autoloader/klipper/extras/stealth_autoloader.py`
- `~/klipper/klippy/extras/sa_motion.py` → `~/stealth-autoloader/klipper/extras/sa_motion.py`
- `~/klipper/klippy/extras/sa_sequences.py` → `~/stealth-autoloader/klipper/extras/sa_sequences.py`
- `~/klipper/klippy/extras/sa_calibration.py` → `~/stealth-autoloader/klipper/extras/sa_calibration.py`
- `~/klipper/klippy/extras/sa_encoder.py` → `~/stealth-autoloader/klipper/extras/sa_encoder.py`
- `~/moonraker/moonraker/components/sa_moonraker.py` → `~/stealth-autoloader/moonraker/sa_moonraker.py`

KlipperScreen panels are NOT symlinked — copy directly to `~/KlipperScreen/panels/` and `~/KlipperScreen/`.

---

## Motion System Architecture

ERCF V2 mechanical concept, adapted for fixed multi-toolhead use:

```
[Filament Roll 0]  [Roll 1]  ...  [Roll N]
        |               |                |
   Entry Sensor 0  Entry Sensor 1  Entry Sensor N
        |               |                |
        └───────────────┴────────────────┘
                        |
                  Selector Motor
                  (positions carriage)
                        |
                  Drive Gear ←── Engage Servo ──► ENGAGED (driven)
                        |                          DISENGAGED (neutral)
                  Drive Encoder
                  (single, on drive gear output shaft)
                        |
               PTFE Tube (selected path)
                        |
               Extruder Motor (toolhead)
                        |
               Hotend + Nozzle
```

### Components
| Component | Klipper Object | Role |
|---|---|---|
| Selector motor | `manual_stepper sa_selector` (M2) | Positions drive carriage to active path |
| Drive motor | `manual_stepper sa_drive` (M1) | Moves filament through selected path |
| Engage servo | `servo sa_engage` | Engages (driven) or releases (neutral) drive gear |
| Drive encoder | `sa_encoder` | Single encoder on drive gear shaft; measures all movement |
| Entry sensors | `filament_switch_sensor entry_sensor_N` | Per-path; fixed position at roll end |

### Engage vs Neutral
- **Engaged** (servo at `servo_engaged_angle`) — drive gear grips filament; drive motor moves it
- **Neutral** (servo at `servo_disengaged_angle`) — drive gear releases; filament flows freely
  ("neutral" like a car transmission — no force transmitted)

---

## MCU Layout

| MCU name | Board | Role | UUID |
|---|---|---|---|
| `mcu` | BTT Manta M8P | Main printer (in printer.cfg — do not redefine) | — |
| `autoloader` | BTT MMB CAN V2.0 | All 6 paths on one board | 329ce333239a |

---

## Pin Assignments — BTT MMB CAN V2.0 (`autoloader`)

| Alias | Physical Pin | Role |
|---|---|---|
| SA_DRIVE_STEP/DIR/EN | M3: PC15/PC11/PC10 | Drive motor step/dir/enable |
| SA_DRIVE_CS | PB3 | Drive motor TMC5160 SPI chip-select |
| SA_SELECTOR_STEP/DIR/EN | M1: PD4/PD3/PD5 | Selector motor step/dir/enable |
| SA_SELECTOR_CS | PB5 | Selector motor TMC5160 SPI chip-select |
| SA_SELECTOR_STOP | PA15 (STOP1) | Selector physical endstop (switch) |
| SA_SERVO | PA1 | Engage servo PWM signal |
| SA_ENCODER_0..5 | PC7,PA9,PB12,PB10,PB1,PC5 | Per-path encoders (2x7 high pins) |
| SA_ENTRY_0..5 | PC6,PA8,PB11,PB2,PB0,PC4 | Entry sensors (2x7 low pins) |
| SA_SELECTOR_DIAG | PB9 | TMC5160 stallguard DIAG (M1 selector) — required for SA_CALIBRATE_SELECTOR via `[gcode_button selector_stall]` |
| SA_DRIVE_DIAG | PB7 | TMC5160 stallguard DIAG (M3 drive) — reserved |

**TMC5160 SPI bus** (shared M1+M2): software SPI — MISO=PB14, MOSI=PB15, SCK=PB13
`sense_resistor: 0.075` — hardware SPI (spi_bus: spi1/spi2) fails on this MCU; software SPI required.

Entry sensors use `^!` (pull-up + invert) because the sensors read HIGH when empty on this hardware.

**BTT EBB36 toolhead sensor pins (per toolhead MCU `etN`):**
| Pin | Sensor | Role |
|---|---|---|
| `^etN:PB8` | `toolhead_sensor_N` | Filament past extruder gears, entering hotend (final load confirmation) |
| `^etN:PB5` | `extruder_sensor_N` | Filament at toolhead entry, before extruder gears (Bowden calibration endpoint) |

---

## Klipper Config Rules — Strict

1. `[board_pins]` block name MUST exactly match the `[mcu name]`. Wrong name → "Unknown pin chip name" error.
2. Pin polarity (`^` pull-up, `!` invert) goes in hardware.cfg on the USE line — never in pin_aliases.cfg.
3. Last alias entry in a `[board_pins]` block has NO trailing comma.
4. Include order is fixed: pin_aliases.cfg → hardware.cfg → parameters.cfg → macros.cfg. (parameters.cfg references hardware sections so it must come after hardware.cfg.)
5. Never duplicate an `[mcu]` section — main `[mcu]` lives in printer.cfg only.
6. Step pins on secondary MCU: do NOT use `^` prefix — step pins are outputs. `^chip:pin` fails; `chip:pin` works.
7. Input pins (sensors, endstops): `^!autoloader:SA_ENTRY_0` works — `^` and `!` are stripped before chip name lookup.

---

## Python Backend — stealth_autoloader.py

Single `[stealth_autoloader]` config section, single class instance, controls everything.

### Config Parameters

| Parameter | Default | Description |
|---|---|---|
| `drive_stepper` | required | manual_stepper name for drive motor |
| `selector_stepper` | required | manual_stepper name for selector |
| `servo` | required | servo object name for engage/disengage |
| `encoder_N` | `sa_encoder N` | Per-path encoder section name |
| `num_paths` | 6 | Number of filament paths (1–32) |
| `entry_sensor_N` | none | filament_switch_sensor name for entry sensor on path N |
| `extruder_sensor_N` | none | filament_switch_sensor at extruder gear entry on path N (required for SA_CALIBRATE_BOWDEN, sensor-terminated load) |
| `toolhead_sensor_N` | none | filament_switch_sensor past gears, before nozzle on path N (final load confirmation) |
| `selector_position_N` | N×21mm | Selector position in mm from home for path N (set by SA_CALIBRATE_SELECTOR) |
| `extruder_N` | extruder / extruderN | Extruder name for heating during load |
| `bowden_length_N` | 800.0 | Per-path Bowden tube length (mm); set by SA_CALIBRATE_BOWDEN. Replaces global `tube_length`. |
| `servo_engaged_angle` | 30 | Servo angle when drive gear grips filament |
| `servo_disengaged_angle` | 160 | Servo angle when path is neutral |
| `tube_length` | 800 | Legacy fallback Bowden length used if `bowden_length_N` not set |
| `nozzle_distance` | 50 | Extruder gears → nozzle tip (mm) |
| `purge_length` | 30 | Extra extrusion after nozzle loaded (mm) |
| `load_temperature` | 200 | Min hotend temp before extruding (°C) |
| `load_park_z` | 50.0 | Z height held throughout load/unload + park (mm) |
| `engage_max_distance` | 60 | Max drive travel before expecting encoder motion (mm) |
| `slip_tolerance` | 15 | Warn if encoder vs stepper differ by > this % |
| `feed_speed` | 50 | Drive motor speed (mm/s) |
| `selector_speed` | 200 | Selector motor speed (mm/s) |
| `feed_step_size` | 10 | Drive motor step per loop iteration (mm) |
| `sensor_polling_delay` | 0.2 | Seconds between sensor checks in loops |
| `servo_move_delay` | 0.3 | Seconds to wait after servo command |
| `stepper_timeout` | 120 | Idle stepper auto-disable seconds; 0 disables auto-disable |
| `cooling_pad_enabled` | True | Call `PARK_ON_COOLING_PAD` after load/unload |
| `clean_nozzle_enabled` | True | Call `SA_CLEAN_NOZZLE` before parking |
| `selector_max_travel` | 200.0 | Max sweep distance (mm) for SA_CALIBRATE_SELECTOR auto-cal |
| `selector_homing_speed` | 50.0 | Selector home approach speed (mm/s) |
| `selector_homing_backoff` | 5.0 | Back-off mm before slow re-approach in double-touch home |
| `selector_stall_current` | 0.4 | Reduced motor current (A) during stallguard sweep — bumping hard stop is harmless |
| `selector_stall_threshold` | 3 | TMC5160 SGT value for stallguard sensitivity (raise to reduce false triggers) |
| `selector_stall_speed` | 50.0 | Sweep speed (mm/s) during stallguard cal |

### GCode Commands (all registered by Python)

| Command | Description |
|---|---|
| `SA_HOME` | Home selector to physical endstop (double-touch), zero position |
| `SA_SELECT TOOL=N` | Move selector to path N (servo stays neutral) |
| `SA_ENGAGE` | Engage drive servo (grip filament) |
| `SA_DISENGAGE` | Disengage drive servo (neutral) |
| `SA_LOAD TOOL=N` | Full load sequence for path N |
| `SA_UNLOAD TOOL=N` | Full unload sequence for path N |
| `SA_STATUS` | Print state for all paths |
| `SA_BUZZ_DRIVE` | Test drive motor |
| `SA_BUZZ_SELECTOR` | Test selector motor |
| `SA_CALIBRATE_SELECTOR` | Auto sweep + measure total travel → calculate path positions |
| `SA_CALIBRATE_DRIVE` | Interactive drive motor rotation_distance calibration |
| `SA_CALIBRATE_ENCODER TOOL=N` | Measure mm_per_pulse for encoder N |
| `SA_CALIBRATE_BOWDEN TOOL=N` | Measure Bowden tube length for path N |
| `SA_ENCODER_QUERY [TOOL=N] [RESET=1]` | Snapshot encoder distances |
| `SA_ENCODER_WATCH [TOOL=N] [DURATION=30] [INTERVAL=0.5]` | Live encoder delta stream |
| `SA_RESPOND VALUE=x` | Advance active calibration to next phase |
| `SA_SET_STATE TOOL=N STATE=<state>` | Override path state (loaded/empty/partial/unknown) |
| `SA_SET_MATERIAL TOOL=N MATERIAL=… BRAND=… LINE=… COLOR_NAME=… COLOR_HEX=… LOAD_TEMP=… UNLOAD_TEMP=… PURGE_SPEED=… PURGE_LENGTH=…` | Store filament profile for a path; consumed by load sequence and exposed via web/touchscreen UIs |

### Load Sequence

```
SA_LOAD TOOL=N
↓ Check entry_sensor_N — filament present?
↓ _select_path(N) → servo disengage → selector move → (update current_path)
↓ _servo_engage()
↓ encoder.set_direction(forward=True), encoder.reset_distance()
↓ Phase 1: feed +step until encoder moves (engage_max_distance limit)
↓ Phase 2: feed until encoder >= tube_length (slip check each step)
↓ _servo_disengage()
↓ TEMPERATURE_WAIT extruder_N >= load_temperature
↓ G1 E{nozzle_distance} F300 (extruder drives filament to nozzle)
↓ G1 E{purge_length} F300
↓ _CLEAN_NOZZLE → PARK_ON_COOLING_PAD
↓ path_states[N] = 'loaded'
```

### Unload Sequence

```
SA_UNLOAD TOOL=N
↓ G1 E-{nozzle_distance + purge_length} F300 (retract from nozzle)
↓ _select_path(N) → selector move
↓ _servo_engage()
↓ encoder.set_direction(forward=False), encoder.reset_distance()
↓ Drive -step until entry_sensor_N == False
↓ _servo_disengage()
↓ path_states[N] = 'empty'
```

---

## Deploy Workflow

```bash
# 1. Klipper config files
scp stealth-autoloader/hardware.cfg               pi@192.168.1.214:~/printer_data/config/stealth-autoloader/
scp stealth-autoloader/pin_aliases.cfg            pi@192.168.1.214:~/printer_data/config/stealth-autoloader/
scp stealth-autoloader/parameters.cfg             pi@192.168.1.214:~/printer_data/config/stealth-autoloader/
scp stealth-autoloader/macros.cfg                 pi@192.168.1.214:~/printer_data/config/stealth-autoloader/
scp stealth-autoloader/stealth-autoloader.cfg     pi@192.168.1.214:~/printer_data/config/stealth-autoloader/

# 2. Klipper Python extras (to repo copy — symlinks pick it up)
scp klipper/extras/stealth_autoloader.py  pi@192.168.1.214:~/stealth-autoloader/klipper/extras/
scp klipper/extras/sa_motion.py           pi@192.168.1.214:~/stealth-autoloader/klipper/extras/
scp klipper/extras/sa_sequences.py        pi@192.168.1.214:~/stealth-autoloader/klipper/extras/
scp klipper/extras/sa_calibration.py      pi@192.168.1.214:~/stealth-autoloader/klipper/extras/
scp klipper/extras/sa_encoder.py          pi@192.168.1.214:~/stealth-autoloader/klipper/extras/

# 3. Moonraker component (to repo copy — symlink picks it up)
scp moonraker/sa_moonraker.py  pi@192.168.1.214:~/stealth-autoloader/moonraker/

# 4. KlipperScreen panels (NOT symlinked — direct copy to KS install)
scp KlipperScreen/panels/sa_*.py        pi@192.168.1.214:~/KlipperScreen/panels/
scp KlipperScreen/sa_*.py               pi@192.168.1.214:~/KlipperScreen/
scp KlipperScreen/sa_klipperscreen.conf pi@192.168.1.214:~/printer_data/config/

# 5. Web UI panels (Mainsail/Fluidd) — install path depends on host setup; user-managed.
#    web/mainsail/AutoloaderPanel.vue
#    web/fluidd/AutoloaderPanel.vue

# 6. Restart services (Moonraker only if sa_moonraker.py changed)
ssh pi@192.168.1.214 "echo pi | sudo -S systemctl restart klipper"
ssh pi@192.168.1.214 "echo pi | sudo -S systemctl restart moonraker"

# 7. Commit and push
git add -A && git commit -m "..." && git push origin main
```

**Important:** Always SCP Python extras to `~/stealth-autoloader/klipper/extras/` (the repo copy), not to `~/klipper/klippy/extras/` directly — those should be symlinks. Same for the Moonraker component.

On first install, create symlinks:
```bash
ln -sf ~/stealth-autoloader/klipper/extras/stealth_autoloader.py  ~/klipper/klippy/extras/stealth_autoloader.py
ln -sf ~/stealth-autoloader/klipper/extras/sa_motion.py           ~/klipper/klippy/extras/sa_motion.py
ln -sf ~/stealth-autoloader/klipper/extras/sa_sequences.py        ~/klipper/klippy/extras/sa_sequences.py
ln -sf ~/stealth-autoloader/klipper/extras/sa_calibration.py      ~/klipper/klippy/extras/sa_calibration.py
ln -sf ~/stealth-autoloader/klipper/extras/sa_encoder.py          ~/klipper/klippy/extras/sa_encoder.py
ln -sf ~/stealth-autoloader/moonraker/sa_moonraker.py             ~/moonraker/moonraker/components/sa_moonraker.py
```

---

## Calibration Sequence (first-time setup)

1. **Flash and connect** the BTT MMB CAN V2.0 board.
2. **Update `canbus_uuid`** in `hardware.cfg`.
3. **Test motors:** `SA_BUZZ_DRIVE` then `SA_BUZZ_SELECTOR` — confirm both move.
4. **Test servo:** `SA_ENGAGE` then `SA_DISENGAGE` — confirm servo moves.
5. **Home selector:** `SA_HOME` — confirm endstop triggers and carriage returns.
6. **Calibrate selector:** `SA_CALIBRATE_SELECTOR` — auto-calculates path positions via stallguard sweep.
7. **Load filament** on path 0 past the drive gear.
8. **Calibrate drive motor:** `SA_CALIBRATE_DRIVE` — sets `rotation_distance`.
9. **Calibrate encoders:** `SA_CALIBRATE_ENCODER TOOL=N` for each path.
10. **Calibrate Bowden lengths:** `SA_CALIBRATE_BOWDEN TOOL=N` for each path (requires extruder sensors).
11. **Test full load:** `SA_LOAD TOOL=0` — verify complete sequence.

---

## Happy Hare — Reference Rules

Happy Hare (https://github.com/moggieuk/Happy-Hare) is referenced for:
- ERCF V2 mechanical topology (selector + drive gear + servo)
- Encoder calibration concept (mm_per_pulse, single encoder for all paths)
- Config file style and Python extra class patterns

Do NOT copy Happy Hare code. This project does not need:
- Gate/selector that moves encoder position
- Tip forming, spoolman, LED, servo retract sequences
- Multi-color on single toolhead (it's a multi-toolhead printer)
- Any MMU-specific logic

If code resembles Happy Hare too closely, simplify it for single-path-per-tool architecture.

---

## What Not To Do

- Do not modify printer.cfg, klipper-toolchanger, or core klipper files
- Do not put load/unload sequences in macros — they live in stealth_autoloader.py
- Do not add per-path feed motors — there is ONE drive motor for all paths
- Do not use `^` before a chip name on output pins (step pins) — only valid on input pins
- Do not define `[mcu autoloader]` in printer.cfg — it's in hardware.cfg
- Do not add trailing comma to last alias in `[board_pins]`
- Do not SCP `References/` folder to printer
- Do not create separate `[filament_feed toolN]` sections — replaced by `[stealth_autoloader]`
- Do not use blocking `reactor.pause()` poll loops to wait for SA_RESPOND — the GCode mutex blocks it. Use the state machine in SACalibration instead.
- Do not add "are you ready?" confirmation prompts — user initiated the command, that is confirmation enough.
- Do not add sensorless/stallguard homing — homing is physical endstop only (SA_SELECTOR_STOP / PA15). The endstop pin is always `^!autoloader:SA_SELECTOR_STOP`.

## Console Output Rules

- **Every command must be in its own individual code block** — never combine multiple commands in one block.
- This applies to all responses: GCode commands, bash commands, test steps, calibration sequences, deploy instructions.
- All SA_RESPOND prompts must be on their own clearly separated lines so the user can copy-paste without typos.
- Use `_prompt(gcmd, message, *commands)` helper in SACalibration — it formats commands with leading spaces on their own lines.
- Print calibration phase progress as plain text (no extra decoration needed).

## Calibration Architecture

Calibration uses a non-blocking phase state machine:
- `owner._cal_state` (str | None): current phase key, e.g. `'sel_confirm'`, cleared on Klipper restart
- `owner._cal_data` (dict): data bag passed between phases (positions, measurements, attempt counts, etc.)
- `SA_RESPOND VALUE=x` calls `calibration.respond(gcmd, value)` which dispatches to the correct `_*_respond()` handler
- Each phase runs to completion (no blocking waits) and either finishes or sets the next state + prompts
- `SA_RESPOND VALUE=abort` always cancels and clears state
- State is automatically cleared on Klipper restart — no risk of waking up mid-calibration after a power cycle
