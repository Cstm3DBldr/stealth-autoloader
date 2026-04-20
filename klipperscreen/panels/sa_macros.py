import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import logging
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_macros')

_BTNS = [
    # (label, gcode, requires_homed, color)
    ("HOME SELECTOR",   "SA_HOME",              False, "color1"),
    ("ENGAGE DRIVE",    "SA_ENGAGE",            True,  "color2"),
    ("DISENGAGE DRIVE", "SA_DISENGAGE",         True,  "color2"),
    ("STATUS REPORT",   "SA_STATUS",            False, "color3"),
    ("BUZZ DRIVE",      "SA_BUZZ_DRIVE",        False, "color4"),
    ("BUZZ SELECTOR",   "SA_BUZZ_SELECTOR",     False, "color4"),
    ("CAL SELECTOR",    "SA_CALIBRATE_SELECTOR",False, "color4"),
    ("CAL ENCODER T0",  "SA_CALIBRATE_ENCODER TOOL=0", False, "color4"),
]


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        super().__init__(screen, title or "SA Macros")
        self._homed_btns = []

        grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True,
                        row_spacing=5, column_spacing=5, margin=10)

        for i, (label, gcode, req_homed, style) in enumerate(_BTNS):
            btn = self._gtk.Button(label=label, style=style, scale=self.bts)
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
