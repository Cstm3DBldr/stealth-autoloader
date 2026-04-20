import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import logging
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_main')

COLOR_SWATCH = '⬤'
EMPTY_SWATCH = '◯'

# Pango markup foreground must be #rrggbb hex — rgba() is not supported
_STATE_MARKUP = {
    'loaded':  ('<b>● LOADED</b>', '#388E3C'),
    'empty':   ('○ EMPTY',        '#616161'),
    'partial': ('≈ PARTIAL',      '#E65100'),
    'unknown': ('? UNKNOWN',      '#F9A825'),
}
_DOT_ON  = '#388E3C'
_DOT_OFF = '#616161'


def _rgba_from_hex(hex_c):
    """Parse #rrggbb or rrggbb to Gdk.RGBA. Returns grey on failure."""
    rgba = Gdk.RGBA()
    if hex_c and Gdk.RGBA.parse(rgba, hex_c):
        return rgba
    if hex_c and Gdk.RGBA.parse(rgba, '#' + hex_c):
        return rgba
    Gdk.RGBA.parse(rgba, 'rgba(97,97,97,1)')
    return rgba


def _effective_state(i, states, entry, toolhead, extruder):
    """Derive display state from live sensors; fall back to saved state."""
    e  = entry[i]    if i < len(entry)    else None
    th = toolhead[i] if i < len(toolhead) else None
    ex = extruder[i] if i < len(extruder) else None
    if e is None:
        return states[i] if i < len(states) else 'unknown'
    if not e and not th and not ex:
        return 'empty'
    if e and th and ex:
        return 'loaded'
    if e or th or ex:
        return 'partial'
    return states[i] if i < len(states) else 'unknown'


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        super().__init__(screen, title or "SA Status")
        self.labels = {}
        self._num_paths = 0
        self._entry_prev = []

        _css = Gtk.CssProvider()
        _css.load_from_data(b".sa-compact-btn { padding-top: 1px; padding-bottom: 1px; min-height: 0; }")
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), _css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        scroll = self._gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # Outer box centers the grid horizontally
        center_box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.START)

        self._grid = Gtk.Grid(row_spacing=2, column_spacing=16, margin=8,
                              row_homogeneous=True)
        self._grid.set_halign(Gtk.Align.CENTER)
        self._build_header()
        center_box.pack_start(self._grid, False, False, 0)
        scroll.add(center_box)
        self.content.pack_start(scroll, True, True, 0)

        bar = Gtk.Box(spacing=2, margin=0)
        for label, cmd in [("HOME", "SA_HOME"), ("ENGAGE", "SA_ENGAGE"),
                           ("DISENGAGE", "SA_DISENGAGE"), ("REFRESH", None)]:
            btn = self._gtk.Button(label=label, style="color1", scale=self.bts)
            btn.set_size_request(-1, 26)
            btn.get_style_context().add_class("sa-compact-btn")
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
            lbl.set_halign(Gtk.Align.CENTER)
            self._grid.attach(lbl, col, 0, 1, 1)

    def _row_h(self):
        """Target row height — compact but readable."""
        return max(32, (self._screen.height - 160) // (max(self._num_paths, 1) * 2))

    def _build_rows(self, num_paths):
        for key in [k for k in self.labels if k.startswith('row_')]:
            w = self.labels.pop(key)
            self._grid.remove(w)

        self._num_paths = num_paths
        rh = self._row_h()
        for i in range(num_paths):
            row = i + 1

            num_lbl = Gtk.Label(halign=Gtk.Align.CENTER)
            num_lbl.set_markup(f'<b>T{i}</b>')
            num_lbl.set_size_request(-1, rh)
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

            color_box = Gtk.Box(spacing=6, halign=Gtk.Align.START,
                                valign=Gtk.Align.CENTER)
            swatch = Gtk.Label(label=EMPTY_SWATCH)
            color_name = Gtk.Label(label="---", halign=Gtk.Align.START,
                                   xalign=0.0, max_width_chars=20,
                                   ellipsize=3)
            color_box.pack_start(swatch,     False, False, 0)
            color_box.pack_start(color_name, False, False, 0)
            self._grid.attach(color_box, 6, row, 1, 1)
            self.labels[f'row_{i}_swatch'] = swatch
            self.labels[f'row_{i}_color']  = color_name

        self._grid.show_all()

    def _send(self, widget, gcode):
        self._screen._ws.klippy.gcode_script(gcode)

    def _query_sa(self):
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
        self._screen._ws.klippy.object_subscription(
            {"objects": {"stealth_autoloader": None}})
        sa = self._query_sa()
        if sa:
            self._entry_prev = list(sa.get("entry_filament", []))
            self._apply_sa(sa)
        else:
            self._refresh()

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("stealth_autoloader")
        if sa is None:
            return
        new_entry = sa.get("entry_filament", [])
        for i, active in enumerate(new_entry):
            was_active = self._entry_prev[i] if i < len(self._entry_prev) else False
            if not was_active and active:
                GLib.idle_add(
                    self._screen.show_panel, 'sa_load_unload', 'Load / Unload')
        self._entry_prev = list(new_entry)
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
            # Derive state from live sensors first
            state = _effective_state(i, states, entry, toolhead, extruder)

            state_lbl = self.labels.get(f'row_{i}_state')
            if state_lbl:
                markup, color_hex = _STATE_MARKUP.get(state, ('? UNKNOWN', '#616161'))
                state_lbl.set_markup(
                    f'<span font_size="large" foreground="{color_hex}">{markup}</span>')

            for sensor_key, arr in [('entry', entry), ('toolhead', toolhead), ('extruder', extruder)]:
                dot = self.labels.get(f'row_{i}_{sensor_key}')
                if dot:
                    val = arr[i] if i < len(arr) else False
                    c = _DOT_ON if val else _DOT_OFF
                    dot.set_markup(f'<span font_size="large" foreground="{c}">{"●" if val else "○"}</span>')

            mat_lbl = self.labels.get(f'row_{i}_material')
            if mat_lbl:
                mat = materials[i] if i < len(materials) and materials[i] else "---"
                mat_lbl.set_markup(f'<span font_size="large">{mat}</span>')

            swatch    = self.labels.get(f'row_{i}_swatch')
            color_lbl = self.labels.get(f'row_{i}_color')
            hex_c  = hexes[i]  if i < len(hexes)  else ''
            name_c = colors[i] if i < len(colors) and colors[i] else "---"

            if swatch:
                if hex_c:
                    rgba = _rgba_from_hex(hex_c)
                    swatch.override_color(Gtk.StateType.NORMAL, rgba)
                    swatch.set_markup(f'<span font_size="large">{COLOR_SWATCH}</span>')
                else:
                    swatch.override_color(Gtk.StateType.NORMAL, grey_rgba)
                    swatch.set_markup(f'<span font_size="large">{EMPTY_SWATCH}</span>')
            if color_lbl:
                color_lbl.set_markup(f'<span font_size="large">{name_c}</span>')

        return False
