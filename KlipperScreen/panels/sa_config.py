import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sa_button_style as _sbs
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_config')

# (section_label, param_key, display_label, unit, decimal_places)
# param_key must match [stealth_autoloader] config key and get_status() key
_PARAMS = [
    ("SPEEDS",        "feed_speed",              "Feed Speed",          "mm/s", 1),
    ("SPEEDS",        "selector_speed",           "Selector Speed",      "mm/s", 1),
    ("DISTANCES",     "tube_length",              "Tube Length",         "mm",   1),
    ("DISTANCES",     "nozzle_to_sensor_dist",    "Sensor \u2192 Nozzle","mm",   1),
    ("DISTANCES",     "nozzle_distance",          "Nozzle Distance",     "mm",   1),
    ("DISTANCES",     "purge_length",             "Purge Length",        "mm",   1),
    ("DISTANCES",     "engage_max_distance",      "Engage Max Dist",     "mm",   1),
    ("TEMPERATURES",  "load_temperature",         "Load Temperature",    "\u00b0C", 0),
    ("TEMPERATURES",  "tip_form_temp",            "Tip Form Temp",       "\u00b0C", 0),
    ("SERVO",         "servo_engaged_angle",      "Engaged Angle",       "\u00b0",  0),
    ("SERVO",         "servo_disengaged_angle",   "Disengaged Angle",    "\u00b0",  0),
]


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        super().__init__(screen, title or "SA Config")
        _sbs.apply()

        self._pending    = {}   # key → new value string (staged, not yet saved)
        self._current    = {}   # key → current float value from firmware
        self._val_labels = {}   # key → Gtk.Label showing value on list page
        self._edit_key   = None
        self._edit_label = ""
        self._edit_unit  = ""
        self._edit_dps   = 1
        self._input_str  = "0"

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack.set_transition_duration(150)

        self._list_outer = self._build_list_page()
        self._edit_outer = self._build_edit_page()
        self._stack.add_named(self._list_outer, "list")
        self._stack.add_named(self._edit_outer, "edit")

        self.content.pack_start(self._stack, True, True, 0)

    # ── List page ─────────────────────────────────────────────────────────────

    def _build_list_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
        scroll.set_overlay_scrolling(False)

        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=4, margin=8)
        scroll.add(self._list_box)
        outer.pack_start(scroll, True, True, 0)

        self._save_btn = _sbs.make("SAVE & RESTART", "sa-btn-warn")
        self._save_btn.set_sensitive(False)
        self._save_btn.set_margin_start(8)
        self._save_btn.set_margin_end(8)
        self._save_btn.set_margin_top(4)
        self._save_btn.set_margin_bottom(6)
        self._save_btn.connect("clicked", self._do_save)
        outer.pack_start(self._save_btn, False, False, 0)

        return outer

    def _rebuild_list(self):
        box = self._list_box
        for child in box.get_children():
            box.remove(child)
        self._val_labels.clear()

        last_section = None
        for (section, key, label, unit, dps) in _PARAMS:
            if section != last_section:
                if last_section is not None:
                    box.pack_start(Gtk.Separator(), False, False, 2)
                sec_lbl = Gtk.Label(halign=Gtk.Align.START)
                sec_lbl.set_markup(
                    '<b><span font_size="large">%s</span></b>' % section)
                box.pack_start(sec_lbl, False, False, 2)
                last_section = section

            row = Gtk.Box(spacing=8, margin_top=2, margin_bottom=2)

            name_lbl = Gtk.Label(label=label, halign=Gtk.Align.START,
                                 xalign=0.0)
            name_lbl.set_hexpand(True)

            val_lbl = Gtk.Label(halign=Gtk.Align.END)
            self._refresh_val_label(val_lbl, key, unit, dps)
            self._val_labels[key] = val_lbl

            edit_btn = _sbs.make("\u270e", "sa-btn-nav")
            edit_btn.set_size_request(52, 44)
            edit_btn.connect("clicked", self._open_edit, key, label, unit, dps)

            row.pack_start(name_lbl, True,  True,  0)
            row.pack_start(val_lbl,  False, False, 8)
            row.pack_start(edit_btn, False, False, 0)
            box.pack_start(row, False, False, 0)

        box.show_all()

    def _refresh_val_label(self, lbl, key, unit, dps):
        if key in self._pending:
            val_str = self._pending[key]
            lbl.set_markup(
                '<span foreground="#FFA726"><b>%s %s \u25cf</b></span>'
                % (val_str, unit))
        else:
            cur = self._current.get(key)
            if cur is None:
                lbl.set_markup('<span foreground="#9E9E9E">\u2014</span>')
            elif dps == 0:
                lbl.set_markup('%d %s' % (int(round(cur)), unit))
            else:
                lbl.set_markup('%.*f %s' % (dps, cur, unit))

    # ── Edit (numpad) page ────────────────────────────────────────────────────

    def _build_edit_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._edit_header = Gtk.Label()
        self._edit_header.set_margin_top(10)
        self._edit_header.set_margin_bottom(4)
        outer.pack_start(self._edit_header, False, False, 0)

        # Value display
        self._display_lbl = Gtk.Label()
        self._display_lbl.set_margin_start(16)
        self._display_lbl.set_margin_end(16)
        self._display_lbl.set_margin_bottom(6)
        outer.pack_start(self._display_lbl, False, False, 0)

        # Numpad grid
        pad = Gtk.Grid(row_homogeneous=True, column_homogeneous=True,
                       row_spacing=5, column_spacing=5,
                       margin_start=16, margin_end=16)

        numpad_def = [
            ("7", 0, 0), ("8", 1, 0), ("9", 2, 0), ("\u232b", 3, 0),
            ("4", 0, 1), ("5", 1, 1), ("6", 2, 1), ("C",       3, 1),
            ("1", 0, 2), ("2", 1, 2), ("3", 2, 2), (".",       3, 2),
        ]
        for (key, col, row) in numpad_def:
            style = "sa-btn-alt" if key in ("\u232b", "C", ".") else "sa-btn"
            btn = _sbs.make(key, style)
            btn.connect("clicked", self._numpad_key, key)
            pad.attach(btn, col, row, 1, 1)

        zero = _sbs.make("0", "sa-btn")
        zero.connect("clicked", self._numpad_key, "0")
        pad.attach(zero, 0, 3, 2, 1)

        outer.pack_start(pad, True, True, 0)

        # Cancel / Confirm
        btn_row = Gtk.Box(spacing=6,
                          margin_start=16, margin_end=16,
                          margin_top=6, margin_bottom=8)
        cancel_btn = _sbs.make("\u2715  Cancel", "sa-btn-alt")
        cancel_btn.connect("clicked", lambda w: self._stack.set_visible_child_name("list"))
        confirm_btn = _sbs.make("\u2713  Confirm", "sa-btn")
        confirm_btn.connect("clicked", self._confirm_edit)
        btn_row.pack_start(cancel_btn,  True, True, 0)
        btn_row.pack_start(confirm_btn, True, True, 0)
        outer.pack_start(btn_row, False, False, 0)

        return outer

    def _open_edit(self, widget, key, label, unit, dps):
        self._edit_key   = key
        self._edit_label = label
        self._edit_unit  = unit
        self._edit_dps   = dps

        # Pre-fill with pending value if present, else current firmware value
        if key in self._pending:
            self._input_str = self._pending[key]
        else:
            cur = self._current.get(key)
            if cur is None:
                self._input_str = "0"
            elif dps == 0:
                self._input_str = str(int(round(cur)))
            else:
                raw = ('%.*f' % (dps, cur))
                # strip trailing zeros but keep at least one digit
                self._input_str = raw.rstrip('0').rstrip('.') or '0'

        self._edit_header.set_markup(
            '<b><span font_size="large">%s</span></b>' % label)
        self._refresh_display()
        self._stack.set_visible_child_name("edit")

    def _numpad_key(self, widget, key):
        s = self._input_str
        if key == "\u232b":          # backspace
            self._input_str = s[:-1] if len(s) > 1 else "0"
        elif key == "C":             # clear
            self._input_str = "0"
        elif key == ".":
            if "." not in s:
                self._input_str = s + "."
        elif key == "0":
            if s != "0":
                self._input_str = s + "0"
        else:
            if s == "0":
                self._input_str = key
            else:
                self._input_str = s + key
        self._refresh_display()

    def _refresh_display(self):
        s = self._input_str or "0"
        unit = self._edit_unit
        self._display_lbl.set_markup(
            '<span font_size="xx-large" font_weight="bold">%s %s</span>'
            % (s, unit))

    def _confirm_edit(self, widget):
        key = self._edit_key
        if key is None:
            self._stack.set_visible_child_name("list")
            return

        val = self._input_str.strip().rstrip(".")
        if not val:
            val = "0"

        self._pending[key] = val

        # Update the value label on the list page
        lbl = self._val_labels.get(key)
        if lbl:
            entry = next((e for e in _PARAMS if e[1] == key), None)
            if entry:
                _, _, _, unit, dps = entry
                self._refresh_val_label(lbl, key, unit, dps)

        self._save_btn.set_sensitive(True)
        self._stack.set_visible_child_name("list")

    # ── Save & Restart ────────────────────────────────────────────────────────

    def _do_save(self, widget):
        if not self._pending:
            return
        for key, val in self._pending.items():
            self._screen._ws.klippy.gcode_script(
                "SA_SET_CONFIG PARAM=%s VALUE=%s" % (key, val))
        self._screen._ws.klippy.gcode_script("SAVE_CONFIG")
        self._screen.show_popup_message(
            "Saving config \u2014 Klipper will restart to apply changes",
            level=1)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def activate(self):
        self._pending.clear()
        self._save_btn.set_sensitive(False)
        self._stack.set_visible_child_name("list")

        sa = self._query_sa()
        self._load_current(sa)
        self._rebuild_list()

    def _query_sa(self):
        try:
            resp = self._screen.apiclient.send_request(
                "printer/objects/query?stealth_autoloader")
            if resp and 'status' in resp:
                return resp['status'].get('stealth_autoloader', {})
        except Exception as e:
            logger.warning("sa_config: query failed: %s", e)
        return {}

    def _load_current(self, sa):
        for (_, key, _, _, _) in _PARAMS:
            val = sa.get(key)
            if val is not None:
                try:
                    self._current[key] = float(val)
                except (TypeError, ValueError):
                    pass

    def process_update(self, action, data):
        pass
