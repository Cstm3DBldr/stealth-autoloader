import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_home')

# Layout: 6-column grid (LCM of 2 and 3)
#   Row 0: STATUS (span 3) | LOAD / UNLOAD (span 3)   — 2 wide buttons
#   Row 1: MACROS (span 2) | CALIBRATION (span 2) | SETTINGS (span 2)

_TOP_ROW = [
    ("STATUS",        "sa_main",              "SA Status",      "color1", "spoolman"),
    ("LOAD / UNLOAD", "sa_load_unload",       "Load / Unload",  "color3", "load"),
]

_BOT_ROW = [
    ("MACROS",        "sa_macros",            "SA Macros",      "color2", "move"),
    ("CALIBRATION",   "sa_calibration_guide", "SA Calibration", "color1", "settings"),
    ("SETTINGS",      "sa_settings",          "SA Settings",    "color3", "settings"),
]


class Panel(ScreenPanel):
    """Stealth Autoloader home — 2 wide top + 3 equal bottom, native KS style."""

    def __init__(self, screen, title):
        super().__init__(screen, title or "Autoloader")

        grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True,
                        row_spacing=6, column_spacing=6, margin=10)

        # Top row — 2 buttons each spanning 3 of 6 columns
        for idx, (label, panel, ptitle, color, icon) in enumerate(_TOP_ROW):
            btn = self._gtk.Button(icon, label, color)
            btn.connect("clicked", self._open_panel, panel, ptitle)
            grid.attach(btn, idx * 3, 0, 3, 1)

        # Bottom row — 3 buttons each spanning 2 of 6 columns
        for idx, (label, panel, ptitle, color, icon) in enumerate(_BOT_ROW):
            btn = self._gtk.Button(icon, label, color)
            btn.connect("clicked", self._open_panel, panel, ptitle)
            grid.attach(btn, idx * 2, 1, 2, 1)

        self.content.add(grid)

    def _open_panel(self, widget, panel_name, panel_title):
        self._screen.show_panel(panel_name, panel_title)

    def activate(self):
        pass

    def process_update(self, action, data):
        pass
