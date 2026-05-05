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
    ("CAL SELECTOR",    "SA_CALIBRATE_SELECTOR",            False),
    ("CAL DRIVE",       "SA_CALIBRATE_DRIVE",               False),
    ("CAL ENC SPD",     "SA_CALIBRATE_ENCODER_SPEED",       False),
    ("CAL ENCODER",     "SA_CALIBRATE_ENCODER TOOL={t}",    True),
    ("CAL BOWDEN",      "SA_CALIBRATE_BOWDEN TOOL={t}",     True),
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
        lbl.set_margin_top(8)
        lbl.set_margin_bottom(2)
        return lbl

    def _section_row(self, items, btn_h):
        """Single row of equal-width buttons for a section."""
        row = Gtk.Box(spacing=6)
        row.set_hexpand(True)
        for label, gcode, needs_tool in items:
            btn = _sbs.make(label)
            btn.set_size_request(-1, btn_h)
            if needs_tool:
                btn.connect("clicked", self._pick_tool, gcode)
            else:
                btn.connect("clicked", self._send, gcode)
            row.pack_start(btn, True, True, 0)
        return row

    # ── Main page ─────────────────────────────────────────────────────────

    def _build_main_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        spacing=4, margin=8)

        # DAILY — biggest buttons; the daily-use commands deserve emphasis.
        outer.pack_start(self._section_header("DAILY"),         False, False, 0)
        outer.pack_start(self._section_row(_DAILY, btn_h=72),   False, False, 0)

        # DIAGNOSTICS — medium buttons; same height, fewer items per row.
        outer.pack_start(self._section_header("DIAGNOSTICS"),   False, False, 0)
        outer.pack_start(self._section_row(_DIAG, btn_h=64),    False, False, 0)

        # CALIBRATION — compact buttons; touched once per setup so they don't
        # need the full real estate.
        outer.pack_start(self._section_header("CALIBRATION"),   False, False, 0)
        outer.pack_start(self._section_row(_CAL, btn_h=56),     False, False, 0)

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
