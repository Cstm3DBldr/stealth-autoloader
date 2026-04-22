import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sa_button_style as _sbs
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_home')

# 2-row × 3-column navigation grid
# (label, panel_name, panel_title)
_TILES = [
    ("STATUS",      "sa_main",              "SA Status"),
    ("LOAD / UNLOAD", "sa_load_unload",     "Load / Unload"),
    ("MACROS",      "sa_macros",            "SA Macros"),
    ("CALIBRATION", "sa_calibration_guide", "SA Calibration"),
    ("SETTINGS",    "sa_settings",          "SA Settings"),
    (None,          None,                   None),           # empty cell
]


class Panel(ScreenPanel):
    """Stealth Autoloader home — 2×3 navigation grid."""

    def __init__(self, screen, title):
        super().__init__(screen, title or "Autoloader")
        _sbs.apply()

        grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True,
                        row_spacing=8, column_spacing=8, margin=12)

        for idx, (label, panel, ptitle) in enumerate(_TILES):
            col = idx % 3
            row = idx // 3
            if label is None:
                # Empty placeholder so grid stays even
                grid.attach(Gtk.Box(), col, row, 1, 1)
                continue
            btn = _sbs.make(label)
            btn.connect("clicked", self._open_panel, panel, ptitle)
            grid.attach(btn, col, row, 1, 1)

        self.content.add(grid)

    def _open_panel(self, widget, panel_name, panel_title):
        self._screen.show_panel(panel_name, panel_title)

    def activate(self):
        pass

    def process_update(self, action, data):
        pass
