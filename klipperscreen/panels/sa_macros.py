# sa_macros.py — Stealth Autoloader KlipperScreen macro/diagnostics panel
#
# Tab 3: Motion, Calibration, and Diagnostics button grid.
# Homing-dependent buttons are disabled when selector is unhomed (current_path == -1).

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import logging

logger = logging.getLogger('klipperscreen.sa_macros')

# (label, gcode, requires_homed)
_MOTION_BTNS = [
    ("⌂  HOME SELECTOR",   "SA_HOME",       False),
    ("▲  ENGAGE DRIVE",    "SA_ENGAGE",     True),
    ("▼  DISENGAGE DRIVE", "SA_DISENGAGE",  True),
]

_DIAGNOSTICS_BTNS = [
    ("▤  STATUS REPORT",   "SA_STATUS",     False),
    ("↕  ENCODER QUERY",   "SA_ENCODER_QUERY", False),
    ("⚙  BUZZ DRIVE",      "SA_BUZZ_DRIVE", False),
    ("⚙  BUZZ SELECTOR",   "SA_BUZZ_SELECTOR", False),
]

_CALIBRATION_BTNS = [
    ("⚙  CAL SELECTOR",    "SA_CALIBRATE_SELECTOR", False),
    ("⚙  CAL DRIVE",       "SA_CALIBRATE_DRIVE",    False),
    ("⚙  CAL ENCODER T0",  "SA_CALIBRATE_ENCODER TOOL=0", False),
    ("⚙  CAL BOWDEN T0",   "SA_CALIBRATE_BOWDEN TOOL=0",  True),
    ("✏  SET STATE T0",    "SA_SET_STATE TOOL=0 STATE=loaded", False),
]


class Panel:
    """KlipperScreen macros / diagnostics panel for Stealth Autoloader."""

    def __init__(self, screen, title):
        self._screen = screen
        self._gtk    = screen.gtk
        self._title  = title
        self._homed  = False
        self._homed_btns = []

        self.content = self._build()

    def _build(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        main_box.set_margin_start(12)
        main_box.set_margin_end(12)
        main_box.set_margin_top(8)

        hdr = Gtk.Label(label="Stealth Autoloader — Macros & Diagnostics")
        hdr.get_style_context().add_class('title_2')
        hdr.set_halign(Gtk.Align.START)
        main_box.pack_start(hdr, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._add_section(left, "MOTION",      _MOTION_BTNS)
        self._add_section(left, "CALIBRATION", _CALIBRATION_BTNS)
        body.pack_start(left, True, True, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        body.pack_start(sep, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._add_section(right, "DIAGNOSTICS", _DIAGNOSTICS_BTNS)
        body.pack_start(right, True, True, 0)

        main_box.pack_start(body, True, True, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(main_box)
        return scroll

    def _add_section(self, parent, title, buttons):
        lbl = Gtk.Label(label=title)
        lbl.get_style_context().add_class('color4')
        lbl.set_halign(Gtk.Align.START)
        parent.pack_start(lbl, False, False, 0)

        for label, gcode, req_homed in buttons:
            btn = Gtk.Button(label=label)
            btn.set_halign(Gtk.Align.FILL)
            btn.set_size_request(-1, 44)
            btn.connect("clicked", self._on_btn, gcode)
            if req_homed:
                btn.set_sensitive(self._homed)
                self._homed_btns.append(btn)
            parent.pack_start(btn, False, False, 0)

    def _on_btn(self, btn, gcode):
        self._screen._ws.klippy.gcode_script(gcode)

    def activate(self):
        pass

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("stealth_autoloader")
        if not sa:
            return
        GLib.idle_add(self._refresh_state, sa)

    def _refresh_state(self, sa):
        self._homed = sa.get('current_path', -1) >= 0
        for btn in self._homed_btns:
            btn.set_sensitive(self._homed)
        return False


def create_panel(*args):
    return Panel(*args)
