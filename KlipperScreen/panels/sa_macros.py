import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sa_button_style as _sbs
import sa_subscription as _sasub
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_macros')


# Concept B layout: 3 grouped sections instead of one flat 4×3 grid.
#   DAILY        — buttons used during normal operation
#   DIAGNOSTICS  — confirm motors/wiring without committing to a calibration
#   CALIBRATION  — one-time setup commands
#
# (label, gcode_template, needs_tool)
# Use {t} as placeholder for TOOL=N when needs_tool=True.

_DAILY = [
    ("HOME SELECTOR",   "SA_HOME",                          False),
    ("SELECT PATH",     "SA_SELECT TOOL={t}",               True),
    ("ENGAGE",          "SA_ENGAGE",                        False),
    ("DISENGAGE",       "SA_DISENGAGE",                     False),
]

_DIAG = [
    ("STATUS REPORT",   "SA_STATUS",                        False),
    ("BUZZ DRIVE",      "SA_BUZZ_DRIVE",                    False),
    ("BUZZ SELECTOR",   "SA_BUZZ_SELECTOR",                 False),
]

_CAL = [
    # CALIBRATION row labels intentionally drop the "CAL " prefix — the
    # section header already says CALIBRATION, and shorter labels let the
    # 5-up row fit on a 480 px display without ellipsizing or overflow.
    ("SELECTOR",    "SA_CALIBRATE_SELECTOR",            False),
    ("DRIVE",       "SA_CALIBRATE_DRIVE",               False),
    ("ENC SPEED",   "SA_CALIBRATE_ENCODER_SPEED",       False),
    ("ENCODER",     "SA_CALIBRATE_ENCODER TOOL={t}",    True),
    ("BOWDEN",      "SA_CALIBRATE_BOWDEN TOOL={t}",     True),
]

# QUICK RE-CAL row — the three most-common cal tasks as bigger, more
# prominent buttons for one-tap re-runs. Same gcodes as the matching
# entries in _CAL, but presented separately so a user who wants to
# touch up a single cal doesn't have to scan the full 5-button strip.
_QUICK_CAL = [
    ("Re-cal\nSelector",      "SA_CALIBRATE_SELECTOR",      False),
    ("Re-cal\nDrive",         "SA_CALIBRATE_DRIVE",         False),
    ("Re-cal\nEnc Speed",     "SA_CALIBRATE_ENCODER_SPEED", False),
]


class Panel(ScreenPanel):
    """Autoloader macros — grouped by frequency of use.

    DAILY first, large; DIAGNOSTICS middle; CALIBRATION at the bottom with
    smaller buttons since those are run once per setup.
    """

    def __init__(self, screen, title):
        super().__init__(screen, title or "SA Macros")
        _sbs.apply()

        self._num_paths   = 6
        self._pending_cmd = None   # gcode template waiting for tool selection

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack.set_transition_duration(150)

        self._stack.add_named(self._build_main_page(), "main")
        self._stack.add_named(self._build_tool_page(), "tool")

        self.content.pack_start(self._stack, True, True, 0)

    # ── Section header ────────────────────────────────────────────────────

    def _section_header(self, title):
        """Small-caps dimmed label used as a section divider."""
        lbl = Gtk.Label(halign=Gtk.Align.START, xalign=0.0)
        lbl.set_markup(
            '<span font_size="x-small" foreground="#9E9E9E" '
            'letter_spacing="2000">── %s ──</span>' % title)
        # Tight headers — top margin minimized further so all 4 sections
        # fit on a 480 px screen with no scroll on the QUICK RE-CAL row.
        lbl.set_margin_top(1)
        lbl.set_margin_bottom(0)
        return lbl

    def _section_row(self, items, btn_h):
        """Single row of equal-width buttons for a section.

        Three layers of overflow protection:
          1. row.set_homogeneous(True) splits available width equally
             across N children regardless of label length.
          2. Each button's label wraps to up to 2 lines (word-aware) so
             a long two-word label like "HOME SELECTOR" stacks vertically
             instead of being chopped with "…".
          3. set_lines(2) caps wrapping at 2 lines and falls back to
             ellipsize if the label is still too wide (rare with the
             current label set, but safe).
        """
        from gi.repository import Pango
        row = Gtk.Box(spacing=6)
        row.set_hexpand(True)
        row.set_homogeneous(True)
        for label, gcode, needs_tool in items:
            btn = _sbs.make(label)
            btn.set_size_request(-1, btn_h)
            child = btn.get_child()
            if isinstance(child, Gtk.Label):
                child.set_line_wrap(True)
                child.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                child.set_lines(2)
                child.set_justify(Gtk.Justification.CENTER)
                child.set_ellipsize(Pango.EllipsizeMode.END)
                child.set_max_width_chars(12)
            if needs_tool:
                btn.connect("clicked", self._pick_tool, gcode)
            else:
                btn.connect("clicked", self._send, gcode)
            row.pack_start(btn, True, True, 0)
        return row

    # ── Main page ─────────────────────────────────────────────────────────

    def _build_main_page(self):
        # Tight spacing/margin so 4 sections fit on a 480 px screen
        # (content_height ≈ 396 px) without scrolling. Each section's
        # header is followed directly by its button row with no extra
        # spacing in between (`spacing=2` between siblings of the outer
        # Box keeps headers visually attached to their rows).
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        spacing=2, margin=4)

        # Heights step down section by section so the eye lands on
        # DAILY first. Trimmed further (60/50/42/52 → 54/44/38/48) so
        # all 4 sections clear the bottom of a 480 px screen with no
        # scroll, even after KS chrome takes its share.
        outer.pack_start(self._section_header("DAILY"),              False, False, 0)
        outer.pack_start(self._section_row(_DAILY,     btn_h=54),    False, False, 0)

        outer.pack_start(self._section_header("DIAGNOSTICS"),        False, False, 0)
        outer.pack_start(self._section_row(_DIAG,      btn_h=44),    False, False, 0)

        outer.pack_start(self._section_header("CALIBRATION"),        False, False, 0)
        outer.pack_start(self._section_row(_CAL,       btn_h=38),    False, False, 0)

        # QUICK RE-CAL — three buttons slightly taller than CALIBRATION
        # to telegraph "quick shortcut" while still fitting on screen.
        outer.pack_start(self._section_header("QUICK RE-CAL"),       False, False, 0)
        outer.pack_start(self._section_row(_QUICK_CAL, btn_h=48),    False, False, 0)

        return outer

    # ── Tool picker page ──────────────────────────────────────────────────

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

        back_btn = _sbs.make("←  Cancel", "sa-btn-alt")
        back_btn.set_margin_start(8)
        back_btn.set_margin_end(8)
        back_btn.set_margin_top(6)
        back_btn.set_margin_bottom(8)
        back_btn.connect("clicked",
                         lambda w: self._stack.set_visible_child_name("main"))
        outer.pack_start(back_btn, False, False, 0)

        return outer

    def _rebuild_tool_buttons(self, num_paths):
        for child in self._tool_grid.get_children():
            self._tool_grid.remove(child)
        for i in range(num_paths):
            btn = _sbs.make("T%d" % i)
            btn.connect("clicked", self._tool_selected, i)
            self._tool_grid.attach(btn, i % 3, i // 3, 1, 1)
        self._tool_grid.show_all()

    # ── Handlers ──────────────────────────────────────────────────────────

    def _send(self, widget, gcode):
        self._screen._ws.klippy.gcode_script(gcode)

    def _pick_tool(self, widget, gcode_template):
        self._pending_cmd = gcode_template
        self._rebuild_tool_buttons(self._num_paths)
        self._stack.set_visible_child_name("tool")

    def _tool_selected(self, widget, tool_idx):
        if self._pending_cmd:
            cmd = self._pending_cmd.replace("{t}", str(tool_idx))
            self._screen._ws.klippy.gcode_script(cmd)
            self._pending_cmd = None
        self._stack.set_visible_child_name("main")

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def activate(self):
        # Same combined subscription + global popup watcher pattern as the
        # other autoloader panels — keeps base_panel toolhead-temp updating
        # and lets popups fire from any KS panel.
        self._screen._ws.klippy.object_subscription(
            {"objects": _sasub.build_subscription(self._screen)})
        _sasub.install_global_popup_watcher(self._screen)

        sa = self._printer.data.get("autoloader", {})
        self._num_paths = sa.get("num_paths", 6)
        self._stack.set_visible_child_name("main")

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("autoloader")
        if sa is not None:
            n = sa.get("num_paths")
            if n is not None:
                self._num_paths = n
        return False
