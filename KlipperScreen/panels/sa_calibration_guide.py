import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sa_button_style as _sbs
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_calibration_guide')

_GREY      = "#616161"
_GREEN     = "#388E3C"
_AMBER     = "#F9A825"
_NUM_STEPS = 7

_STEP_TITLES = [
    "1 — Test Motors",
    "2 — Home Selector",
    "3 — Calibrate Selector",
    "4 — Calibrate Drive Motor",
    "5 — Calibrate Encoder Speed",
    "6 — Calibrate Encoder (mm/pulse)",
    "7 — Calibrate Bowden Length",
]


class Panel(ScreenPanel):
    """Step-by-step calibration guide — one full page per step."""

    def __init__(self, screen, title):
        super().__init__(screen, title or "SA Calibration")
        _sbs.apply()

        self._num_paths   = 6
        self._last_sa     = {}
        self._pending_cmd = None
        self._step        = 0

        # Outer stack: "pages" (step navigator) | "tool" (path picker)
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack.set_transition_duration(150)

        self._stack.add_named(self._build_pages_view(), "pages")
        self._stack.add_named(self._build_tool_page(),  "tool")

        self.content.pack_start(self._stack, True, True, 0)

    # ── Pages view ────────────────────────────────────────────────────────────

    def _build_pages_view(self):
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Inner stack — one ScrolledWindow per step
        self._page_stack = Gtk.Stack()
        self._page_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._page_stack.set_transition_duration(200)

        self._step_boxes = []
        for i in range(_NUM_STEPS):
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_overlay_scrolling(False)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=12)
            scroll.add(box)
            self._step_boxes.append(box)
            self._page_stack.add_named(scroll, "step%d" % i)

        wrapper.pack_start(self._page_stack, True, True, 0)

        # Nav bar: [◀ Back]  Step N of 7  [Next ▶]  — compact, fixed height
        nav = Gtk.Box(spacing=6, margin_start=6, margin_end=6,
                      margin_top=4, margin_bottom=4)

        self._prev_btn = Gtk.Button(label="◀  Back",
                                    hexpand=False, vexpand=False, can_focus=False)
        self._prev_btn.set_size_request(110, 40)
        self._prev_btn.connect("clicked", self._go_prev)

        self._step_lbl = Gtk.Label()
        self._step_lbl.set_hexpand(True)
        self._step_lbl.set_halign(Gtk.Align.CENTER)

        self._next_btn = Gtk.Button(label="Next  ▶",
                                    hexpand=False, vexpand=False, can_focus=False)
        self._next_btn.set_size_request(110, 40)
        self._next_btn.connect("clicked", self._go_next)

        nav.pack_start(self._prev_btn, False, False, 0)
        nav.pack_start(self._step_lbl, True,  True,  0)
        nav.pack_start(self._next_btn, False, False, 0)
        wrapper.pack_start(nav, False, False, 0)

        return wrapper

    def _update_nav(self):
        self._step_lbl.set_markup(
            '<b>Step %d of %d</b>' % (self._step + 1, _NUM_STEPS))
        self._prev_btn.set_sensitive(self._step > 0)
        self._next_btn.set_sensitive(self._step < _NUM_STEPS - 1)

    def _go_prev(self, widget):
        if self._step > 0:
            self._step -= 1
            self._show_step()

    def _go_next(self, widget):
        if self._step < _NUM_STEPS - 1:
            self._step += 1
            self._show_step()

    def _show_step(self):
        self._page_stack.set_visible_child_name("step%d" % self._step)
        self._update_nav()
        self._populate_step(self._step, self._last_sa)

    # ── Step content ──────────────────────────────────────────────────────────

    def _populate_step(self, idx, sa):
        box = self._step_boxes[idx]
        for child in box.get_children():
            box.remove(child)

        homed       = sa.get("current_path", -1) >= 0
        sel_pos     = sa.get("selector_positions", [])
        enc_mpp     = sa.get("encoder_mpp", [])
        bowden_lens = sa.get("bowden_lengths", [])
        drv_rot     = sa.get("drive_rotation_distance", 0.0)
        enc_max     = sa.get("encoder_max_speed", 0.0)
        num         = sa.get("num_paths", self._num_paths)
        cal_state   = sa.get("cal_state", "")

        box.pack_start(self._section(_STEP_TITLES[idx]), False, False, 0)

        if   idx == 0: self._step_motors(box)
        elif idx == 1: self._step_home(box, homed)
        elif idx == 2: self._step_selector(box, sel_pos, cal_state, num)
        elif idx == 3: self._step_drive(box, drv_rot)
        elif idx == 4: self._step_enc_speed(box, enc_max)
        elif idx == 5: self._step_encoder(box, enc_mpp, num)
        elif idx == 6: self._step_bowden(box, bowden_lens, num)

        box.show_all()

    def _step_motors(self, box):
        box.pack_start(self._hint(
            "Run both buzz tests to confirm motor wiring and direction."),
            False, False, 0)
        row = Gtk.Grid(column_spacing=8)
        row.set_column_homogeneous(True)
        b1 = _sbs.make("BUZZ DRIVE",    "sa-btn-alt")
        b2 = _sbs.make("BUZZ SELECTOR", "sa-btn-alt")
        b1.connect("clicked", self._send, "SA_BUZZ_DRIVE")
        b2.connect("clicked", self._send, "SA_BUZZ_SELECTOR")
        row.attach(b1, 0, 0, 1, 1)
        row.attach(b2, 1, 0, 1, 1)
        box.pack_start(row, False, False, 0)

    def _step_home(self, box, homed):
        box.pack_start(
            self._status("\u2713 Homed" if homed else "\u2715 Not homed",
                         _GREEN if homed else _AMBER),
            False, False, 0)
        box.pack_start(self._hint(
            "Moves the selector to the physical endstop and zeros its position. "
            "Required before any selector movement."),
            False, False, 0)
        btn = _sbs.make("HOME SELECTOR", "sa-btn-alt")
        btn.connect("clicked", self._send, "SA_HOME")
        box.pack_start(btn, False, False, 0)

    def _step_selector(self, box, sel_pos, cal_state, num):
        has_cal = (bool(sel_pos) and
                   any(abs(sel_pos[i] - i * 21.0) > 1.0 for i in range(len(sel_pos))))
        if has_cal:
            pos_str = "  ".join("T%d:%.1f" % (i, sel_pos[i]) for i in range(len(sel_pos)))
            box.pack_start(self._status("\u2713 Calibrated  " + pos_str, _GREEN),
                           False, False, 0)
        else:
            box.pack_start(self._status("\u2715 Using defaults (run to calibrate)", _AMBER),
                           False, False, 0)
        if cal_state:
            box.pack_start(self._status("In progress: %s" % cal_state, _AMBER),
                           False, False, 0)
        box.pack_start(self._hint(
            "Sweeps the full rail using stallguard to find the far end, then "
            "homes back to measure total travel and calculate even path spacing."),
            False, False, 0)
        btn = _sbs.make("CAL SELECTOR", "sa-btn-alt")
        btn.connect("clicked", self._send, "SA_CALIBRATE_SELECTOR")
        box.pack_start(btn, False, False, 0)

    def _step_drive(self, box, drv_rot):
        if drv_rot and drv_rot > 0:
            box.pack_start(
                self._status("\u2713 rotation_distance = %.4f" % drv_rot, _GREEN),
                False, False, 0)
        else:
            box.pack_start(self._status("\u2715 Not calibrated", _AMBER),
                           False, False, 0)
        box.pack_start(self._hint(
            "Manually load filament through the drive gear. Marks a 100 mm "
            "reference, drives it, then prompts you to measure actual movement "
            "to calculate rotation_distance."),
            False, False, 0)
        btn = _sbs.make("CAL DRIVE", "sa-btn-alt")
        btn.connect("clicked", self._send, "SA_CALIBRATE_DRIVE")
        box.pack_start(btn, False, False, 0)

    def _step_enc_speed(self, box, enc_max):
        if enc_max and enc_max > 0:
            box.pack_start(
                self._status("\u2713 Max = %.1f mm/s  (blast = %.1f mm/s)"
                             % (enc_max, enc_max * 0.75), _GREEN),
                False, False, 0)
        else:
            box.pack_start(
                self._status("\u2715 Not calibrated  (blast defaults to 75 mm/s)", _AMBER),
                False, False, 0)
        box.pack_start(self._hint(
            "Ramps feed speed up until the encoder starts slipping, then saves "
            "the highest reliable speed. Run with filament loaded through the drive gear."),
            False, False, 0)
        btn = _sbs.make("CAL ENCODER SPEED", "sa-btn-alt")
        btn.connect("clicked", self._send, "SA_CALIBRATE_ENCODER_SPEED")
        box.pack_start(btn, False, False, 0)

    def _step_encoder(self, box, enc_mpp, num):
        box.pack_start(self._hint(
            "Measures mm of filament per encoder pulse. "
            "Run per path with filament loaded past the drive gear."),
            False, False, 0)
        grid = Gtk.Grid(column_spacing=6, row_spacing=6)
        grid.set_column_homogeneous(True)
        for i in range(num):
            mpp  = enc_mpp[i] if i < len(enc_mpp) else 0.0
            done = mpp > 0.0
            fg   = _GREEN if done else _GREY
            lbl  = Gtk.Label(halign=Gtk.Align.CENTER)
            lbl.set_markup('<span foreground="%s" font_size="small">T%d\n%s</span>'
                           % (fg, i, ("%.4f" % mpp) if done else "\u2715"))
            lbl.set_size_request(-1, 34)
            grid.attach(lbl, i % 3, i // 3 * 2,     1, 1)
            btn = _sbs.make("T%d" % i, "sa-btn-alt")
            btn.connect("clicked", self._pick_tool, "SA_CALIBRATE_ENCODER TOOL={t}", i)
            grid.attach(btn, i % 3, i // 3 * 2 + 1, 1, 1)
        box.pack_start(grid, False, False, 0)

    def _step_bowden(self, box, bowden_lens, num):
        box.pack_start(self._hint(
            "Loads filament from the drive gear to the extruder sensor and "
            "records the distance. Run per path after encoder is calibrated."),
            False, False, 0)
        grid = Gtk.Grid(column_spacing=6, row_spacing=6)
        grid.set_column_homogeneous(True)
        for i in range(num):
            blen = bowden_lens[i] if i < len(bowden_lens) else 800.0
            done = abs(blen - 800.0) > 5.0
            fg   = _GREEN if done else _GREY
            lbl  = Gtk.Label(halign=Gtk.Align.CENTER)
            lbl.set_markup('<span foreground="%s" font_size="small">T%d\n%s</span>'
                           % (fg, i, ("%.0fmm" % blen) if done else "\u2715"))
            lbl.set_size_request(-1, 34)
            grid.attach(lbl, i % 3, i // 3 * 2,     1, 1)
            btn = _sbs.make("T%d" % i, "sa-btn-alt")
            btn.connect("clicked", self._pick_tool, "SA_CALIBRATE_BOWDEN TOOL={t}", i)
            grid.attach(btn, i % 3, i // 3 * 2 + 1, 1, 1)
        box.pack_start(grid, False, False, 0)

    # ── Tool picker page ──────────────────────────────────────────────────────

    def _build_tool_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        lbl = Gtk.Label()
        lbl.set_markup('<b><span font_size="large">Select Path</span></b>')
        lbl.set_margin_top(10)
        lbl.set_margin_bottom(6)
        outer.pack_start(lbl, False, False, 0)

        self._tool_grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True,
                                   row_spacing=6, column_spacing=6,
                                   margin_start=8, margin_end=8)
        outer.pack_start(self._tool_grid, True, True, 0)

        back_btn = _sbs.make("\u2190  Cancel", "sa-btn-alt")
        back_btn.set_margin_start(8)
        back_btn.set_margin_end(8)
        back_btn.set_margin_top(6)
        back_btn.set_margin_bottom(8)
        back_btn.connect("clicked", lambda w: self._stack.set_visible_child_name("pages"))
        outer.pack_start(back_btn, False, False, 0)
        return outer

    def _rebuild_tool_buttons(self, num_paths, preselected=None):
        for child in self._tool_grid.get_children():
            self._tool_grid.remove(child)
        for i in range(num_paths):
            style = "sa-btn" if i == preselected else "sa-btn-alt"
            btn = _sbs.make("T%d" % i, style)
            btn.connect("clicked", self._tool_selected, i)
            self._tool_grid.attach(btn, i % 3, i // 3, 1, 1)
        self._tool_grid.show_all()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _section(self, title):
        lbl = Gtk.Label(halign=Gtk.Align.START)
        lbl.set_markup('<b><span font_size="large">%s</span></b>' % title)
        return lbl

    def _hint(self, text):
        lbl = Gtk.Label(label=text, halign=Gtk.Align.START, xalign=0.0, wrap=True)
        lbl.get_style_context().add_class("color4")
        return lbl

    def _status(self, text, fg=_GREY):
        lbl = Gtk.Label(halign=Gtk.Align.START)
        lbl.set_markup('<span foreground="%s">%s</span>' % (fg, text))
        return lbl

    def _send(self, widget, gcode):
        self._screen._ws.klippy.gcode_script(gcode)

    def _pick_tool(self, widget, gcode_template, preselected=None):
        self._pending_cmd = gcode_template
        self._rebuild_tool_buttons(self._num_paths, preselected)
        self._stack.set_visible_child_name("tool")

    def _tool_selected(self, widget, tool_idx):
        if self._pending_cmd:
            cmd = self._pending_cmd.replace("{t}", str(tool_idx))
            self._screen._ws.klippy.gcode_script(cmd)
            self._pending_cmd = None
        self._stack.set_visible_child_name("pages")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def activate(self):
        sa = self._printer.data.get("stealth_autoloader", {})
        self._last_sa   = dict(sa)
        self._num_paths = sa.get("num_paths", 6)
        self._stack.set_visible_child_name("pages")
        self._show_step()

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("stealth_autoloader")
        if sa is not None:
            self._last_sa.update(sa)
            n = sa.get("num_paths")
            if n is not None:
                self._num_paths = n
            GLib.idle_add(self._refresh_current_step)

    def _refresh_current_step(self):
        if self._stack.get_visible_child_name() == "pages":
            self._populate_step(self._step, self._last_sa)
        return False
