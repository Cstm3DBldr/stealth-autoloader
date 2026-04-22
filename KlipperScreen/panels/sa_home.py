import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sa_button_style as _sbs
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_home')

# Layout: 6-column grid (LCM of 2 and 3)
#   Row 0: STATUS (span 3) | LOAD / UNLOAD (span 3)   — 2 wide buttons
#   Row 1: MACROS (span 2) | CALIBRATION (span 2) | SETTINGS (span 2)  — 3 buttons

_TOP_ROW = [
    ("STATUS",        "sa_main",              "SA Status"),
    ("LOAD / UNLOAD", "sa_load_unload",       "Load / Unload"),
]

_BOT_ROW = [
    ("MACROS",        "sa_macros",            "SA Macros"),
    ("CALIBRATION",   "sa_calibration_guide", "SA Calibration"),
    ("SETTINGS",      "sa_settings",          "SA Settings"),
]


class Panel(ScreenPanel):
    """Stealth Autoloader home — 2 wide top + 3 equal bottom."""

    def __init__(self, screen, title):
        super().__init__(screen, title or "Autoloader")
        _sbs.apply()

        grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True,
                        row_spacing=8, column_spacing=8, margin=12)

        # Top row — 2 buttons each spanning 3 of 6 columns
        for idx, (label, panel, ptitle) in enumerate(_TOP_ROW):
            btn = _sbs.make(label)
            btn.connect("clicked", self._open_panel, panel, ptitle)
            grid.attach(btn, idx * 3, 0, 3, 1)

        # Bottom row — 3 buttons each spanning 2 of 6 columns
        for idx, (label, panel, ptitle) in enumerate(_BOT_ROW):
            btn = _sbs.make(label)
            btn.connect("clicked", self._open_panel, panel, ptitle)
            grid.attach(btn, idx * 2, 1, 2, 1)

        self.content.add(grid)

    def _open_panel(self, widget, panel_name, panel_title):
        self._screen.show_panel(panel_name, panel_title)

    def activate(self):
        pass

    def process_update(self, action, data):
        pass
