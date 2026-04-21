import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import logging
import sys, os

_panels_dir = os.path.dirname(os.path.abspath(__file__))
_ks_root    = os.path.dirname(_panels_dir)
for _p in (_ks_root, _panels_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sa_button_style as _sbs
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_post_load')

_GREEN  = '#388E3C'
_ORANGE = '#E65100'
_GREY   = '#37474F'
_RED    = '#B71C1C'


class Panel(ScreenPanel):
    """Post-load / post-unload action panel.

    Appears automatically when cal_state becomes 'load_purge' or 'unload_done'.
    Sends SA_RESPOND VALUE=<action> for each button press.
    """

    def __init__(self, screen, title):
        super().__init__(screen, title or "SA Action")
        _sbs.apply()

        self._active    = False
        self._cal_state = ''
        self._cal_path  = -1
        self._num_paths = 6

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=10)

        # Status header
        self._hdr = Gtk.Label()
        self._hdr.set_halign(Gtk.Align.CENTER)
        self._hdr.set_markup(
            '<span font_size="large" foreground="%s"><b>SA Action</b></span>' % _GREEN)
        outer.pack_start(self._hdr, False, False, 0)

        self._sub = Gtk.Label()
        self._sub.set_halign(Gtk.Align.CENTER)
        outer.pack_start(self._sub, False, False, 0)

        outer.pack_start(Gtk.Separator(), False, False, 4)

        # Primary action buttons (row 1)
        row1 = Gtk.Box(spacing=8)
        self._more_btn = self._make_action_btn(
            "\u21ba  PURGE 60mm", _GREEN, self._do_more)
        self._park_btn = self._make_action_btn(
            "\U0001f3d4  PARK",    None,   self._do_park)
        self._exit_btn = self._make_action_btn(
            "\u2715  EXIT",        _RED,   self._do_exit)
        row1.pack_start(self._more_btn, True, True, 0)
        row1.pack_start(self._park_btn, True, True, 0)
        row1.pack_start(self._exit_btn, True, True, 0)
        outer.pack_start(row1, False, False, 0)

        # Load-same row (unload_done only)
        self._load_same_btn = self._make_action_btn(
            "\u25b6  LOAD SAME PATH", _GREEN, self._do_load_same)
        outer.pack_start(self._load_same_btn, False, False, 0)

        outer.pack_start(Gtk.Separator(), False, False, 2)

        # Load T0..T5 row
        load_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._load_lbl = Gtk.Label(halign=Gtk.Align.START)
        self._load_lbl.set_markup('<span font_size="small" foreground="#90CAF9">LOAD PATH:</span>')
        self._load_grid = Gtk.Grid(column_spacing=6, row_spacing=0,
                                   column_homogeneous=True)
        load_box.pack_start(self._load_lbl,  False, False, 0)
        load_box.pack_start(self._load_grid, False, False, 0)
        outer.pack_start(load_box, False, False, 0)

        # Unload T0..T5 row
        unload_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._unload_lbl = Gtk.Label(halign=Gtk.Align.START)
        self._unload_lbl.set_markup('<span font_size="small" foreground="#FFCC80">UNLOAD PATH:</span>')
        self._unload_grid = Gtk.Grid(column_spacing=6, row_spacing=0,
                                     column_homogeneous=True)
        unload_box.pack_start(self._unload_lbl,  False, False, 0)
        unload_box.pack_start(self._unload_grid, False, False, 0)
        outer.pack_start(unload_box, False, False, 0)

        self.content.pack_start(outer, True, True, 0)

    def _make_action_btn(self, label, color, callback):
        btn = _sbs.make(label)
        if color:
            css = Gtk.CssProvider()
            css.load_from_data((
                ".sa-btn-custom {{ background: %s; color: white; "
                "min-height: 62px; border-radius: 6px; padding: 4px 8px; }}"
                ".sa-btn-custom label {{ color: white; }}"
                ".sa-btn-custom:hover {{ background: %s; }}"
            % (color, color)).encode())
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), css,
                Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
            btn.get_style_context().remove_class("sa-btn")
            btn.get_style_context().add_class("sa-btn-custom")
        btn.connect("clicked", callback)
        return btn

    def _build_path_grids(self, num):
        for grid, action in ((self._load_grid, 'load'), (self._unload_grid, 'unload')):
            for child in grid.get_children():
                grid.remove(child)
            for i in range(num):
                btn = _sbs.make("T%d" % i, "sa-btn-alt")
                btn.set_size_request(-1, 48)
                btn.connect("clicked", self._do_path_action, action, i)
                grid.attach(btn, i, 0, 1, 1)
        self._load_grid.show_all()
        self._unload_grid.show_all()

    # ── State update ──────────────────────────────────────────────────────────

    def _apply_state(self, cal_state, cal_path, num_paths):
        self._cal_state = cal_state
        self._cal_path  = cal_path
        self._num_paths = num_paths or 6

        self._build_path_grids(self._num_paths)

        if cal_state == 'load_purge':
            self._hdr.set_markup(
                '<span font_size="large" foreground="%s"><b>\u2713 LOAD COMPLETE \u00b7 T%d</b></span>'
                % (_GREEN, cal_path))
            self._sub.set_markup(
                '<span foreground="#BDBDBD">Filament purging at nozzle</span>')
            self._more_btn.set_visible(True)
            self._load_same_btn.set_visible(False)
        elif cal_state == 'unload_done':
            self._hdr.set_markup(
                '<span font_size="large" foreground="%s"><b>\u2713 UNLOAD COMPLETE \u00b7 T%d</b></span>'
                % (_ORANGE, cal_path))
            self._sub.set_markup(
                '<span foreground="#BDBDBD">What next?</span>')
            self._more_btn.set_visible(False)
            self._load_same_btn.set_visible(True)
        else:
            self._hdr.set_markup(
                '<span font_size="large"><b>SA Action</b></span>')
            self._sub.set_text('')
            self._more_btn.set_visible(True)
            self._load_same_btn.set_visible(False)

    # ── Button handlers ───────────────────────────────────────────────────────

    def _gcode(self, cmd):
        self._screen._ws.klippy.gcode_script(cmd)

    def _respond(self, value):
        self._gcode("SA_RESPOND VALUE=%s" % value)

    def _close(self):
        self._screen.show_panel('sa_main', 'SA Status')

    def _do_more(self, widget=None):
        self._respond("more")
        # Stay on panel — user may want more purge again

    def _do_park(self, widget=None):
        self._respond("park")
        self._close()

    def _do_exit(self, widget=None):
        self._respond("exit")
        self._close()

    def _do_load_same(self, widget=None):
        self._respond("load")
        self._close()

    def _do_path_action(self, widget, action, path):
        self._respond("%s:%d" % (action, path))
        self._close()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _query_sa(self):
        try:
            resp = self._screen.apiclient.send_request(
                "printer/objects/query?stealth_autoloader")
            if resp and 'status' in resp:
                return resp['status'].get('stealth_autoloader', {})
        except Exception as e:
            logger.warning("sa_post_load: query failed: %s", e)
        return {}

    def activate(self):
        self._active = True
        self._screen._ws.klippy.object_subscription(
            {"objects": {"stealth_autoloader": None}})
        sa = self._query_sa()
        self._apply_state(
            sa.get("cal_state", ""),
            sa.get("cal_path",  -1),
            sa.get("num_paths",  6))

    def deactivate(self):
        self._active = False

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("stealth_autoloader")
        if sa is None:
            return

        cal = sa.get("cal_state")
        if cal is not None:
            path = sa.get("cal_path", self._cal_path)
            num  = sa.get("num_paths", self._num_paths)
            GLib.idle_add(self._apply_state, cal, path, num)

            # Auto-close if backend cleared the state while we're visible
            if cal == '' and self._active:
                GLib.idle_add(self._close)
