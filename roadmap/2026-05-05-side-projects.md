# Autoloader — Roadmap & Side Projects (2026-05-05)

This is a planning document only. Nothing here is deployed to the
printer. Files referenced as `drafts/<name>` exist in the repo for
review but are excluded from `post_update.sh`'s sync targets, so they
cannot accidentally land on the printer until the user moves them
into a deployed location.

---

## 1. Tomorrow's Tracked Tasks

### 1.1 sa_config screen rework

**Current state:** `KlipperScreen/panels/sa_config.py` (311 lines) shows
11 tunable autoloader parameters in a flat scrolling list grouped only
by section header (`SPEEDS`, `DISTANCES`, `TEMPERATURES`, `SERVO`).
Each row has a value column and a pencil edit button. Edit opens a
numpad. `SAVE & RESTART` button at the bottom commits all pending edits
via `SA_SET_CONFIG PARAM=… VALUE=…` then `SAVE_CONFIG`.

**Issues observed in user testing (earlier conversation):**
- Save button could be more prominently positioned
- Inconsistent layout vs the rest of the autoloader UI (sa_macros now
  uses 2-line stacked labels and grouped sections — sa_config is a
  flat list)
- No indicator that a value has been calibrated vs left at default
- No way to revert a single pending change without canceling all

**Proposed direction (open for revision):**
- Section-grouped layout matching sa_macros's three-section pattern,
  but driven by the existing `_PARAMS` data
- Visual cue (asterisk, color tint, or subtle badge) for any value
  that's been changed away from its defined default
- Per-row "revert" button next to the edit pencil
- Pending-changes count badge on the SAVE button so it's obvious how
  many edits are queued
- First-render sizing fix using the same `_install_action_bar_css()`
  pattern from sa_macros (sa_config is also a Stack-based panel; the
  `vexpand` / em-padding gotchas may bite it the same way they bit
  sa_macros if its content size ever changes)

**Effort:** ~half-day. No backend changes required — the gcode
contracts (`SA_SET_CONFIG`, `SAVE_CONFIG`) are stable.

### 1.2 Calibration verification pass — KlipperScreen + Mainsail

After the recent panel rework (sa_macros restructure, sa_home preview
fix), nothing in the `klipper/extras/sa_calibration.py` backend has
changed. But the GUI buttons that drive each calibration have all
been touched. Need to confirm end-to-end that:

| Calibration | KlipperScreen path | Mainsail path | What to verify |
|---|---|---|---|
| `SA_HOME` | Macros → DAILY → HOME SELECTOR | Autoloader panel → Home | Selector double-touches endstop, current_path = 0 |
| `SA_CALIBRATE_SELECTOR` | Macros → CAL → Calibrate Selector | Autoloader panel → Calibrate Selector | Stallguard sweep, `selector_position_N` written to parameters.cfg |
| `SA_CALIBRATE_DRIVE` | Macros → CAL → Calibrate Drive | Calibrate Drive | Prompt + 100 mm extrude, user enters measured, `rotation_distance` written |
| `SA_CALIBRATE_ENCODER_SPEED` | Macros → CAL → Calibrate Encoder Speed | Calibrate Encoder Speed | Speed ramp until encoder slips, `encoder_max_speed` saved |
| `SA_CALIBRATE_ENCODER TOOL=N` | Macros → CAL → Calibrate Encoder → tool picker | Per-path encoder cal | mm-per-pulse measured per path |
| `SA_CALIBRATE_BOWDEN TOOL=N` | Macros → CAL → Calibrate Bowden → tool picker | Per-path bowden cal | Bowden length measured to extruder sensor per path |
| `SA_CAL_PROMPT` flow | Calibration Guide panel walkthrough | Modal prompt in Mainsail | All `SA_RESPOND VALUE=x` transitions work |

Rerun each at least once on real filament. Watch klippy.log for
exceptions, watch the SA Status panel afterward to confirm the
calibrated values stuck. If anything regressed, the suspect is one
of: panel-side gcode emission, the `_pick_tool` flow in sa_macros, or
the popup watcher in `sa_subscription.py` (recently touched).

**Effort:** half-day on a real print session.

---

## 2. Side Projects

### 2.1 Filament respooler for Creality Space Pi X4

**Problem:** during long retracts (autoloader unload sequence, or
typical M600/print-end retracts) the filament dryer doesn't rewind,
so loose filament tangles inside the dryer chamber.

**Existing open-source designs found:**
- [Auto-Rewinder Mod for Creality Space Pi Plus](https://www.printables.com/model/1675067-auto-rewinder-mod-for-creality-space-pi-plus-integ)
  — spring-loaded, no electronics, plugs into the dryer with no
  modifications. Closest fit since you already own the Space Pi line.
  Won't fit X4 directly without measurement, but the spring-tension
  approach scales.
- [LTS Respooler — motorized winder](https://makerworld.com/en/models/448008-lts-respooler-motorized-filament-winder)
  — stepper-driven with a runout sensor as the trigger. More complex
  build but actively rewinds rather than just maintaining tension.
  Could be wired into the printer's existing CAN bus and driven by
  a Klipper `[manual_stepper]` block.
- [Cell Spool Winder](https://makerworld.com/en/models/561571-cell-spool-winder)
  — passive, simpler, less integrated.

**Recommendation:** start with the spring-loaded passive design (low
risk, no electronics, fits inside the existing dryer). If retraction
distance ever exceeds what the spring can absorb (~500 mm of slack),
upgrade to the LTS-style motorized version with a stepper driven from
one of the BTT MMB CAN board's spare driver slots — we have 8 stepper
slots and only use 2 (drive + selector), so 6 spares are available
without new hardware.

**Integration points (if motorized version):**
- Add `[manual_stepper sa_respooler_N]` per path in `hardware.cfg`
  (one shared if all paths feed from the same dryer, otherwise per-path)
- Trigger from autoloader unload sequence after the filament fully
  exits the entry sensor — autoloader.py already knows the moment;
  add a `_respool(path)` step before completion
- Trigger from the per-toolhead retract during pause/M600 — hook
  into `PAUSE` macro

**Effort:** 1 weekend design + print + assembly for passive; +1 day
of wiring and config for motorized.

### 2.2 Toolhead Voron logo LED status

**Hardware (verified on printer):**
```
[neopixel et0_leds]   ← per toolhead, EBB pin PD3
chain_count: 3        ← typical Voron StealthBurner: 1 logo + 2 nozzle
color_order: GRB      ← currently 3-channel; user thought RGBW
```

The string is 3 LEDs, GRB-coded, on each toolhead's EBB module.
Standard Voron StealthBurner mapping is index 0 = logo, indexes 1+2
= nozzle. The user said the LEDs may be RGBW physically — if so,
just flipping `color_order: GRBW` and supplying a `WHITE=` value will
work; the same macro code handles RGB and RGBW transparently because
GRB hardware ignores the WHITE channel.

**Currently:** no status macros exist. The `[neopixel]` blocks have
their `initial_*` values set to white, and nothing changes them. Lights
are constant white whenever the printer is on.

**Proposed macros (drafted in `drafts/sa_status_leds.cfg`, not
deployed):**

| Macro | Effect | Trigger |
|---|---|---|
| `_SA_LED_PARKED TOOL=N COLOR=#xxxxxx` | Logo = filament color, nozzle off | Tool docks on cooling pad / parking position |
| `_SA_LED_UNLOADED TOOL=N` | Logo = dim white (~10%), nozzle off | Path state transitions to `empty` |
| `_SA_LED_ACTIVE TOOL=N` | All 3 LEDs full white | Tool picked up by toolchanger carriage |
| `_SA_LED_LOADING TOOL=N COLOR=#xxxxxx` | Logo pulses filament color, nozzle off | During SA_LOAD sequence |
| `_SA_LED_ERROR TOOL=N` | Logo solid red | Slip detected, sensor mismatch, etc. |

**Integration points:**
- `autoloader.py` already passes color through `SA_SET_MATERIAL`
  (color_hex is stored). Update the load and unload sequences to
  call the appropriate `_SA_LED_*` macro at completion.
- `toolchanger.cfg`'s tool-pickup / tool-park hooks need to call
  `_SA_LED_ACTIVE` and `_SA_LED_PARKED` — those macros are already
  defined by the toolchanger module; we'd add the LED call to them.

**Color handling caveat:** the autoloader stores `color_hex` per tool
(e.g. `#1976D2`). Klipper's `SET_LED` wants 0.0-1.0 floats per channel.
A jinja helper macro `_HEX_TO_RGB` converts on the fly:

```jinja
{% set r = (params.HEX[1:3]|int(base=16)) / 255 %}
{% set g = (params.HEX[3:5]|int(base=16)) / 255 %}
{% set b = (params.HEX[5:7]|int(base=16)) / 255 %}
```

**Effort:** half-day implementation + tuning brightness levels.

### 2.3 Pressure advance database tied to filament profiles

**Goal:** when a tool is loaded with a specific filament profile,
automatically apply the correct pressure advance value for that
extruder + filament combo. Per-tool, per-filament-line.

**Existing PA flow on this printer:** none. PA is set globally via
`SET_PRESSURE_ADVANCE ADVANCE=…` in the start gcode of each print,
or sometimes via M900 from the slicer.

**Proposed architecture:**

1. **Storage:** extend the existing `filaments/brands/<brand>.cfg`
   schema to include a `pressure_advance` value per product line:
   ```
   [filament_line:Polymaker:PolyTerra_PLA]
   load_temp: 215
   unload_temp: 200
   pressure_advance: 0.045   ← NEW field
   ```
   Per-tool override stored in `parameters.cfg` SAVE_CONFIG block:
   ```
   #*# pa_override_T0 = 0.048   # if user calibrated this specific spool on T0
   ```

2. **Apply on load:** `autoloader.py`'s load sequence ends by storing
   the profile. Add a final step that emits
   `SET_PRESSURE_ADVANCE EXTRUDER=extruderN ADVANCE=<value>`,
   reading `pa_override_T0` first, falling back to the profile's
   `pressure_advance` value, falling back to a configured default.

3. **Calibrate per-tool:** new `SA_CALIBRATE_PA TOOL=N` macro that
   prints a standard PA test pattern (Klipper's built-in
   `TUNING_TOWER` or a Frix-x-style line test) and prompts the user
   for the best line number. Store as `pa_override_TN`.

4. **Slicer side:** the slicer should NOT emit `SET_PRESSURE_ADVANCE`
   itself when this system is active, otherwise it'd overwrite our
   value. Document the toggle in the user-facing README.

**Schema draft:** see `drafts/pa_database.cfg`. It's a sample include
file showing the per-line PA values for a few common filaments —
proof of concept, not exhaustive data.

**Effort:** 1 day backend + half-day per-tool calibration UI.

### 2.4 PrusaSlicer filament profile sync

**Goal:** push the printer's currently-loaded filament profiles
(material, color, brand, calibrated PA) to PrusaSlicer so that
multi-tool prints automatically map slicer extruder slots to the
right printer extruders without manual intervention.

**Research findings:**

- **PrusaSlicer has no plugin API.** Issue #3188 confirms there's
  no scripting / extension mechanism, and Prusa explicitly hasn't
  documented one.
- **Vendor bundles ARE auto-updatable.** Per the Prusa wiki on
  [Vendor bundles and updating process](https://github.com/prusa3d/PrusaSlicer/wiki/Vendor-bundles-and-updating-process),
  a vendor `.ini` file with a `config_update_url` field will be
  refreshed by PrusaSlicer on launch. This is the official sync
  mechanism, and it's exactly what we need.
- **Vendor bundle structure:**
  ```
  [vendor]
  name = Voron-Autoloader
  config_version = 0.0.1
  config_update_url = http://192.168.1.214:7125/server/files/vendor/voron_autoloader.ini
  repo_id = non-prusa-fff   ← required since 2.8.0

  [printer_model:Voron2.4]   ← printer profile
  ...

  [filament:Autoloader_T0_Loaded]   ← one entry per loaded path
  filament_settings_id = "Autoloader_T0_Loaded"
  filament_colour = #1976D2          ← matches printer's color_hex
  filament_type = PLA
  filament_vendor = Polymaker
  pressure_advance = 0.045           ← from the pa database
  ```
- **Local storage:** PrusaSlicer reads from
  `AppData/PrusaSlicer/vendor/` on each launch. The user adds the
  printer's vendor URL once via Configuration Wizard → Other
  Vendors. After that, it auto-syncs.

**Architecture (no slicer modifications needed):**

```
Printer Side (Moonraker component)
─────────────────────────────────────
  sa_moonraker.py — already exists, exposes /server/autoloader/*
  + new endpoint:  GET /server/autoloader/prusa_vendor.ini
  + watches autoloader status changes
  + regenerates vendor.ini whenever a profile changes
  + serves it as static file via Moonraker

Slicer Side (one-time setup)
─────────────────────────────────────
  Configuration Wizard → Add Other Vendor
  URL: http://192.168.1.214:7125/server/autoloader/prusa_vendor.ini
  PrusaSlicer launches → auto-fetches latest → filament profiles
    appear in the Filament dropdown, matching exactly what's in
    the toolheads
```

**No fork, no plugin, no rewrite.** The vendor-bundle URL system is
the official Prusa-supported way to do exactly this.

**Caveats:**
- `config_version` in the .ini must monotonically increase or
  PrusaSlicer ignores updates. The Moonraker generator needs to
  increment it on every regeneration.
- User has to re-launch PrusaSlicer for sync to pick up. Not
  real-time but acceptable.
- Multi-color slicing in PrusaSlicer is per-`filament_settings_id`,
  so we need one filament profile per loaded tool, named
  predictably (`Autoloader_T<N>_<material>`).

**Effort:** 2 days for the Moonraker endpoint + vendor.ini
generator + testing. Mostly code in `moonraker/sa_moonraker.py`.

---

## 3. File Drafts (Review-Only)

These exist in the repo but are NOT in any of `post_update.sh`'s sync
targets, so they will not deploy to the printer.

| Path | Purpose |
|---|---|
| `drafts/sa_status_leds.cfg` | Toolhead LED status macros for §2.2. |
| `drafts/pa_database.cfg` | Sample per-line pressure advance values for §2.3. |

If approved, the path to deployment would be:
- LED macros → `autoloader/macros.cfg` or new `autoloader/leds.cfg`
- PA database → extend the existing `filaments/brands/<brand>.cfg`
  schema (add `pressure_advance` field per line)

The PrusaSlicer vendor.ini generator and the respooler design aren't
drafted as files — the slicer one is a Moonraker module to write, and
the respooler is hardware (CAD).

---

## 4. Suggested Order of Operations

1. **Day 1 (next session):** sa_config rework + calibration
   verification pass. Both are scoped, no design unknowns.
2. **Day 2:** LED status macros (smallest of the side projects, biggest
   visible impact, depends only on existing autoloader profile data).
3. **Day 3:** Pressure-advance database wiring. Backend mostly.
4. **Day 4–5:** PrusaSlicer vendor.ini Moonraker endpoint. Test the
   one-time wizard setup, verify auto-update works.
5. **Side track / weekend:** print and install a passive respooler.
6. **Future / once everything else is stable:** evaluate whether the
   PA database UI needs a panel of its own (probably not — the existing
   sa_settings + sa_load_unload can show the per-tool PA value as
   read-only data).

---

## 5. Sources

- [PrusaSlicer Issue #3188 — extending with scripts/plugins](https://github.com/prusa3d/PrusaSlicer/issues/3188)
- [PrusaSlicer Issue #15107 — filament profile import/export request](https://github.com/prusa3d/PrusaSlicer/issues/15107)
- [PrusaSlicer Wiki — Vendor bundles and updating process](https://github.com/prusa3d/PrusaSlicer/wiki/Vendor-bundles-and-updating-process)
- [Klipper docs — Pressure Advance](https://www.klipper3d.org/Pressure_Advance.html)
- [Klipper Discourse — auto-switching pressure advance per filament](https://klipper.discourse.group/t/auto-switching-pressure-advance-values-per-fillament/8573)
- [Frix-x klippain — pressure advance calibration macro](https://github.com/Frix-x/klippain/blob/main/docs/features/pa_calibration.md)
- [PressureAdvanceCamera — auto PA cal via USB camera](https://github.com/undingen/PressureAdvanceCamera)
- [Auto-Rewinder Mod for Creality Space Pi Plus (Printables)](https://www.printables.com/model/1675067-auto-rewinder-mod-for-creality-space-pi-plus-integ)
- [LTS Respooler — Motorized Filament Winder (MakerWorld)](https://makerworld.com/en/models/448008-lts-respooler-motorized-filament-winder)
