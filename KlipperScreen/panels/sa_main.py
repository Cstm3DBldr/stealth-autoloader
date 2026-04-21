import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sa_button_style as _sbs
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_main')

COLOR_SWATCH = '\u2b24'
EMPTY_SWATCH = '\u25ef'

_STATE_MARKUP = {
    'loaded':  ('<b>\u25cf LOADED</b>', '#388E3C'),
    'empty':   ('\u25cb EMPTY',        '#616161'),
    'partial': ('\u2248 PARTIAL',      '#E65100'),
    'unknown': ('? UNKNOWN',      '#F9A825'),
}
_DOT_ON     = '#388E3C'
_DOT_OFF    = '#616161'
_ENC_ACTIVE = '#42A5F5'
_ENC_IDLE   = '#616161'


def _rgba_from_hex(hex_c):
    rgba = Gdk.RGBA()
    if hex_c and Gdk.RGBA.parse(rgba, hex_c):
        return rgba
    if hex_c and Gdk.RGBA.parse(rgba, '#' + hex_c):
        return rgba
    Gdk.RGBA.parse(rgba, 'rgba(97,97,97,1)')
    return rgba


def _effective_state(i, states, entry, toolhead, extruder):
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
        self._last_sa = {}
        self._enc_distances = {}
        self._last_cal_state = ''

        _sbs.apply()

        scroll = self._gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        center_box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        self._grid = Gtk.Grid(row_spacing=2, column_spacing=14, margin=8,
                              row_homogeneous=True)
        self._grid.set_halign(Gtk.Align.CENTER)
        self._build_header()
        center_box.pack_start(self._grid, False, False, 0)
        scroll.add(center_box)
        self.content.pack_start(scroll, True, True, 0)

        bar = Gtk.Grid(row_spacing=4, column_spacing=4, margin=4,
                       row_homogeneous=True, column_homogeneous=True)
        for (label, cmd), (col, row) in zip(
            [("HOME",      "SA_HOME"),
             ("ENGAGE",    "SA_ENGAGE"),
             ("DISENGAGE", "SA_DISENGAGE"),
             ("REFRESH",   None)],
            [(0, 0), (1, 0), (0, 1), (1, 1)]
        ):
            btn = _sbs.make(label)
            if cmd:
                btn.connect("clicked", self._send, cmd)
            else:
                btn.connect("clicked", self._refresh)
            btn.set_hexpand(True)
            bar.attach(btn, col, row, 1, 1)
        self.content.pack_end(bar, False, False, 0)

    def _build_header(self):
        # Column order: #, STATE, EN, EX, TH, ENCODER, MATERIAL, COLOR
        for col, h in enumerate(["#", "STATE", "EN", "EX", "TH", "ENCODER", "MATERIAL", "COLOR"]):
            lbl = Gtk.Label(label=h)
            lbl.get_style_context().add_class("color4")
            lbl.set_halign(Gtk.Align.CENTER)
            self._grid.attach(lbl, col, 0, 1, 1)

    def _row_h(self):
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
            num_lbl.set_markup('<b>T%d</b>' % i)
            num_lbl.set_size_request(-1, rh)
            self._grid.attach(num_lbl, 0, row, 1, 1)
            self.labels['row_%d_num' % i] = num_lbl

            state_lbl = Gtk.Label(label="? UNKNOWN", halign=Gtk.Align.CENTER)
            self._grid.attach(state_lbl, 1, row, 1, 1)
            self.labels['row_%d_state' % i] = state_lbl

            # col 2=entry, col 3=extruder, col 4=toolhead
            for col, key in [(2, 'entry'), (3, 'extruder'), (4, 'toolhead')]:
                dot = Gtk.Label(label="\u25cb", halign=Gtk.Align.CENTER)
                self._grid.attach(dot, col, row, 1, 1)
                self.labels['row_%d_%s' % (i, key)] = dot

            # col 5 — encoder distance
            enc_lbl = Gtk.Label(label="\u2014", halign=Gtk.Align.CENTER)
            enc_lbl.set_size_request(58, -1)
            self._grid.attach(enc_lbl, 5, row, 1, 1)
            self.labels['row_%d_encoder' % i] = enc_lbl

            mat_lbl = Gtk.Label(label="---", halign=Gtk.Align.CENTER)
            self._grid.attach(mat_lbl, 6, row, 1, 1)
            self.labels['row_%d_material' % i] = mat_lbl

            color_box = Gtk.Box(spacing=6, halign=Gtk.Align.START,
                                valign=Gtk.Align.CENTER)
            swatch = Gtk.Label(label=EMPTY_SWATCH)
            color_name = Gtk.Label(label="---", halign=Gtk.Align.START,
                                   xalign=0.0, max_width_chars=20,
                                   ellipsize=3)
            color_box.pack_start(swatch,     False, False, 0)
            color_box.pack_start(color_name, False, False, 0)
            self._grid.attach(color_box, 7, row, 1, 1)
            self.labels['row_%d_swatch' % i] = swatch
            self.labels['row_%d_color'  % i] = color_name

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

    def _query_encoders(self, num_paths):
        distances = {}
        try:
            objs = "&".join("sa_encoder%%20%d" % i for i in range(num_paths))
            resp = self._screen.apiclient.send_request(
                "printer/objects/query?" + objs)
            if resp and 'status' in resp:
                status = resp['status']
                for i in range(num_paths):
                    key = "sa_encoder %d" % i
                    enc = status.get(key, {})
                    if enc:
                        distances[i] = float(enc.get('distance', 0.0))
        except Exception as e:
            logger.warning("sa_main: encoder query failed: %s", e)
        return distances

    def _refresh(self, widget=None):
        sa = self._query_sa()
        if sa:
            self._apply_sa(sa)
        if self._num_paths:
            encs = self._query_encoders(self._num_paths)
            self._enc_distances.update(encs)
            self._apply_encoders()

    def activate(self):
        objs = {"stealth_autoloader": None}
        num = self._num_paths or 6
        for i in range(num):
            objs["sa_encoder %d" % i] = None
        self._screen._ws.klippy.object_subscription({"objects": objs})

        sa = self._query_sa()
        if sa:
            self._last_sa = dict(sa)
            self._entry_prev = list(sa.get("entry_filament", []))
            self._apply_sa(self._last_sa)
            encs = self._query_encoders(sa.get("num_paths", 6))
            self._enc_distances.update(encs)
            self._apply_encoders()
        else:
            self._refresh()

    def process_update(self, action, data):
        if action != "notify_status_update":
            return

        updated_enc = False
        for key, val in data.items():
            if key.startswith("sa_encoder "):
                try:
                    idx = int(key.split()[-1])
                    self._enc_distances[idx] = float(val.get('distance', 0.0))
                    updated_enc = True
                except (ValueError, AttributeError):
                    pass

        sa = data.get("stealth_autoloader")
        if sa is None and not updated_enc:
            return

        if sa is not None:
            self._last_sa.update(sa)
            new_entry = self._last_sa.get("entry_filament", [])
            for i, active in enumerate(new_entry):
                was_active = self._entry_prev[i] if i < len(self._entry_prev) else False
                if not was_active and active:
                    GLib.idle_add(
                        self._screen.show_panel, 'sa_load_unload', 'Load / Unload')
            self._entry_prev = list(new_entry)

            cal = self._last_sa.get("cal_state", "")
            if cal != self._last_cal_state:
                if cal in ('load_purge', 'unload_done'):
                    import sa_ui_prefs as _prefs
                    if _prefs.get("popup_on_complete", True):
                        GLib.idle_add(self._screen.show_panel, 'sa_post_load', 'SA Action')
                elif cal:
                    # Calibration phase starting — open prompt panel
                    GLib.idle_add(self._screen.show_panel, 'sa_cal_prompt', 'SA Calibration')
            self._last_cal_state = cal

        GLib.idle_add(self._redraw)

    def _redraw(self):
        self._apply_sa(self._last_sa)
        self._apply_encoders()
        return False

    def _apply_encoders(self):
        for i in range(self._num_paths):
            lbl = self.labels.get('row_%d_encoder' % i)
            if lbl is None:
                continue
            dist = self._enc_distances.get(i, None)
            if dist is None:
                lbl.set_markup(
                    '<span foreground="%s" font_size="small">\u2014</span>' % _ENC_IDLE)
            elif abs(dist) > 0.1:
                lbl.set_markup(
                    '<span foreground="%s" font_size="small"><b>%.1fmm</b></span>'
                    % (_ENC_ACTIVE, dist))
            else:
                lbl.set_markup(
                    '<span foreground="%s" font_size="small">0.0</span>' % _ENC_IDLE)

    def _apply_sa(self, sa):
        num = sa.get("num_paths", 0)
        if num != self._num_paths:
            self._build_rows(num)
            objs = {"stealth_autoloader": None}
            for i in range(num):
                objs["sa_encoder %d" % i] = None
            self._screen._ws.klippy.object_subscription({"objects": objs})

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
            state = _effective_state(i, states, entry, toolhead, extruder)

            state_lbl = self.labels.get('row_%d_state' % i)
            if state_lbl:
                markup, color_hex = _STATE_MARKUP.get(state, ('? UNKNOWN', '#616161'))
                state_lbl.set_markup(
                    '<span font_size="large" foreground="%s">%s</span>'
                    % (color_hex, markup))

            # EN, EX, TH order
            for sensor_key, arr in [('entry', entry), ('extruder', extruder), ('toolhead', toolhead)]:
                dot = self.labels.get('row_%d_%s' % (i, sensor_key))
                if dot:
                    val = arr[i] if i < len(arr) else False
                    c = _DOT_ON if val else _DOT_OFF
                    dot.set_markup(
                        '<span font_size="large" foreground="%s">%s</span>'
                        % (c, "\u25cf" if val else "\u25cb"))

            mat_lbl = self.labels.get('row_%d_material' % i)
            if mat_lbl:
                mat = materials[i] if i < len(materials) and materials[i] else "---"
                mat_lbl.set_markup('<span font_size="large">%s</span>' % mat)

            swatch    = self.labels.get('row_%d_swatch' % i)
            color_lbl = self.labels.get('row_%d_color'  % i)
            hex_c  = hexes[i]  if i < len(hexes)  else ''
            name_c = colors[i] if i < len(colors) and colors[i] else "---"

            if swatch:
                if hex_c:
                    rgba = _rgba_from_hex(hex_c)
                    swatch.override_color(Gtk.StateType.NORMAL, rgba)
                    swatch.set_markup(
                        '<span font_size="large">%s</span>' % COLOR_SWATCH)
                else:
                    swatch.override_color(Gtk.StateType.NORMAL, grey_rgba)
                    swatch.set_markup(
                        '<span font_size="large">%s</span>' % EMPTY_SWATCH)
            if color_lbl:
                color_lbl.set_markup('<span font_size="large">%s</span>' % name_c)

        return False
