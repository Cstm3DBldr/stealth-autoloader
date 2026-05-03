import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import logging
import sys, os

_panels_dir = os.path.dirname(os.path.abspath(__file__))
_ks_root    = os.path.dirname(_panels_dir)
for _p in (_ks_root, _panels_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sa_button_style as _sbs
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_cal_prompt')

_GREEN  = '#388E3C'
_RED    = '#B71C1C'
_BLUE   = '#1565C0'
_GREY   = '#37474F'


def _input_type(state):
    """Map cal_state to the kind of input widget needed."""
    if state in ('sel_confirm', 'drv_save') or \
       state.startswith('enc_save_') or state.startswith('enc_mark_'):
        return 'yesno'
    if state == 'drv_mark':
        return 'ready'
    if state == 'drv_path':
        return 'numpad_int'
    if state in ('drv_meas',) or \
       state.startswith('enc_meas_') or state.startswith('bow_est_'):
        return 'numpad_float'
    return 'yesno'


class Panel(ScreenPanel):
    """Calibration prompt panel — shows backend prompt + appropriate input."""

    def __init__(self, screen, title):
        super().__init__(screen, title or "SA Calibration")
        _sbs.apply()

        self._active    = False
        self._cal_state = ''
        self._entry_val = ''

        self._build_ui()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=8)

        # Prompt text
        self._prompt_lbl = Gtk.Label()
        self._prompt_lbl.set_line_wrap(True)
        self._prompt_lbl.set_halign(Gtk.Align.CENTER)
        self._prompt_lbl.set_markup(
            '<span font_size="large">Waiting for calibration…</span>')
        outer.pack_start(self._prompt_lbl, False, False, 0)

        outer.pack_start(Gtk.Separator(), False, False, 2)

        # Input area — swapped based on input type
        self._input_stack = Gtk.Stack()
        self._input_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._input_stack.set_transition_duration(100)

        self._input_stack.add_named(self._build_yesno(),       "yesno")
        self._input_stack.add_named(self._build_ready(),       "ready")
        self._input_stack.add_named(self._build_numpad(False), "numpad_int")
        self._input_stack.add_named(self._build_numpad(True),  "numpad_float")

        outer.pack_start(self._input_stack, True, True, 0)

        # ABORT — always at bottom
        abort_btn = _sbs.make("\u26d4  ABORT", "sa-btn-warn")
        abort_btn.connect("clicked", self._do_abort)
        outer.pack_start(abort_btn, False, False, 0)

        self.content.pack_start(outer, True, True, 0)

    def _build_yesno(self):
        box = Gtk.Box(spacing=12, margin_top=12)
        yes = _sbs.make("\u2714  YES", "sa-btn")
        no  = _sbs.make("\u2716  NO",  "sa-btn-alt")
        yes.set_size_request(-1, 72)
        no.set_size_request(-1, 72)
        yes.connect("clicked", self._respond_fixed, "yes")
        no.connect("clicked",  self._respond_fixed, "no")
        box.pack_start(yes, True, True, 0)
        box.pack_start(no,  True, True, 0)
        return box

    def _build_ready(self):
        box = Gtk.Box(spacing=12, margin_top=12)
        rdy  = _sbs.make("\u25b6  READY",    "sa-btn")
        retry = _sbs.make("\u21ba  NOT YET", "sa-btn-alt")
        rdy.set_size_request(-1, 72)
        retry.set_size_request(-1, 72)
        rdy.connect("clicked",   self._respond_fixed, "yes")
        retry.connect("clicked", self._respond_fixed, "no")
        box.pack_start(rdy,   True, True, 0)
        box.pack_start(retry, True, True, 0)
        return box

    def _build_numpad(self, with_decimal):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        # Entry display
        disp = Gtk.Entry()
        disp.set_editable(False)
        disp.set_alignment(1.0)
        disp.set_size_request(-1, 48)
        disp.get_style_context().add_class("sa-btn-alt")
        key = "float" if with_decimal else "int"
        setattr(self, "_disp_%s" % key, disp)
        outer.pack_start(disp, False, False, 0)

        grid = Gtk.Grid(row_spacing=4, column_spacing=4, row_homogeneous=True,
                        column_homogeneous=True)

        digits = [
            ('7', 0, 0), ('8', 0, 1), ('9', 0, 2),
            ('4', 1, 0), ('5', 1, 1), ('6', 1, 2),
            ('1', 2, 0), ('2', 2, 1), ('3', 2, 2),
        ]
        for lbl, row, col in digits:
            btn = _sbs.make(lbl, "sa-btn-alt")
            btn.set_size_request(-1, 52)
            btn.connect("clicked", self._numpad_digit, key, lbl)
            grid.attach(btn, col, row, 1, 1)

        # Bottom row: [. or blank] [0] [⌫]
        if with_decimal:
            dot_btn = _sbs.make(".", "sa-btn-alt")
            dot_btn.set_size_request(-1, 52)
            dot_btn.connect("clicked", self._numpad_digit, key, ".")
            grid.attach(dot_btn, 0, 3, 1, 1)
        else:
            placeholder = Gtk.Label()
            grid.attach(placeholder, 0, 3, 1, 1)

        zero_btn = _sbs.make("0", "sa-btn-alt")
        zero_btn.set_size_request(-1, 52)
        zero_btn.connect("clicked", self._numpad_digit, key, "0")
        grid.attach(zero_btn, 1, 3, 1, 1)

        back_btn = _sbs.make("\u232b", "sa-btn-alt")
        back_btn.set_size_request(-1, 52)
        back_btn.connect("clicked", self._numpad_backspace, key)
        grid.attach(back_btn, 2, 3, 1, 1)

        outer.pack_start(grid, True, True, 0)

        send_btn = _sbs.make("\u2713  SEND", "sa-btn")
        send_btn.set_size_request(-1, 56)
        send_btn.connect("clicked", self._numpad_send, key)
        outer.pack_start(send_btn, False, False, 0)

        return outer

    # ── Numpad logic ───────────────────────────────────────────────────────────

    def _get_disp(self, key):
        return getattr(self, "_disp_%s" % key, None)

    def _numpad_digit(self, widget, key, digit):
        disp = self._get_disp(key)
        if disp is None:
            return
        cur = disp.get_text()
        if digit == '.' and '.' in cur:
            return
        disp.set_text(cur + digit)

    def _numpad_backspace(self, widget, key):
        disp = self._get_disp(key)
        if disp is None:
            return
        cur = disp.get_text()
        disp.set_text(cur[:-1])

    def _numpad_send(self, widget, key):
        disp = self._get_disp(key)
        if disp is None:
            return
        val = disp.get_text().strip()
        if not val:
            return
        disp.set_text('')
        self._respond(val)

    # ── Button handlers ────────────────────────────────────────────────────────

    def _gcode(self, cmd):
        self._screen._ws.klippy.gcode_script(cmd)

    def _respond(self, value):
        self._gcode("SA_RESPOND VALUE=%s" % value)

    def _respond_fixed(self, widget, value):
        self._respond(value)

    def _do_abort(self, widget=None):
        self._respond("abort")
        self._close()

    def _close(self):
        self._screen.show_panel('sa_main', 'SA Status')

    # ── State update ───────────────────────────────────────────────────────────

    def _apply_state(self, cal_state, cal_prompt):
        self._cal_state = cal_state

        if cal_state:
            display_text = cal_prompt if cal_prompt else cal_state
            self._prompt_lbl.set_markup(
                '<span font_size="medium">%s</span>' % display_text)
            itype = _input_type(cal_state)
            self._input_stack.set_visible_child_name(itype)
            # Clear numpad display on state change
            for key in ('int', 'float'):
                disp = self._get_disp(key)
                if disp:
                    disp.set_text('')

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def _query_sa(self):
        try:
            resp = self._screen.apiclient.send_request(
                "printer/objects/query?autoloader")
            if resp and 'status' in resp:
                return resp['status'].get('autoloader', {})
        except Exception as e:
            logger.warning("sa_cal_prompt: query failed: %s", e)
        return {}

    def activate(self):
        self._active = True
        self._screen._ws.klippy.object_subscription(
            {"objects": {"autoloader": None}})
        sa = self._query_sa()
        self._apply_state(
            sa.get("cal_state",  ""),
            sa.get("cal_prompt", ""))

    def deactivate(self):
        self._active = False

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("autoloader")
        if sa is None:
            return

        cal   = sa.get("cal_state")
        if cal is None:
            return

        prompt = sa.get("cal_prompt", "")
        GLib.idle_add(self._apply_state, cal, prompt)

        # State cleared → return to main; post-load/unload → let sa_main handle it
        if (cal == '' or cal in ('load_purge', 'unload_done')) and self._active:
            GLib.idle_add(self._close)
