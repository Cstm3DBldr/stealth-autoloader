import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import logging
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_main')

COLOR_SWATCH = '⬤'
EMPTY_SWATCH = '◯'

_STATE_MARKUP = {
    'loaded':  ('<b>● LOADED</b>',   'rgba(56,142,60,1)'),
    'empty':   ('○ EMPTY',           'rgba(97,97,97,1)'),
    'partial': ('≈ PARTIAL',         'rgba(230,81,0,1)'),
    'unknown': ('? UNKNOWN',         'rgba(249,168,37,1)'),
}


def _rgba_from_hex(hex_c):
    """Parse #rrggbb or rrggbb to Gdk.RGBA. Returns grey on failure."""
    rgba = Gdk.RGBA()
    if hex_c and Gdk.RGBA.parse(rgba, hex_c):
        return rgba
    if hex_c and Gdk.RGBA.parse(rgba, '#' + hex_c):
        return rgba
    Gdk.RGBA.parse(rgba, 'rgba(97,97,97,1)')
    return rgba


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        super().__init__(screen, title or "SA Status")
        self.labels = {}
        self._num_paths = 0

        scroll = self._gtk.ScrolledWindow()
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
        # Remove old row widgets
        for key in [k for k in self.labels if k.startswith('row_')]:
            w = self.labels.pop(key)
            self._grid.remove(w)

        self._num_paths = num_paths
        for i in range(num_paths):
            row = i + 1

            num_lbl = Gtk.Label(label=f"T{i}", halign=Gtk.Align.CENTER)
            self._grid.attach(num_lbl, 0, row, 1, 1)
            self.labels[f'row_{i}_num'] = num_lbl

            state_lbl = Gtk.Label(label="? UNKNOWN", halign=Gtk.Align.CENTER)
            self._grid.attach(state_lbl, 1, row, 1, 1)
            self.labels[f'row_{i}_state'] = state_lbl

            for col, key in [(2, 'entry'), (3, 'toolhead'), (4, 'extruder')]:
                dot = Gtk.Label(label="○", halign=Gtk.Align.CENTER)
                self._grid.attach(dot, col, row, 1, 1)
                self.labels[f'row_{i}_{key}'] = dot

            mat_lbl = Gtk.Label(label="---", halign=Gtk.Align.CENTER)
            self._grid.attach(mat_lbl, 5, row, 1, 1)
            self.labels[f'row_{i}_material'] = mat_lbl

            color_box = Gtk.Box(spacing=4, halign=Gtk.Align.START)
            swatch = Gtk.Label(label=EMPTY_SWATCH)
            color_name = Gtk.Label(label="---", halign=Gtk.Align.START)
            color_box.pack_start(swatch, False, False, 0)
            color_box.pack_start(color_name, False, False, 0)
            self._grid.attach(color_box, 6, row, 1, 1)
            self.labels[f'row_{i}_swatch'] = swatch
            self.labels[f'row_{i}_color'] = color_name

        self._grid.show_all()

    def _send(self, widget, gcode):
        self._screen._ws.klippy.gcode_script(gcode)

    def _query_sa(self):
        """Direct API query — bypasses printer.data which only has subscribed objects."""
        try:
            resp = self._screen.apiclient.send_request(
                "printer/objects/query?stealth_autoloader")
            if resp and 'status' in resp:
                return resp['status'].get('stealth_autoloader', {})
        except Exception as e:
            logger.warning("sa_main: query failed: %s", e)
        return {}

    def _refresh(self, widget=None):
        sa = self._query_sa()
        if sa:
            self._apply_sa(sa)

    def activate(self):
        # Subscribe so process_update receives future stealth_autoloader events
        self._screen._ws.klippy.object_subscription(
            {"objects": {"stealth_autoloader": None}})
        self._refresh()

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("stealth_autoloader")
        if sa is not None:
            GLib.idle_add(self._apply_sa, sa)

    def _apply_sa(self, sa):
        num = sa.get("num_paths", 0)
        if num != self._num_paths:
            self._build_rows(num)

        states    = sa.get("path_states",      [])
        entry     = sa.get("entry_filament",    [])
        toolhead  = sa.get("toolhead_filament", [])
        extruder  = sa.get("extruder_filament", [])
        materials = sa.get("path_materials",    [])
        colors    = sa.get("path_color_names",  [])
        hexes     = sa.get("path_color_hexes",  [])

        grey_rgba = Gdk.RGBA()
        Gdk.RGBA.parse(grey_rgba, 'rgba(97,97,97,1)')

        for i in range(self._num_paths):
            state = states[i] if i < len(states) else 'unknown'

            state_lbl = self.labels.get(f'row_{i}_state')
            if state_lbl:
                markup, color_str = _STATE_MARKUP.get(state, ('? UNKNOWN', 'rgba(97,97,97,1)'))
                state_lbl.set_markup(f'<span foreground="{color_str}">{markup}</span>')

            for sensor_key, arr in [('entry', entry), ('toolhead', toolhead), ('extruder', extruder)]:
                dot = self.labels.get(f'row_{i}_{sensor_key}')
                if dot:
                    val = arr[i] if i < len(arr) else False
                    c = 'rgba(56,142,60,1)' if val else 'rgba(97,97,97,1)'
                    dot.set_markup(f'<span foreground="{c}">{"●" if val else "○"}</span>')

            mat_lbl = self.labels.get(f'row_{i}_material')
            if mat_lbl:
                mat_lbl.set_text(materials[i] if i < len(materials) and materials[i] else "---")

            swatch = self.labels.get(f'row_{i}_swatch')
            color_lbl = self.labels.get(f'row_{i}_color')
            hex_c  = hexes[i]  if i < len(hexes)  else ''
            name_c = colors[i] if i < len(colors) and colors[i] else "---"

            if swatch:
                if hex_c:
                    rgba = _rgba_from_hex(hex_c)
                    swatch.override_color(Gtk.StateType.NORMAL, rgba)
                    swatch.set_text(COLOR_SWATCH)
                else:
                    swatch.override_color(Gtk.StateType.NORMAL, grey_rgba)
                    swatch.set_text(EMPTY_SWATCH)
            if color_lbl:
                color_lbl.set_text(name_c)

        return False
