# sa_main.py — Stealth Autoloader KlipperScreen status overview panel
#
# Tab 1: 6-path status grid showing state, sensor dots, material, color swatch.
# Responsive to 480x272 (compact), 800x480 (standard), 1024x600 (large).

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import logging

logger = logging.getLogger('klipperscreen.sa_main')

# ── Responsive breakpoints ────────────────────────────────────────────────────
_BREAKPOINTS = [
    (500,  {'font': 11, 'row_h': 32, 'cols': 2}),
    (850,  {'font': 14, 'row_h': 44, 'cols': 4}),
    (9999, {'font': 16, 'row_h': 52, 'cols': 4}),
]

def _bp(width):
    for w, cfg in _BREAKPOINTS:
        if width <= w:
            return cfg
    return _BREAKPOINTS[-1][1]

# ── State styling ─────────────────────────────────────────────────────────────
_STATE_STYLE = {
    'loaded':  ('● LOADED',  '#388E3C'),
    'empty':   ('○ EMPTY',   '#616161'),
    'partial': ('≈ PARTIAL', '#E65100'),
    'unknown': ('? UNKNOWN', '#F9A825'),
}


class Panel:
    """KlipperScreen status overview panel for Stealth Autoloader."""

    def __init__(self, screen, title):
        self._screen = screen
        self._gtk    = screen.gtk
        self._title  = title
        self._path_rows = []
        self._btn_engage   = None
        self._btn_disengage = None

        self.content = self._build()

    def _build(self):
        w   = self._screen.width
        bp  = _bp(w)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        main_box.set_margin_start(8)
        main_box.set_margin_end(8)
        main_box.set_margin_top(4)

        # ── Header label ─────────────────────────────────────────────────────
        hdr = Gtk.Label(label="Stealth Autoloader — Path Status")
        hdr.get_style_context().add_class('title_2')
        hdr.set_halign(Gtk.Align.START)
        main_box.pack_start(hdr, False, False, 0)

        # ── Column headers ───────────────────────────────────────────────────
        hdr_grid = Gtk.Grid()
        hdr_grid.set_column_spacing(6)
        hdr_grid.set_row_spacing(0)
        for col, (label, expand) in enumerate([
            ('#',        False),
            ('STATE',    True),
            ('EN',       False),
            ('TH',       False),
            ('EX',       False),
            ('MATERIAL', False),
            ('COLOR',    True),
        ]):
            lbl = Gtk.Label(label=label)
            lbl.get_style_context().add_class('color4')
            lbl.set_halign(Gtk.Align.START)
            hdr_grid.attach(lbl, col, 0, 1, 1)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        main_box.pack_start(hdr_grid, False, False, 0)
        main_box.pack_start(sep, False, False, 2)

        # ── Path rows ────────────────────────────────────────────────────────
        rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._path_rows = []

        for i in range(6):
            row = self._make_row(i, bp)
            self._path_rows.append(row)
            rows_box.pack_start(row['grid'], False, False, 1)

        main_box.pack_start(rows_box, True, True, 0)

        # ── Action bar ───────────────────────────────────────────────────────
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_homogeneous(True)

        btn_home = self._gtk.Button(label="HOME")
        btn_home.connect("clicked", lambda b: self._send("SA_HOME"))
        btn_box.pack_start(btn_home, True, True, 0)

        self._btn_engage = self._gtk.Button(label="ENGAGE")
        self._btn_engage.connect("clicked", lambda b: self._send("SA_ENGAGE"))
        btn_box.pack_start(self._btn_engage, True, True, 0)

        self._btn_disengage = self._gtk.Button(label="DISENGAGE")
        self._btn_disengage.connect("clicked", lambda b: self._send("SA_DISENGAGE"))
        btn_box.pack_start(self._btn_disengage, True, True, 0)

        btn_status = self._gtk.Button(label="STATUS")
        btn_status.connect("clicked", lambda b: self._send("SA_STATUS"))
        btn_box.pack_start(btn_status, True, True, 0)

        main_box.pack_end(btn_box, False, False, 4)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(main_box)
        return scroll

    def _make_row(self, i, bp):
        grid = Gtk.Grid()
        grid.set_column_spacing(8)
        grid.set_row_spacing(0)

        path_lbl = Gtk.Label(label="T%d" % i)
        path_lbl.set_halign(Gtk.Align.CENTER)
        path_lbl.set_size_request(28, -1)

        state_lbl = Gtk.Label(label="? UNKNOWN")
        state_lbl.set_halign(Gtk.Align.START)
        state_lbl.set_size_request(90, -1)

        en_dot  = Gtk.Label(label="○")
        th_dot  = Gtk.Label(label="○")
        ex_dot  = Gtk.Label(label="○")

        mat_lbl   = Gtk.Label(label="---")
        mat_lbl.set_halign(Gtk.Align.START)
        mat_lbl.set_size_request(60, -1)

        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        swatch    = Gtk.DrawingArea()
        swatch.set_size_request(18, 18)
        swatch.connect("draw", self._draw_swatch, '#808080')
        color_lbl = Gtk.Label(label="---")
        color_lbl.set_halign(Gtk.Align.START)
        color_box.pack_start(swatch, False, False, 0)
        color_box.pack_start(color_lbl, True, True, 0)

        grid.attach(path_lbl,  0, 0, 1, 1)
        grid.attach(state_lbl, 1, 0, 1, 1)
        grid.attach(en_dot,    2, 0, 1, 1)
        grid.attach(th_dot,    3, 0, 1, 1)
        grid.attach(ex_dot,    4, 0, 1, 1)
        grid.attach(mat_lbl,   5, 0, 1, 1)
        grid.attach(color_box, 6, 0, 1, 1)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)

        return {
            'grid':      grid,
            'sep':       sep,
            'state_lbl': state_lbl,
            'en_dot':    en_dot,
            'th_dot':    th_dot,
            'ex_dot':    ex_dot,
            'mat_lbl':   mat_lbl,
            'swatch':    swatch,
            'color_lbl': color_lbl,
            'hex':       '#808080',
        }

    def _draw_swatch(self, widget, cr, color_hex):
        try:
            rgba = Gdk.RGBA()
            rgba.parse(color_hex)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 1.0)
        except Exception:
            cr.set_source_rgb(0.5, 0.5, 0.5)
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        cr.arc(w / 2, h / 2, min(w, h) / 2 - 1, 0, 6.2832)
        cr.fill()

    def activate(self):
        pass

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("stealth_autoloader")
        if not sa:
            return
        GLib.idle_add(self._refresh_rows, sa)

    def _refresh_rows(self, sa):
        states        = sa.get('path_states',        [])
        entry_fil     = sa.get('entry_filament',     [])
        th_fil        = sa.get('toolhead_filament',  [])
        ex_fil        = sa.get('extruder_filament',  [])
        materials     = sa.get('path_materials',     [])
        color_names   = sa.get('path_color_names',   [])
        color_hexes   = sa.get('path_color_hexes',   [])
        current_path  = sa.get('current_path', -1)
        servo_engaged = sa.get('servo_engaged', False)

        homed = current_path >= 0
        if self._btn_engage:
            self._btn_engage.set_sensitive(homed)
        if self._btn_disengage:
            self._btn_disengage.set_sensitive(homed)

        num = min(len(self._path_rows), len(states)) if states else 0
        for i in range(num):
            row   = self._path_rows[i]
            state = states[i] if i < len(states) else 'unknown'
            label, color = _STATE_STYLE.get(state, ('? UNKNOWN', '#F9A825'))

            row['state_lbl'].set_markup(
                '<span foreground="%s">%s</span>' % (color, label))

            for dot_key, fil_list in [
                ('en_dot', entry_fil), ('th_dot', th_fil), ('ex_dot', ex_fil)
            ]:
                has_fil = fil_list[i] if i < len(fil_list) else False
                dot_color = '#66BB6A' if has_fil else '#616161'
                symbol    = '●' if has_fil else '○'
                row[dot_key].set_markup(
                    '<span foreground="%s">%s</span>' % (dot_color, symbol))

            mat = materials[i] if i < len(materials) else ''
            row['mat_lbl'].set_text(mat if mat else '---')

            chex  = color_hexes[i]  if i < len(color_hexes)  else ''
            cname = color_names[i]  if i < len(color_names)  else ''
            if chex:
                row['hex'] = chex
                row['swatch'].connect("draw", self._draw_swatch, chex)
                row['swatch'].queue_draw()
            row['color_lbl'].set_text(cname if cname else '---')

        return False

    def _send(self, cmd):
        self._screen._ws.klippy.gcode_script(cmd)


def create_panel(*args):
    return Panel(*args)
