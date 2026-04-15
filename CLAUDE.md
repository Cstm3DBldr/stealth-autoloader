# Stealth Autoloader — Claude Code Project Instructions

## What This Project Is
A filament auto-load and auto-unload system for a Voron StealthChanger 3D printer
with 6 independent toolheads. Each toolhead has its own complete filament path with
dedicated motors and sensors. This project bolts on top of klipper-toolchanger as a
clean add-on — no core klipper or toolchanger files are modified.

This is a NEW original project. It is NOT a port or fork of any existing project.

---

## Printer Access
- SSH:         pi@192.168.1.214
- Config path: ~/printer_data/config/stealth-autoloader/
- Repo path:   ~/stealth-autoloader/

## GitHub
- Repo: https://github.com/Cstm3DBldr/stealth-autoloader.git
- Branch: main
- Always commit and push after any change that works on the printer

---

## Project File Structure

| File | Purpose |
|---|---|
| `stealth-autoloader/stealth-autoloader.cfg` | Main entry point — only file included in printer.cfg |
| `stealth-autoloader/pin_aliases.cfg` | ONLY place real pin names are entered. One [board_pins] block per MCU |
| `stealth-autoloader/hardware.cfg` | MCU definitions + all stepper/sensor/encoder/filament_feed sections |
| `stealth-autoloader/macros.cfg` | User-facing gcode macros. Thin wrappers that call the Python backend |
| `stealth-autoloader/parameters.cfg` | Reference doc for all tunable variable names and what they do |
| `klipper/extras/filament_feed.py` | Python backend. All load/unload logic lives here |
| `klipper/extras/sa_encoder.py` | Rotary encoder (Binky-style) driver. Counts pulses → mm |
| `References/hardware_pinouts/` | Pinout images and README for all boards — local + GitHub only, NOT on printer |
| `install.sh` | Install and uninstall script |
| `post_update.sh` | Run by moonraker update manager after a git pull |
| `README.md` | End-user install and config guide — keep this updated |
| `CLAUDE.md` | This file — project instructions for Claude Code |

---

## Hardware Per Filament Path (x6 toolheads)

Each toolhead has this exact hardware chain — NO buffer, NO mid-path switches:

```
[Filament Roll]
      |
Entry Sensor        — filament_switch_sensor, detects filament inserted at roll end
      |
Feed Motor          — manual_stepper (BTT MMB CAN V2.0 M1/M2/M3)
      |
Binky Encoder       — sa_encoder, single-pulse hall-effect sensor just after feed motor
      |               tracks actual filament movement in mm (mm_per_pulse × pulse count)
Long PTFE Tube      — up to 1 meter
      |
Extruder Motor      — on the toolhead, driven by klipper extruder config
      |
Hotend + Nozzle
```

The encoder replaces ALL mid-path sensors (extruder sensor, toolhead sensor,
buffer tension/compression). Distance to any point in the path is tracked by
counting encoder pulses × mm_per_pulse.

---

## MCU Layout

| MCU name | Board | Tools | UUID / Serial |
|---|---|---|---|
| `mcu` | BTT Manta M8P | Main printer | defined in printer.cfg — do not redefine here |
| `autoloader_a` | BTT MMB CAN V2.0 | Tools 0, 1, 2 | canbus_uuid: 329ce333239a |
| `autoloader_b` | BTT MMB CAN V2.0 | Tools 3, 4, 5 | second board pending arrival |

---

## Pin Assignments — BTT MMB CAN V2.0

Each board has identical hardware. autoloader_a = Tools 0-2, autoloader_b = Tools 3-5.

### Feed motors
| Driver | Tool (board A) | Tool (board B) | STEP | DIR | EN | UART |
|---|---|---|---|---|---|---|
| M1 | T0 | T3 | PD4 | PD3 | PD5 | PB5 |
| M2 | T1 | T4 | PC9 | PC8 | PD2 | PB4 |
| M3 | T2 | T5 | PC15 | PC11 | PC10 | PB3 |

### 2×7 Header — entry sensor (low pin) + encoder (high pin)
| Tool | Board | Entry Pin | Encoder Pin |
|---|---|---|---|
| T0 | autoloader_a | PC6 | PC7 |
| T1 | autoloader_a | PA8 | PA9 |
| T2 | autoloader_a | PB11 | PB12 |
| T3 | autoloader_b | PB2 | PB10 |
| T4 | autoloader_b | PB0 | PB1 |
| T5 | autoloader_b | PC4 | PC5 |

---

## Klipper Config Rules — Strict

These rules must never be broken:

1. `[board_pins]` requires ONE block per MCU. The block name AND the `mcu:` value
   must exactly match a real `[mcu name]` section. This is the source of the
   "Unknown pin chip name" error if violated.

2. Pin polarity (`^` pull-up, `!` invert) goes in hardware.cfg on the line that
   USES the pin — never inside the alias definition in pin_aliases.cfg.

3. The last alias entry in each `[board_pins]` block has NO trailing comma.
   Every other alias line has a comma. A trailing comma on the last entry
   will cause a klipper parse error.

4. Include order in stealth-autoloader.cfg is fixed:
   pin_aliases.cfg must load before hardware.cfg (aliases must exist before use).

5. Never duplicate an [mcu] section. The main [mcu] lives in printer.cfg only.

6. All tunable distance and timing values live inside the [filament_feed toolX]
   section in hardware.cfg — not scattered across separate files.

7. The `[board_pins autoloader_b]` block in pin_aliases.cfg will fail to parse if
   the `[mcu autoloader_b]` section is commented out in hardware.cfg. Keep both
   commented out or both active together.

---

## Variable Reference
These are the configurable values inside each [filament_feed toolX] section.

| Variable | Unit | Description |
|---|---|---|
| `tube_length` | mm | Encoder distance target for "filament reached extruder" |
| `sensor_polling_frequency` | sec | Delay between encoder/sensor checks in feed loops |
| `nozzle_distance` | mm | Extruder gears → nozzle tip (extruded after heating) |
| `purge_length` | mm | Extra extrusion after nozzle is loaded |
| `load_temperature` | °C | Minimum hotend temp before any extrusion is attempted |
| `feed_step_size` | mm | Feed motor movement per loop iteration |
| `extruder_step_size` | mm | Extruder movement per loop iteration |
| `engage_max_distance` | mm | Max feed before expecting encoder motion (jam detect) |
| `slip_tolerance` | % | Warn if encoder vs stepper differ by more than this % |

Encoder-specific (per [sa_encoder toolX]):

| Variable | Unit | Description |
|---|---|---|
| `mm_per_pulse` | mm | Filament mm per encoder pulse — calibrate with SA_CALIBRATE_ENCODER |

---

## Load Sequence (encoder-based — source of truth)

```
Entry sensor triggers OR user calls FILAMENT_LOAD TOOL=toolX
↓
Print: "Loading Filament — toolX"
Enable feed motor, set encoder direction=forward, reset encoder
↓
--- Phase 1: Engage ---
LOOP: feed +feed_step_size → check encoder distance
repeat until encoder_distance >= 3 × mm_per_pulse  OR  fed >= engage_max_distance
If max distance reached with no encoder motion → ERROR (no filament / encoder fault)
↓
Print: "Filament engaged — feeding to extruder..."
↓
--- Phase 2: Feed to extruder ---
LOOP: feed +feed_step_size → check encoder distance → check slip
repeat until encoder_distance >= tube_length
↓
Print: "Filament at extruder — Xmm fed (encoder: Ymm)"
↓
--- Phase 3: Heat and extrude ---
TEMPERATURE_WAIT SENSOR=extruder MINIMUM=load_temperature
Print: "Feeding through extruder to nozzle tip..."
G1 E{nozzle_distance} F300 (relative mode M83)
↓
--- Phase 4: Purge ---
Print: "Purging nozzle..."
G1 E{purge_length} F300
↓
_CLEAN_NOZZLE
PARK_ON_COOLING_PAD
Print: "LOAD COMPLETE — toolX"
```

---

## Unload Sequence

```
User calls FILAMENT_UNLOAD TOOL=toolX
↓
Print: "Unloading Filament — toolX"
M83 → G1 E-{nozzle_distance + purge_length} F300 (retract from nozzle)
↓
Enable feed motor, set encoder direction=reverse, reset encoder
↓
LOOP: feed -feed_step_size → check entry sensor
repeat until entry_sensor == False (filament cleared)
↓
Print: "UNLOAD COMPLETE — toolX (encoder: Xmm retracted)"
```

---

## Calibration Commands

| Command | Purpose |
|---|---|
| `SA_BUZZ_M1` | Buzz M1 driver (PD4/PD3/PD5) — confirm which driver your wiring uses |
| `SA_BUZZ_M3` | Buzz M3 driver (PC15/PC11/PC10) — confirm which driver your wiring uses |
| `SA_CALIBRATE_ENCODER TOOL=tool0` | Instructions + `SA_CALIBRATE_ENCODER_RUN` to measure mm_per_pulse |
| `SA_CALIBRATE_ENCODER_RUN TOOL=tool0 DISTANCE=100` | Feed 100mm, report encoder vs stepper, calculate mm_per_pulse |
| `SA_CALIBRATE_TUBE TOOL=tool0` | Slow-feed to find tube_length — stop when filament reaches extruder |

---

## Gcode Command Structure

| Command | What it does |
|---|---|
| `FILAMENT_LOAD` | Loads on the currently active toolhead |
| `FILAMENT_LOAD_T0` … `T5` | Loads a specific toolhead directly |
| `FILAMENT_UNLOAD` | Unloads the currently active toolhead |
| `FILAMENT_UNLOAD_T0` … `T5` | Unloads a specific toolhead directly |
| `SA_FILAMENT_LOAD TOOL=tool0` | Direct Python backend call (used internally by macros) |
| `SA_FILAMENT_UNLOAD TOOL=tool0` | Direct Python backend call (used internally by macros) |

---

## Python Backend Rules (filament_feed.py + sa_encoder.py)

- One instance of FilamentFeed is created per [filament_feed toolX] section
- One instance of SAEncoder is created per [sa_encoder toolX] section
- Commands are registered as SA_FILAMENT_LOAD / SA_FILAMENT_UNLOAD with a TOOL= parameter
  so all 6 tool instances can coexist without command name conflicts
- All hardware is resolved at runtime via printer.lookup_object() — not at init time
- Encoder uses Klipper's buttons module for interrupt-driven pulse counting
- Feed motor moves use MANUAL_STEPPER gcode via run_script_from_command()
- Extruder moves use G1 E in relative mode (M83)
- All configurable values are read from the [filament_feed] config section
- Do not hardcode any distances, speeds, or temperatures

---

## Deploy Workflow

After making and testing a change:

```bash
# 1. Push changed config files to printer
scp stealth-autoloader/hardware.cfg pi@192.168.1.214:~/printer_data/config/stealth-autoloader/
scp stealth-autoloader/pin_aliases.cfg pi@192.168.1.214:~/printer_data/config/stealth-autoloader/
scp stealth-autoloader/macros.cfg pi@192.168.1.214:~/printer_data/config/stealth-autoloader/
scp stealth-autoloader/stealth-autoloader.cfg pi@192.168.1.214:~/printer_data/config/stealth-autoloader/

# 2. Push Python backend if changed
scp klipper/extras/filament_feed.py pi@192.168.1.214:~/klipper/klippy/extras/
scp klipper/extras/sa_encoder.py    pi@192.168.1.214:~/klipper/klippy/extras/

# 3. Restart Klipper
ssh pi@192.168.1.214 "sudo systemctl restart klipper"

# 4. Commit and push to GitHub
git add -A
git commit -m "description of what changed"
git push origin main
```

Note: References/hardware_pinouts/ is local + GitHub only — never SCP to printer.

---

## Happy Hare — Reference Only

Happy Hare (https://github.com/moggieuk/Happy-Hare) is referenced for:
- Config file style and structure (how variables are named and organized)
- The [board_pins] alias pattern for clean pin management
- The pattern of one Python extra class per hardware unit
- How to keep all user-editable values in .cfg files, not hardcoded in Python
- The Binky encoder concept (single-pulse hall-effect, mm_per_pulse calibration)

Happy Hare is NOT to be copied, ported, or used as a code source.
This project does not need Happy Hare's selector, gate logic, tip forming,
spoolman integration, LED control, or any MMU-specific functionality.
If a solution resembles Happy Hare too closely, rewrite it to be simpler
and specific to this project's single-path-per-tool architecture.

---

## README.md Update Rules

Update README.md whenever:
- A new gcode command is added
- An install or uninstall step changes
- A new config variable is added
- A new hardware component is added to the path
- A sequence (load/unload) changes behavior

Do not update README.md for internal refactors that don't affect the user.

---

## What Not To Do

- Do not modify printer.cfg, klipper-toolchanger files, or any core klipper files
- Do not add buffer/tension/compression sensor sections — those were removed by design
- Do not add functionality from Happy Hare that isn't in the flow chart
- Do not implement state persistence until load/unload works on hardware
- Do not rename variables that are defined in the variable reference table
- Do not split tunable values back out into a separate parameters.cfg — they live in hardware.cfg
- Do not add a trailing comma to the last alias entry in a [board_pins] block
- Do not define [mcu] sections that are already defined in printer.cfg
- Do not SCP References/ folder to printer — it is local + GitHub only
