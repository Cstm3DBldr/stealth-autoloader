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
| `stealth-autoloader/hardware.cfg` | MCU definitions + all stepper/sensor/filament_feed sections for all 6 tools |
| `stealth-autoloader/macros.cfg` | User-facing gcode macros. Thin wrappers that call the Python backend |
| `stealth-autoloader/parameters.cfg` | Reference doc for all tunable variable names and what they do |
| `klipper/extras/filament_feed.py` | Python backend. All load/unload logic lives here |
| `install.sh` | Install and uninstall script |
| `post_update.sh` | Run by moonraker update manager after a git pull |
| `README.md` | End-user install and config guide — keep this updated |
| `CLAUDE.md` | This file — project instructions for Claude Code |

---

## Hardware Per Filament Path (x6 toolheads)

Each toolhead has this exact hardware chain:
[Filament Roll]
|
Entry Sensor        — detects filament inserted at the roll end
|
Feed Motor          — manual_stepper, drives filament toward toolhead
|
Long PTFE Tube      — up to 1 meter
|
Buffer              — spring-loaded sliding mechanism
Tension Sensor    — default state, filament is slack/pulling back
Compression Sensor— filament is pushing forward into extruder gears
|
Extruder Motor      — on the toolhead, driven by klipper extruder config
Extruder Sensor     — confirms filament arrived at extruder gears
|
Toolhead Sensor     — confirms filament passed through extruder
|
Hotend + Nozzle

---

## MCU Layout

- `mcu`          — Main printer MCU (defined in printer.cfg, do not redefine here)
- `autoloader_a` — Extra MCU handling Tools 0, 1, 2 (defined in hardware.cfg)
- `autoloader_b` — Extra MCU handling Tools 3, 4, 5 (defined in hardware.cfg)

If the build ends up with all pins on one extra MCU, remove autoloader_b and
update all references in pin_aliases.cfg. If everything fits on the main MCU,
remove both extra MCU sections and set mcu: to mcu in pin_aliases.cfg.

---

## Klipper Config Rules — Strict

These rules must never be broken:

1. `[board_pins]` requires ONE block per MCU. The `mcu:` value must exactly match
   a real `[mcu name]` section. This is the source of the
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

---

## Variable Reference
These are the configurable values inside each [filament_feed toolX] section.
Variable names come from the flow chart — do not rename them.

| Variable | Unit | Description |
|---|---|---|
| `tube_length` | mm | Feed motor → extruder sensor (full tube run) |
| `sensor_polling_frequency` | sec | Delay between sensor checks in all feed loops |
| `buffer_slide_distance` | mm | Distance between tension state and compression state |
| `extruder_gear_distance` | mm | Extruder gears → toolhead sensor |
| `nozzle_distance` | mm | Toolhead sensor → nozzle tip |
| `purge_length` | mm | Extra extrusion after nozzle is loaded |
| `load_temperature` | °C | Minimum hotend temp before any extrusion is attempted |
| `feed_step_size` | mm | Feed motor movement per loop iteration |
| `extruder_step_size` | mm | Extruder movement per loop iteration |

---

## Load Sequence (from flow chart — this is the source of truth)
Entry sensor triggers OR user calls FILAMENT_LOAD
↓
Print: "Loading Filament"
↓
Enable feed motor
↓
LOOP: feed +feed_step_size mm → wait sensor_polling_frequency → check extruder_sensor
repeat until extruder_sensor == true
↓
LOOP: feed +5mm slow → check buffer_compression_sensor
repeat until buffer_compression_sensor == true
(if tension sensor goes to compression before expected distance — flag as possible jam)
↓
Check hotend temp — if below load_temperature, heat and wait
↓
Print: "Feeding to Toolhead"
↓
Enable extruder motor (active toolhead extruder, relative mode M83)
↓
LOOP: extrude +extruder_step_size mm → wait → check toolhead_sensor
repeat until toolhead_sensor == true
↓
Extrude nozzle_distance mm  (sensor to nozzle tip)
↓
Print: "Purging Nozzle"
↓
Extrude purge_length mm
↓
Call _CLEAN_NOZZLE  (existing working macro — do not rewrite)
↓
Call PARK_ON_COOLING_PAD  (existing working macro — do not rewrite)
↓
Save toolhead state as LOADED
↓
Print: "LOAD COMPLETE"

---

## Unload Sequence
User calls FILAMENT_UNLOAD
↓
Retract nozzle_distance + purge_length mm at controlled speed
↓
LOOP: retract extruder_step_size mm → check extruder_sensor
repeat until extruder_sensor == false
↓
Enable feed motor reverse
↓
LOOP: retract feed_step_size mm → check entry_sensor
repeat until entry_sensor == false
↓
Save toolhead state as UNLOADED
↓
Print: "UNLOAD COMPLETE"

---

## Buffer Monitoring (during print — not yet implemented)

When a toolhead is in LOADED state, the buffer tension/compression sensors must
be monitored continuously. If the buffer goes to TENSION state during a print,
the feed motor must run forward until COMPRESSION is restored.
The distance to feed per correction is buffer_slide_distance.
This will be implemented as a persistent loop in the Python backend.
Do not implement this until the load/unload sequences are confirmed working on hardware.

---

## Toolhead State Persistence

Each toolhead has a LOADED or UNLOADED state that must survive a printer power cycle.
This will use Klipper's save_variables mechanism. The variable names will be:
  toolhead_loaded_t0, toolhead_loaded_t1 ... toolhead_loaded_t5
Do not implement until load/unload is confirmed working on hardware.

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
| `BUFFER_CONTROL_ON` | Placeholder — continuous buffer monitoring, not yet implemented |

---

## Python Backend Rules (filament_feed.py)

- One instance of FilamentFeed is created per [filament_feed toolX] section
- Commands are registered as SA_FILAMENT_LOAD / SA_FILAMENT_UNLOAD with a TOOL= parameter
  so all 6 tool instances can coexist without command name conflicts
- All hardware is resolved at runtime via printer.lookup_object() — not at init time
- Sensor reads use get_status()['filament_detected']
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

# 2. Push Python backend if changed
scp klipper/extras/filament_feed.py pi@192.168.1.214:~/klipper/klippy/extras/

# 3. Restart Klipper
ssh pi@192.168.1.214 "sudo systemctl restart klipper"

# 4. Commit and push to GitHub
git add -A
git commit -m "description of what changed"
git push origin main
```

---

## Happy Hare — Reference Only

Happy Hare (https://github.com/moggieuk/Happy-Hare) is referenced for:
- Config file style and structure (how variables are named and organized)
- The [board_pins] alias pattern for clean pin management
- The pattern of one Python extra class per hardware unit
- How to keep all user-editable values in .cfg files, not hardcoded in Python

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
- A sequence (load/unload/buffer) changes behavior

Do not update README.md for internal refactors that don't affect the user.

---

## What Not To Do

- Do not modify printer.cfg, klipper-toolchanger files, or any core klipper files
- Do not add functionality from Happy Hare that isn't in the flow chart
- Do not implement buffer monitoring or state persistence until load/unload works on hardware
- Do not rename variables that are defined in the flow chart
- Do not split tunable values back out into a separate parameters.cfg — they live in hardware.cfg
- Do not add a trailing comma to the last alias entry in a [board_pins] block
- Do not define [mcu] sections that are already defined in printer.cfg
