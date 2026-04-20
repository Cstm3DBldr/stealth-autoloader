import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sa_button_style as _sbs
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_macros')

_BTNS = [
    # (label, gcode, requires_homed)
    ("HOME SELECTOR",   "SA_HOME",                       False),
    ("ENGAGE DRIVE",    "SA_ENGAGE",                     True),
    ("DISENGAGE DRIVE", "SA_DISENGAGE",                  True),
    ("STATUS REPORT",   "SA_STATUS",                     False),
    ("BUZZ DRIVE",      "SA_BUZZ_DRIVE",                 False),
    ("BUZZ SELECTOR",   "SA_BUZZ_SELECTOR",              False),
    ("CAL SELECTOR T0", "SA_CALIBRATE_SELECTOR TOOL=0",   False),
    ("CAL ENCODER",     "SA_CALIBRATE_ENCODER",           False),
]


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        super().__init__(screen, title or "SA Macros")
        _sbs.apply()
        self._homed_btns = []

        grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True,
                        row_spacing=6, column_spacing=6, margin=8)

        for i, (label, gcode, req_homed) in enumerate(_BTNS):
            btn = _sbs.make(label)
            btn.connect("clicked", self._send, gcode)
            if req_homed:
                self._homed_btns.append(btn)
            grid.attach(btn, i % 2, i // 2, 1, 1)

        self.content.add(grid)

    def _send(self, widget, gcode):
        self._screen._ws.klippy.gcode_script(gcode)

    def activate(self):
        data = self._printer.data
        self._update_homed(data.get("stealth_autoloader", {}))

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("stealth_autoloader")
        if sa is not None:
            GLib.idle_add(self._update_homed, sa)

    def _update_homed(self, sa):
        homed = sa.get("current_path", -1) >= 0
        for btn in self._homed_btns:
            btn.set_sensitive(homed)
        return False
