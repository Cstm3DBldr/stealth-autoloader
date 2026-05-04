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
import sa_subscription as _sasub
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
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=6)

        # Status header
        self._hdr = Gtk.Label()
        self._hdr.set_halign(Gtk.Align.CENTER)
        self._hdr.set_markup(
            '<span font_size="large" foreground="%s"><b>SA Action</b></span>' % _GREEN)
        outer.pack_start(self._hdr, False, False, 0)

        self._sub = Gtk.Label()
        self._sub.set_halign(Gtk.Align.CENTER)
        outer.pack_start(self._sub, False, False, 0)

        # Divider below the sub-header \u2014 visual break before the action row.
        # Kept; the SECOND divider (between action row and LOAD PATH) was
        # removed because it pushed the UNLOAD T-row off the bottom on a
        # 480px screen. Section grouping below relies on the colored
        # "LOAD PATH:" / "UNLOAD PATH:" headers alone.
        outer.pack_start(Gtk.Separator(), False, False, 4)

        # Primary action buttons \u2014 single row in both states. PURGE shows
        # in load_purge, LOAD SAME shows in unload_done; only one of those
        # two is ever visible. PARK and EXIT are always visible. With one
        # of the two conditional buttons hidden via set_visible(False),
        # GTK skips its allocation and the remaining 3 buttons share the
        # row width evenly.
        row1 = Gtk.Box(spacing=8)
        self._more_btn = self._make_action_btn(
            "\u21ba  PURGE 60mm", _GREEN, self._do_more)
        self._load_same_btn = self._make_action_btn(
            "\u25b6  LOAD SAME", _GREEN, self._do_load_same)
        self._park_btn = self._make_action_btn(
            "Ⓟ  PARK",    None,   self._do_park)
        self._exit_btn = self._make_action_btn(
            "\u2715  EXIT",        _RED,   self._do_exit)
        # set_no_show_all so the screen.attach_panel show_all() pass
        # doesn't override our state-dependent set_visible() calls below.
        self._more_btn.set_no_show_all(True)
        self._load_same_btn.set_no_show_all(True)
        for b in (self._more_btn, self._load_same_btn, self._park_btn, self._exit_btn):
            b.set_size_request(-1, 56)
            row1.pack_start(b, True, True, 0)
        outer.pack_start(row1, False, False, 0)

        # Load T0..T5 row
        load_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._load_lbl = Gtk.Label(halign=Gtk.Align.START)
        self._load_lbl.set_markup('<span font_size="small" foreground="#90CAF9">LOAD PATH:</span>')
        self._load_grid = Gtk.Grid(column_spacing=6, row_spacing=0,
                                   column_homogeneous=True)
        load_box.pack_start(self._load_lbl,  False, False, 0)
        load_box.pack_start(self._load_grid, False, False, 0)
        outer.pack_start(load_box, False, False, 0)

        # Unload T0..T5 row
        unload_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._unload_lbl = Gtk.Label(halign=Gtk.Align.START)
        self._unload_lbl.set_markup('<span font_size="small" foreground="#FFCC80">UNLOAD PATH:</span>')
        self._unload_grid = Gtk.Grid(column_spacing=6, row_spacing=0,
                                     column_homogeneous=True)
        unload_box.pack_start(self._unload_lbl,  False, False, 0)
        unload_box.pack_start(self._unload_grid, False, False, 0)
        outer.pack_start(unload_box, False, False, 0)

        self.content.pack_start(outer, True, True, 0)

    def _make_action_btn(self, label, color, callback):
        if color == _GREEN:
            btn = _sbs.make(label, "sa-btn")
        elif color == _RED:
            btn = _sbs.make(label, "sa-btn-warn")
        else:
            btn = _sbs.make(label, "sa-btn-alt")
        btn.connect("clicked", callback)
        return btn

    def _build_path_grids(self, num):
        for grid, action in ((self._load_grid, 'load'), (self._unload_grid, 'unload')):
            for child in grid.get_children():
                grid.remove(child)
            for i in range(num):
                btn = _sbs.make("T%d" % i, "sa-btn-alt")
                # Compact path-row buttons so both LOAD and UNLOAD rows fit
                # below the action buttons on a 480px screen.
                btn.set_size_request(-1, 38)
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
        # Tell the global popup watcher this dismiss is intentional, so the
        # next status update with the same cal_state (Klipper hasn't
        # processed our SA_RESPOND yet — async) doesn't re-open the popup.
        # The flag clears automatically once cal_state actually transitions.
        try:
            _sasub.mark_user_dismissed(self._cal_state)
        except Exception:
            pass

        # Walk back through the autoloader popup chain instead of pushing
        # sa_main onto the stack. show_panel() would leave sa_post_load and
        # sa_load_unload in the back stack, so the user's next "back" tap
        # would re-open the popup. Pop everything autoloader-popup-related
        # so back lands wherever the user was BEFORE the popup chain.
        s = self._screen
        try:
            s._menu_go_back()  # pop self (sa_post_load)
            popup_panels = ('sa_load_unload', 'sa_post_load', 'sa_cal_prompt')
            while s._cur_panels and s._cur_panels[-1] in popup_panels:
                s._menu_go_back()
        except Exception as e:
            logger.warning("sa_post_load: _close fallback to sa_main: %s", e)
            s.show_panel('sa_main', 'SA Status')

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
                "printer/objects/query?autoloader")
            if resp and 'status' in resp:
                return resp['status'].get('autoloader', {})
        except Exception as e:
            logger.warning("sa_post_load: query failed: %s", e)
        return {}

    def activate(self):
        self._active = True
        self._screen._ws.klippy.object_subscription(
            {"objects": _sasub.build_subscription(self._screen)})
        _sasub.install_global_popup_watcher(self._screen)
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
        sa = data.get("autoloader")
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
