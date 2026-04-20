import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import logging
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_main')

_STATE_LABEL = {
    'loaded':  '● LOADED',
    'empty':   '○ EMPTY',
    'partial': '≈ PARTIAL',
    'unknown': '? UNKNOWN',
}
_STATE_COLOR = {
    'loaded':  'rgba(56,142,60,1)',
    'empty':   'rgba(97,97,97,1)',
    'partial': 'rgba(230,81,0,1)',
    'unknown': 'rgba(249,168,37,1)',
}


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        super().__init__(screen, title or "SA Status")
        self._row_widgets = []

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._grid = Gtk.Grid(row_spacing=2, column_spacing=8, margin=8,
                              row_homogeneous=True)
        self._build_header()
        scroll.add(self._grid)
        self.content.pack_start(scroll, True, True, 0)

        bar = Gtk.Box(spacing=8, margin=6)
        for label, cmd in [("HOME", "SA_HOME"), ("ENGAGE", "SA_ENGAGE"),
                           ("DISENGAGE", "SA_DISENGAGE"), ("REFRESH", None)]:
            btn = self._gtk.Button(label=label, style="color1", scale=self.bts)
            if cmd:
                btn.connect("clicked", self._send, cmd)
            else:
                btn.connect("clicked", self._refresh)
            bar.pack_start(btn, True, True, 0)
        self.content.pack_end(bar, False, False, 0)

    def _build_header(self):
        for col, h in enumerate(["#", "STATE", "EN", "TH", "EX", "MATERIAL", "COLOR"]):
            lbl = Gtk.Label(label=h)
            lbl.get_style_context().add_class("color4")
            self._grid.attach(lbl, col, 0, 1, 1)

    def _build_rows(self, num_paths):
        for w in self._row_widgets:
            for widget in w['_all']:
                self._grid.remove(widget)
        self._row_widgets.clear()

        for i in range(num_paths):
            row = i + 1
            w = {'_all': [], '_swatch_hex': ''}

            def _attach(widget, col):
                self._grid.attach(widget, col, row, 1, 1)
                w['_all'].append(widget)
                return widget

            _attach(Gtk.Label(label=f"T{i}", halign=Gtk.Align.CENTER), 0)
            w['state'] = _attach(Gtk.Label(label="? UNKNOWN", halign=Gtk.Align.CENTER), 1)

            for col, key in [(2, 'entry'), (3, 'toolhead'), (4, 'extruder')]:
                w[key] = _attach(Gtk.Label(label="○", halign=Gtk.Align.CENTER), col)

            w['material'] = _attach(Gtk.Label(label="---", halign=Gtk.Align.CENTER), 5)

            color_box = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
            swatch = Gtk.DrawingArea()
            swatch.set_size_request(18, 18)
            swatch.set_valign(Gtk.Align.CENTER)
            swatch.connect("draw", self._draw_swatch, i)
            w['swatch'] = swatch
            color_box.pack_start(swatch, False, False, 0)
            w['color_name'] = Gtk.Label(label="---", halign=Gtk.Align.START)
            color_box.pack_start(w['color_name'], False, False, 0)
            _attach(color_box, 6)

            self._row_widgets.append(w)

        self._grid.show_all()

    def _draw_swatch(self, widget, cr, idx):
        if idx >= len(self._row_widgets):
            return
        hex_c = self._row_widgets[idx].get('_swatch_hex', '')
        if not hex_c:
            return
        try:
            rgba = Gdk.RGBA()
            rgba.parse(hex_c)
            Gdk.cairo_set_source_rgba(cr, rgba)
            ww = widget.get_allocated_width()
            wh = widget.get_allocated_height()
            cr.arc(ww / 2, wh / 2, min(ww, wh) / 2 - 1, 0, 6.2832)
            cr.fill()
        except Exception:
            pass

    def _send(self, widget, gcode):
        self._screen._ws.klippy.gcode_script(gcode)

    def _refresh(self, widget=None):
        sa = self._printer.data.get("stealth_autoloader", {})
        if sa:
            self._apply_sa(sa)

    def activate(self):
        self._refresh()

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("stealth_autoloader")
        if sa is not None:
            GLib.idle_add(self._apply_sa, sa)

    def _apply_sa(self, sa):
        num = sa.get("num_paths", 0)
        if num != len(self._row_widgets):
            self._build_rows(num)

        states    = sa.get("path_states",      [])
        entry     = sa.get("entry_filament",    [])
        toolhead  = sa.get("toolhead_filament", [])
        extruder  = sa.get("extruder_filament", [])
        materials = sa.get("path_materials",    [])
        colors    = sa.get("path_color_names",  [])
        hexes     = sa.get("path_color_hexes",  [])

        for i, w in enumerate(self._row_widgets):
            state = states[i] if i < len(states) else 'unknown'
            clr   = _STATE_COLOR.get(state, '#888')
            lbl   = _STATE_LABEL.get(state, '? UNKNOWN')
            w['state'].set_markup(f'<span foreground="{clr}"><b>{lbl}</b></span>')

            for key, arr in [('entry', entry), ('toolhead', toolhead), ('extruder', extruder)]:
                val = arr[i] if i < len(arr) else False
                dot_clr = 'rgba(56,142,60,1)' if val else 'rgba(97,97,97,1)'
                w[key].set_markup(f'<span foreground="{dot_clr}">{"●" if val else "○"}</span>')

            w['material'].set_text(materials[i] if i < len(materials) and materials[i] else "---")

            hex_c  = hexes[i]  if i < len(hexes)   else ''
            name_c = colors[i] if i < len(colors) and colors[i] else "---"
            w['_swatch_hex'] = hex_c
            w['swatch'].queue_draw()
            w['color_name'].set_text(name_c)

        return False
