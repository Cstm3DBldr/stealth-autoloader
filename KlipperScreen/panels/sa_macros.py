import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sa_button_style as _sbs
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_macros')

# (label, gcode_template, needs_tool)
# Use {t} as placeholder for TOOL=N when needs_tool=True
_BTNS = [
    # ── Most used ─────────────────────────────────────────────────────────────
    ("HOME SELECTOR",        "SA_HOME",                          False),
    ("SELECT PATH",          "SA_SELECT TOOL={t}",               True),
    ("STATUS REPORT",        "SA_STATUS",                        False),
    ("ENGAGE",               "SA_ENGAGE",                        False),
    ("DISENGAGE",            "SA_DISENGAGE",                     False),
    # ── Calibration (in order) ────────────────────────────────────────────────
    ("BUZZ DRIVE",           "SA_BUZZ_DRIVE",                    False),
    ("BUZZ SELECTOR",        "SA_BUZZ_SELECTOR",                 False),
    ("CAL SELECTOR",         "SA_CALIBRATE_SELECTOR",            False),
    ("CAL DRIVE",            "SA_CALIBRATE_DRIVE",               False),
    ("CAL ENCODER SPEED",    "SA_CALIBRATE_ENCODER_SPEED",       False),
    ("CAL ENCODER",          "SA_CALIBRATE_ENCODER TOOL={t}",    True),
    ("CAL BOWDEN",           "SA_CALIBRATE_BOWDEN TOOL={t}",     True),
]


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        super().__init__(screen, title or "SA Macros")
        _sbs.apply()

        self._num_paths  = 6
        self._pending_cmd = None   # gcode template waiting for tool selection

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack.set_transition_duration(150)

        self._stack.add_named(self._build_main_page(), "main")
        self._stack.add_named(self._build_tool_page(), "tool")

        self.content.add(self._stack)

    # ── Main macros page ──────────────────────────────────────────────────────

    def _build_main_page(self):
        grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True,
                        row_spacing=6, column_spacing=6, margin=8)

        for i, (label, gcode, needs_tool) in enumerate(_BTNS):
            btn = _sbs.make(label)
            if needs_tool:
                btn.connect("clicked", self._pick_tool, gcode)
            else:
                btn.connect("clicked", self._send, gcode)
            grid.attach(btn, i % 2, i // 2, 1, 1)

        return grid

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
        back_btn.connect("clicked", lambda w: self._stack.set_visible_child_name("main"))
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

    # ── Handlers ─────────────────────────────────────────────────────────────

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

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def activate(self):
        sa = self._printer.data.get("stealth_autoloader", {})
        self._num_paths = sa.get("num_paths", 6)
        self._stack.set_visible_child_name("main")

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("stealth_autoloader")
        if sa is not None:
            n = sa.get("num_paths")
            if n is not None:
                self._num_paths = n
        return False
