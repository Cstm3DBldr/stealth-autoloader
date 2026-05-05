# Autoloader — Claude Code Project Instructions

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
- Config path: ~/printer_data/config/autoloader/
- Repo path:   ~/autoloader/

## GitHub
- Repo: https://github.com/Cstm3DBldr/autoloader.git
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
- **Printer is authoritative for ALL deployed files. Always pull-first
  before modifying.** Before starting any task that touches files which
  are deployed to the printer (cfg, KlipperScreen panels, html), pull the
  printer's live copy and reconcile against the repo:
    - `~/printer_data/config/autoloader/*.cfg`, `*.html`
    - `~/printer_data/config/sa_klipperscreen.conf`
    - `~/KlipperScreen/panels/sa_*.py`, `~/KlipperScreen/sa_*.py`
    - `~/printer_data/config/autoloader/filament_profiles/`
  The printer's content wins on any divergence; pull it into the repo as
  the new default, commit, push, then make the requested change. This
  protects against losing user-tuned values, runtime calibrations, or
  edits made directly via the Mainsail config editor / KlipperScreen.

  Symlinked files (Klipper extras, Moonraker component) don't need this
  step — they read directly from the repo.

  `scripts/verify.sh` runs the parameters.cfg drift check on every run
  and exits non-zero if the printer has tuned values not in the repo.

---

## UI Preferences (locked — restore if disturbed)

### KlipperScreen Load/Unload path buttons (`KlipperScreen/panels/sa_load_unload.py`)

User-confirmed canonical look (commit `76b4f35`). If a future edit changes
any of this, restore it to match unless the user explicitly asks for a
different layout.

- **Layout:** each path button is a `Gtk.Grid` with `column_homogeneous=True`
  and 3 columns: `T#` (left), color swatch (middle), material (right). Each
  column is exactly 1/3 of the button width.
- **Swatch placement:** swatch widget has `halign=CENTER` inside its column,
  so it lands at the absolute geometric center of the button regardless of
  the T# or material label widths. Do NOT use a `Gtk.Box` + `pack_start`
  chain here — that lets label widths shift the swatch off-center.
- **Swatch size:** `sw_size = max(36, min(48, btn_h - 24))` — gives 36–48 px
  depending on button height. Don't shrink below 36 (too small to read at
  arm's length) and don't grow above 48 (overwhelms the row).
- **Button height:** `_path_btn_h()` returns `max(50, min(72, avail // rows))`
  with `avail = self._screen.height - 60 - 74 - 50`. The 72 cap is what
  prevents the 2×3 grid from pushing the action bar offscreen on a 480px
  display — don't raise it without testing on a real KS device.
- **Label alignment:** both labels `halign=CENTER` inside their columns.
  Material has `set_ellipsize(3)` and `set_max_width_chars(8)` so a long
  material name can't push past the column boundary.
- **Color-picker chips** (in the wizard color step): `60×70` button with
  `40 px` swatch (was `72×82` / `52 px` — too crowded on small screens).

### KlipperScreen Autoloader home menu (`KlipperScreen/panels/sa_home.py`)

User-confirmed canonical look (commit `b991e31`, "Concept B"). If a future
edit changes any of this, restore it to match unless the user explicitly
asks for a different layout.

- **Two-section layout:** top "hero row" (`vexpand=True` — absorbs leftover
  height, ends up ~65% of the panel on a 480 px display); bottom "utility
  row" with a fixed `set_size_request(-1, 110)` height. Outer Box is
  `spacing=8, margin=8`, `homogeneous=False`.
- **Hero tiles (top):** STATUS (`color1`, `spoolman` icon) on the left,
  LOAD/UNLOAD (`color3`, `load` icon) on the right. Each is a custom
  `Gtk.Button` containing a vertical Box (icon → label → preview line),
  built via `_build_hero_btn()`. Do NOT replace these with the standard
  `self._gtk.Button(icon, label, color)` — that one only supports
  icon + label and won't fit the preview.
- **Hero icon size:** `int(self._gtk.img_scale * self._gtk.button_image_scale * 2.0)`
  — 2× the standard KS button image scale. Don't reduce below 1.5× or the
  hero treatment is lost.
- **Hero label:** `<span font_size="large" weight="bold">…</span>`.
- **Hero preview line:** `<span font_size="small" foreground="#BDBDBD">…</span>`,
  `set_max_width_chars(32)`, `set_ellipsize(3)`. Content driven by
  `_build_status_text()` and `_build_load_text()`:
    - STATUS: `"N/M loaded · T0 mat · T1 mat"` (up to 2 loaded materials),
      `"N / M paths loaded"` if no materials set, or `"Autoloader idle"` if
      `total == 0`.
    - LOAD/UNLOAD: `"Selector at TN"` (with `" · drive engaged"` when
      `servo_engaged`), or `"Selector unhomed"` when `current_path < 0`.
  Always escape dynamic text via `xml.sax.saxutils.escape` before passing
  to `set_markup` — material names can contain `&`/`<`.
- **Utility row (bottom):** four standard KS buttons in
  `MACROS / CALIBRATION / SETTINGS / CONFIG` order, each
  `self._gtk.Button(icon, label, color)`, all `pack_start(True, True, 0)`
  for equal-width slots. Row spacing = 8 px. Don't change the order — it's
  alphabetical-ish AND matches the user's "frequency of use, low to high"
  reading.
- **No decorative colored bars** under the labels. Visual hierarchy comes
  from tile size + the standard KS color classes only.
- **Subscription:** `activate()` calls `_sasub.build_subscription(...)` and
  `_sasub.install_global_popup_watcher(...)` — same pattern as every other
  autoloader panel. `process_update` updates previews via `GLib.idle_add`.
  `activate()` also does a one-shot `apiclient.send_request("printer/objects/query?autoloader")`
  so the previews show real data instantly instead of `"…"`.

### KlipperScreen Macros menu (`KlipperScreen/panels/sa_macros.py`)

User-confirmed canonical look. If a future edit changes any of this,
restore it to match unless the user explicitly asks for a different
layout. The first-render-bug history that produced these constraints
is preserved in commits `0079f41` → `d48e0f2`.

- **Three sections, top→bottom:** DAILY (4 buttons) → DIAGNOSTICS (3) →
  CALIBRATION (4). Order is "frequency of use, highest first." Don't
  reorder. An earlier "QUICK RE-CAL" 4th section was removed because
  its 3 buttons (Re-cal Sel / Drive / Enc) were exact duplicates of
  the first 3 CALIBRATION buttons — same gcodes, just different
  labels. Don't add it back.
- **CALIBRATION buttons** are 3 globals + 1 per-tool:
  - `Calibrate Selector` → `SA_CALIBRATE_SELECTOR` (global)
  - `Calibrate Drive` → `SA_CALIBRATE_DRIVE` (global)
  - `Calibrate Encoder Speed` → `SA_CALIBRATE_ENCODER_SPEED` (global)
  - `Calibrate Bowden` → `SA_CALIBRATE_BOWDEN TOOL={t}` (per-tool —
    tapping opens the tool picker)
  
  Per-tool encoder mm/pulse calibration (`SA_CALIBRATE_ENCODER TOOL=N`)
  is intentionally NOT here — it lives in the step-by-step Calibration
  Guide panel because it has a more involved per-path workflow.
- **CALIBRATION labels stack on 2 lines via embedded `\n`:**
  `"Calibrate\nSelector"` etc. This is the ONE place where embedded
  `\n` is acceptable — the long "Calibrate Encoder Speed" label
  doesn't fit a 4-column row width on a 800 px screen, so all four
  labels are deliberately stacked for visual consistency. The
  resulting 2-line button height is the reason CALIBRATION's
  `btn_h=72` is taller than DIAGNOSTICS's 64.
- **Button heights:** DAILY=78, DIAGNOSTICS=64, CALIBRATION=72 px.
  CALIBRATION breaks the simple "decreases with frequency" hierarchy
  because of the 2-line labels — each individual button is still
  visually smaller than DAILY, but the row needs the extra height.
- **Outer Box:** `Gtk.Box(VERTICAL, spacing=6)`, margins
  `top=10, start=8, end=8, bottom=14`. The trailing **vexpand=True
  spacer** Box at the end of `_build_main_page` is REQUIRED — without
  at least one expanding child, the page's natural height = sum of
  fixed children, and base_panel's spanning vexpand action_bar grabs
  more vertical budget than it should on first allocation. Don't
  remove the spacer.
- **Section header:** `Gtk.Label` with markup
  `<span font="11" foreground="#9E9E9E">── %s ──</span>`. Pinned to
  fixed pt size and CSS class `.sa-section-header` (margin/padding/
  min-height all 0). NEVER use em-based `font_size="x-small"` or
  `letter_spacing` — both depend on font-metric measurement that's
  unstable across the first realize pass and produce ~4 px extra
  per header on first attach (×4 headers = the 16 px content
  overflow that stretches base_panel's left rail and clips the
  power icon off-screen).
- **CSS provider** is installed once per session by
  `_install_action_bar_css()` (module-level guard). Pins
  `.action_bar > button` margin/padding to small fixed pixel values
  AND sets `.sa-section-header` to the same. Both rules run from
  one provider at `STYLE_PROVIDER_PRIORITY_USER + 100`.
- **Page switching:** `Gtk.Notebook` with `set_show_tabs(False)` +
  `set_show_border(False)`, two pages: `main` and `tool`. Notebook
  was chosen over `Gtk.Stack` because Stack's `vhomogeneous=False`
  flag doesn't reliably take effect on the first allocation pass
  after KlipperScreen restart. Don't switch back to Stack.
- **Self.content sizing:** `vexpand=False` and
  `set_size_request(-1, _gtk.content_height)` are both pinned in
  `__init__` to override screen_panel.py's default `vexpand=True`,
  so the content widget claims exactly its slice of the grid row
  (no fight with action_bar's vexpand for leftover space).
- **Section row labels:** prefer single-line. Embedded `\n` to stack
  words is OK ONLY when the natural single-line label wouldn't fit
  the column width (the CALIBRATION row's 4 buttons are the example);
  in that case set `btn_h` larger to accommodate, and use `\n` on
  EVERY label in the row for visual consistency rather than letting
  GTK's auto-wrap pick which buttons stack.
- **Section row buttons:** `set_homogeneous(True)` for equal width;
  Pango wrap settings (`set_line_wrap(WORD_CHAR)`, `set_lines(2)`)
  let "HOME SELECTOR" stack to two lines instead of ellipsizing.
  `set_ellipsize(END)` + `set_max_width_chars(12)` is the safety
  net for any longer label that's still added in the future.
- **Subscription:** `activate()` calls
  `_sasub.build_subscription(...)` and
  `_sasub.install_global_popup_watcher(...)` — same pattern as
  every other autoloader panel.

---

## Project File Structure

| File | Purpose |
|---|---|
| `autoloader/autoloader.cfg` | Aggregator. printer.cfg pulls only this file: `[include autoloader/autoloader.cfg]`. Pulls in the others below |
| `autoloader/pin_aliases.cfg` | ONLY physical hardware pins and aliases. One [board_pins] per MCU. No polarity, no hardware config |
| `autoloader/hardware.cfg` | ONLY hardware sections: [mcu], [tmc5160], [manual_stepper], [servo], [sa_encoder], [filament_switch_sensor], [gcode_button selector_stall] |
| `autoloader/parameters.cfg` | The single `[autoloader]` section — all user-tunable values (servo angles, speeds, tip-form, park, selector cal, bowden lengths, sensor/encoder/extruder/stepper references). Klipper requires the section in one file |
| `autoloader/macros.cfg` | Thin gcode wrappers around Python backend commands |
| `klipper/extras/autoloader.py` | Main controller — config parsing, GCode registration, status object |
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
- `~/klipper/klippy/extras/autoloader.py` → `~/autoloader/klipper/extras/autoloader.py`
- `~/klipper/klippy/extras/sa_motion.py` → `~/autoloader/klipper/extras/sa_motion.py`
- `~/klipper/klippy/extras/sa_sequences.py` → `~/autoloader/klipper/extras/sa_sequences.py`
- `~/klipper/klippy/extras/sa_calibration.py` → `~/autoloader/klipper/extras/sa_calibration.py`
- `~/klipper/klippy/extras/sa_encoder.py` → `~/autoloader/klipper/extras/sa_encoder.py`
- `~/moonraker/moonraker/components/sa_moonraker.py` → `~/autoloader/moonraker/sa_moonraker.py`

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

## Python Backend — autoloader.py

Single `[autoloader]` config section, single class instance, controls everything.

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

**Routine deploys** (after committing + pushing to `origin/main`):

The printer auto-syncs through Moonraker Update Manager. Click "Update" on
the autoloader entry in Mainsail's Update Manager, OR run on the printer:

```bash
ssh pi@192.168.1.214 "cd ~/autoloader && git pull && ./post_update.sh && \
    sudo systemctl restart klipper && sudo systemctl restart moonraker && \
    sudo systemctl restart KlipperScreen"
```

`post_update.sh` is the canonical "sync everything that's not symlinked" step.
It runs automatically after every Update Manager pull and copies the .cfg/.html,
KlipperScreen panels, and `sa_klipperscreen.conf` into their live locations.

**First-time install** on a new printer:

```bash
git clone https://github.com/Cstm3DBldr/autoloader.git ~/autoloader
cd ~/autoloader && ./install.sh
```

`install.sh` creates the 6 symlinks (5 Klipper extras + Moonraker component),
runs `post_update.sh` for the initial file sync, registers the repo with the
Update Manager, and restarts services.

**Verification** (always run after a deploy or when something feels off):

```bash
./scripts/verify.sh
```

Checks symlink state, service health, recent log errors, and scans every
on-printer location for forbidden patterns. Default scan looks for stale
`stealth_autoloader` references; pass custom patterns as args after a future
rename: `./scripts/verify.sh OLD_NAME OldName`.

### Project Surface — every place code lives on the printer

If you add a new file to the project, add its destination here AND update
`post_update.sh` (if not symlinked) or `install.sh` (if symlinked).

| Repo path | On-printer destination | Sync mechanism |
|---|---|---|
| `klipper/extras/autoloader.py` + 4 `sa_*.py` | `~/klipper/klippy/extras/` | symlink (install.sh) |
| `moonraker/sa_moonraker.py` | `~/moonraker/moonraker/components/sa_moonraker.py` | symlink (install.sh) |
| `autoloader/*.cfg` | `~/printer_data/config/autoloader/` | direct copy (post_update.sh) |
| `autoloader/*.html` | `~/printer_data/config/autoloader/` | direct copy (post_update.sh) |
| `KlipperScreen/panels/sa_*.py` | `~/KlipperScreen/panels/` | direct copy (post_update.sh) |
| `KlipperScreen/sa_*.py` | `~/KlipperScreen/` | direct copy (post_update.sh) |
| `KlipperScreen/sa_klipperscreen.conf` | `~/printer_data/config/sa_klipperscreen.conf` | direct copy (post_update.sh) |
| `web/mainsail/AutoloaderPanel.vue` | compiled into `~/mainsail/assets/*.js` | manual rebuild from VS source — not auto-synced |
| `web/fluidd/AutoloaderPanel.vue` | depends on Fluidd host setup | user-managed |

### Rename-class changes — extra steps beyond the routine deploy

A project-wide rename (like `stealth_autoloader` → `autoloader`) needs all of
the routine sync PLUS these one-time fixups, because the routine deploy
doesn't cover compiled bundles, persistent caches, or auto-generated config
blocks:

1. **Compiled Mainsail bundle** in `~/mainsail/assets/*.js` has the old
   identifier baked in. Either rebuild from the VS source (proper) or
   sed-rewrite in place (fast — make a backup first):
   ```bash
   ssh pi@192.168.1.214 "cp -r ~/mainsail/assets ~/mainsail/_pre_rename_$(date +%s)/ && \
       for f in \$(grep -rlE 'OLD_PATTERN' ~/mainsail/assets/ ~/mainsail/index.html); do \
           sed -i 's/OLD_PATTERN/NEW_PATTERN/g' \$f; done"
   ```
2. **Moonraker SQLite cache** (`~/printer_data/database/moonraker-sql.db`)
   stores repo metadata under `namespace_store / update_manager / <name>`.
   Renames need: stop moonraker → DELETE the cached row → start moonraker.
3. **`printer.cfg` SAVE_CONFIG block** at the bottom (lines starting with
   `#*#`) contains the old section name. Rename `#*# [OLD_SECTION]` →
   `#*# [NEW_SECTION]` directly with sed.
4. **`moonraker.conf` `[update_manager …]` block** has both a section name
   and `path:` field referencing the old name — both need updating.
5. **stale `__pycache__` directories** under `~/KlipperScreen/panels/` and
   `~/KlipperScreen/`. `rm -rf` them; Python rebuilds on next start.
6. **Browser cache** — hard-refresh (Ctrl+Shift+R) Mainsail/Fluidd. JS
   bundle filenames don't change in a sed-rewrite, so the browser will keep
   serving the cached old code without an explicit refresh.

After all of the above, run `./scripts/verify.sh OLD_PATTERN` to confirm
nothing was missed.

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
- Do not put load/unload sequences in macros — they live in autoloader.py
- Do not add per-path feed motors — there is ONE drive motor for all paths
- Do not use `^` before a chip name on output pins (step pins) — only valid on input pins
- Do not define `[mcu autoloader]` in printer.cfg — it's in hardware.cfg
- Do not add trailing comma to last alias in `[board_pins]`
- Do not SCP `References/` folder to printer
- Do not create separate `[filament_feed toolN]` sections — replaced by `[autoloader]`
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
