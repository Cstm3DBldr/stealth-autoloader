import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import logging
import os
import subprocess
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sa_button_style as _sbs
import sa_ui_prefs     as _prefs
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_settings')


def _autoloader_version():
    """Best-effort `git describe` against the deployed autoloader repo so
    the About section can show what build is actually running. Falls back
    to a hard-coded label if the git invocation fails (no git binary, no
    repo, or timeout). Computed once at module import; safe to cache for
    the lifetime of the panel — the only way it changes is by pulling new
    code, which requires a Klipperscreen restart anyway."""
    repo = os.path.expanduser("~/autoloader")
    try:
        out = subprocess.check_output(
            ["git", "-C", repo, "describe",
             "--tags", "--always", "--dirty"],
            stderr=subprocess.DEVNULL, timeout=2.0)
        return out.decode().strip() or "unknown"
    except Exception:
        # Fallback: try just the short HEAD sha.
        try:
            out = subprocess.check_output(
                ["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL, timeout=2.0)
            return out.decode().strip() or "unknown"
        except Exception:
            return "unknown"


_VERSION = _autoloader_version()

_COLORS = [
    ("Blue",        "#1565C0", "#1976D2", "#0D47A1"),
    ("Teal",        "#00695C", "#00897B", "#004D40"),
    ("Green",       "#2E7D32", "#388E3C", "#1B5E20"),
    ("Purple",      "#6A1B9A", "#7B1FA2", "#4A148C"),
    ("Indigo",      "#283593", "#303F9F", "#1A237E"),
    ("Deep Orange", "#BF360C", "#D84315", "#870000"),
    ("Red",         "#B71C1C", "#C62828", "#7F0000"),
    ("Pink",        "#880E4F", "#AD1457", "#560027"),
    ("Brown",       "#4E342E", "#5D4037", "#3E2723"),
    ("Grey",        "#37474F", "#455A64", "#263238"),
    ("Amber",       "#E65100", "#F57C00", "#BF360C"),
    ("Cyan",        "#006064", "#00838F", "#004D40"),
]


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        super().__init__(screen, title or "SA Settings")
        _sbs.apply()

        self._last_sa = {}

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack.set_transition_duration(150)

        self._stack.add_named(self._build_main_page(), "main")
        self._stack.add_named(self._build_detail_page(), "detail")

        self.content.pack_start(self._stack, True, True, 0)

    # ── Main settings page ────────────────────────────────────────────────────

    def _build_main_page(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_overlay_scrolling(False)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=10)

        # NOTIFICATIONS section removed — the popup-on-complete toggle is
        # now unconditional default behaviour. See sa_subscription.py
        # for the matching cleanup of the _prefs lookup that gated it.

        # ── Accent color (Option B: 12 color circles) ─────────────────────────
        outer.pack_start(self._section("BUTTON ACCENT COLOR"), False, False, 0)

        self._accent_btns     = {}
        self._accent_name_lbl = Gtk.Label(halign=Gtk.Align.END)
        self._selected_hex    = _prefs.get("accent_color", "#1565C0")

        color_grid = Gtk.Grid(row_spacing=8, column_spacing=8,
                              row_homogeneous=True, column_homogeneous=True,
                              margin_top=4, margin_bottom=2)

        for idx, (name, hex_c, hover, active) in enumerate(_COLORS):
            btn = Gtk.Button()
            css = Gtk.CssProvider()
            cls = "sa-accent-%d" % idx
            # Circle: fully rounded (radius = half height) so it appears as
            # a perfect dot. .path-selected adds the lime ring used
            # elsewhere in the project for "currently selected".
            css.load_from_data((
                ".{c} {{ background: {bg}; border-radius: 24px; "
                "min-width: 48px; min-height: 48px; padding: 0; }}"
                ".{c}:hover {{ background: {hv}; }}"
                ".{c}:active {{ background: {ac}; }}"
            ).format(c=cls, bg=hex_c, hv=hover, ac=active).encode())
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), css,
                Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
            btn.get_style_context().add_class(cls)
            if hex_c == self._selected_hex:
                btn.get_style_context().add_class("path-selected")

            btn.set_size_request(48, 48)
            btn.connect("clicked", self._set_color, hex_c, hover, active)
            self._accent_btns[hex_c] = btn
            # 6 columns × 2 rows holds 12 swatches in roughly half the
            # vertical space the old labeled-button grid took.
            color_grid.attach(btn, idx % 6, idx // 6, 1, 1)

        outer.pack_start(color_grid, False, False, 0)

        # "Currently: <Color name>" hint below the grid so users know what
        # they just picked without label-on-button real estate.
        name_row = Gtk.Box(spacing=8)
        name_row.set_halign(Gtk.Align.END)
        name_row.set_margin_top(2)
        self._accent_name_lbl.set_markup(
            '<span font_size="small" foreground="#9E9E9E">Currently: %s</span>'
            % self._color_name_for(self._selected_hex))
        name_row.pack_start(self._accent_name_lbl, False, False, 0)
        outer.pack_start(name_row, False, False, 0)

        # ── Configured values — scrolls with the page, uses accent color ──────
        detail_btn = _sbs.make("Autoloader Configured Values \u2192", "sa-btn")
        detail_btn.connect("clicked", lambda w: self._stack.set_visible_child_name("detail"))
        detail_btn.set_margin_top(8)
        outer.pack_start(detail_btn, False, False, 0)

        cv_hint = Gtk.Label(halign=Gtk.Align.START)
        cv_hint.set_markup(
            '<span font_size="small" foreground="#9E9E9E">'
            '  Read the speeds, distances, bowden lengths, and encoder '
            'mm/pulse currently in use.</span>')
        outer.pack_start(cv_hint, False, False, 0)

        # ── Material profiles ─────────────────────────────────────────────────
        outer.pack_start(self._section("MATERIAL PROFILES"), False, False, 0)

        reset_btn = _sbs.make("Reset All Material Profiles", "sa-btn-warn")
        reset_btn.connect("clicked", self._reset_materials)
        outer.pack_start(reset_btn, False, False, 0)

        # ── About / version ───────────────────────────────────────────────────
        # Cached at module import via _autoloader_version(). Tap-to-copy
        # would be nice but isn't a normal Gtk.Label gesture; keeping it
        # display-only for now. If you ever need to roll back, the version
        # string here matches what `git describe --tags --always --dirty`
        # prints in ~/autoloader on the printer.
        about_hdr = self._section("ABOUT")
        about_hdr.set_margin_top(12)
        outer.pack_start(about_hdr, False, False, 0)

        ver_row = Gtk.Box(spacing=6)
        ver_lbl = Gtk.Label(halign=Gtk.Align.START)
        ver_lbl.set_markup(
            '<span foreground="#E0E0E0">Autoloader </span>'
            '<span foreground="#90CAF9">%s</span>'
            % GLib.markup_escape_text(_VERSION))
        ver_row.pack_start(ver_lbl, False, False, 0)
        outer.pack_start(ver_row, False, False, 0)

        repo_lbl = Gtk.Label(halign=Gtk.Align.START)
        repo_lbl.set_markup(
            '<span font_size="small" foreground="#9E9E9E">'
            '  github.com/Cstm3DBldr/autoloader</span>')
        outer.pack_start(repo_lbl, False, False, 0)

        scroll.add(outer)
        return scroll

    # ── Detail page ────────────────────────────────────────────────────────────

    def _build_detail_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_overlay_scrolling(False)

        self._detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=6, margin=10)
        scroll.add(self._detail_box)
        outer.pack_start(scroll, True, True, 0)

        back_btn = _sbs.make("\u2190  Back", "sa-btn-alt")
        back_btn.set_margin_start(10)
        back_btn.set_margin_end(10)
        back_btn.set_margin_bottom(6)
        back_btn.connect("clicked", lambda w: self._stack.set_visible_child_name("main"))
        outer.pack_start(back_btn, False, False, 0)

        return outer

    def _populate_detail(self, sa):
        box = self._detail_box
        for child in box.get_children():
            box.remove(child)

        num               = sa.get("num_paths", 0)
        bowden_lens       = sa.get("bowden_lengths", [])
        sel_pos           = sa.get("selector_positions", [])
        enc_mpp           = sa.get("encoder_mpp", [])
        feed_speed        = sa.get("feed_speed",              "\u2014")
        selector_speed    = sa.get("selector_speed",          "\u2014")
        purge_length      = sa.get("purge_length",            "\u2014")
        nozzle_dist       = sa.get("nozzle_distance",         "\u2014")
        nozzle_to_sensor  = sa.get("nozzle_to_sensor_dist",   "\u2014")
        drv_rot_dist      = sa.get("drive_rotation_distance", "\u2014")
        enc_max           = sa.get("encoder_max_speed",       0)

        def row(label, value, fg="#FFFFFF"):
            r = Gtk.Box(spacing=8)
            ll = Gtk.Label(label=label, halign=Gtk.Align.START, xalign=0.0)
            ll.set_hexpand(True)
            vl = Gtk.Label(halign=Gtk.Align.END)
            vl.set_markup('<span foreground="%s">%s</span>' % (fg, str(value)))
            r.pack_start(ll, True,  True,  0)
            r.pack_start(vl, False, False, 0)
            return r

        # ── Speeds ────────────────────────────────────────────────────────────
        box.pack_start(self._section("SPEEDS"), False, False, 0)
        box.pack_start(row("Feed Speed",        "%s mm/s" % feed_speed),     False, False, 0)
        box.pack_start(row("Selector Speed",    "%s mm/s" % selector_speed), False, False, 0)
        if enc_max and enc_max > 0:
            blast = enc_max * 0.75
            box.pack_start(row("Encoder Max Speed",
                               "%.1f mm/s" % enc_max), False, False, 0)
            box.pack_start(row("Blast Speed (75%)",
                               "%.1f mm/s" % blast),   False, False, 0)
        else:
            box.pack_start(row("Encoder Max Speed", "\u2014 (run CAL ENCODER SPEED)"),
                           False, False, 0)

        # ── Distances ─────────────────────────────────────────────────────────
        box.pack_start(Gtk.Separator(), False, False, 4)
        box.pack_start(self._section("DISTANCES"), False, False, 0)
        box.pack_start(row("Purge Length",
                           "%s mm" % purge_length),       False, False, 0)
        box.pack_start(row("Toolhead Sensor \u2192 Nozzle",
                           "%s mm" % nozzle_to_sensor),   False, False, 0)
        box.pack_start(row("Extruder Gears \u2192 Nozzle",
                           "%s mm" % nozzle_dist),        False, False, 0)
        box.pack_start(row("Drive Rotation Dist",
                           str(drv_rot_dist)),            False, False, 0)

        # ── Per-path bowden ───────────────────────────────────────────────────
        box.pack_start(Gtk.Separator(), False, False, 4)
        box.pack_start(self._section("PER-PATH BOWDEN LENGTH"), False, False, 0)
        for i in range(num):
            val = ("%.1f mm" % bowden_lens[i]) if i < len(bowden_lens) else "\u2014"
            box.pack_start(row("T%d" % i, val, "#90CAF9"), False, False, 0)

        # ── Selector positions ────────────────────────────────────────────────
        if sel_pos:
            box.pack_start(Gtk.Separator(), False, False, 4)
            box.pack_start(self._section("SELECTOR POSITIONS"), False, False, 0)
            for i in range(num):
                val = ("%.2f mm" % sel_pos[i]) if i < len(sel_pos) else "\u2014"
                box.pack_start(row("T%d" % i, val, "#FFCC80"), False, False, 0)

        # ── Encoder mm/pulse ──────────────────────────────────────────────────
        if enc_mpp:
            box.pack_start(Gtk.Separator(), False, False, 4)
            box.pack_start(self._section("ENCODER mm/pulse"), False, False, 0)
            for i in range(num):
                val = ("%.4f" % enc_mpp[i]) if i < len(enc_mpp) else "\u2014"
                box.pack_start(row("T%d" % i, val, "#CE93D8"), False, False, 0)

        box.show_all()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _section(self, title):
        lbl = Gtk.Label(halign=Gtk.Align.START)
        lbl.set_markup('<b><span font_size="large">%s</span></b>' % title)
        return lbl

    def _color_name_for(self, hex_c):
        """Look up the human-readable name of an accent hex from _COLORS."""
        for name, h, _hv, _ac in _COLORS:
            if h.lower() == hex_c.lower():
                return name
        return hex_c

    def _set_color(self, widget, hex_c, hover, active):
        for h, btn in self._accent_btns.items():
            ctx = btn.get_style_context()
            if h == hex_c:
                ctx.add_class("path-selected")
            else:
                ctx.remove_class("path-selected")
        self._selected_hex = hex_c
        _prefs.save({"accent_color": hex_c})
        _sbs.reapply(hex_c, hover, active)
        # Update the "Currently: X" hint without rebuilding the page.
        if getattr(self, "_accent_name_lbl", None) is not None:
            self._accent_name_lbl.set_markup(
                '<span font_size="small" foreground="#9E9E9E">Currently: %s'
                '</span>' % self._color_name_for(hex_c))
        self._screen.show_popup_message(
            "Button color updated \u2014 reopen panels to see changes", level=1)

    def _reset_materials(self, widget):
        sa = self._last_sa
        n  = sa.get("num_paths", 6)
        for i in range(n):
            self._screen._ws.klippy.gcode_script(
                'SA_SET_MATERIAL TOOL=%d MATERIAL="" BRAND="" LINE="" '
                'COLOR_NAME="" COLOR_HEX="" '
                'LOAD_TEMP=200 UNLOAD_TEMP=185 PURGE_SPEED=5 PURGE_LENGTH=30' % i)
        self._screen.show_popup_message("All material profiles cleared", level=1)

    def _query_sa(self):
        try:
            resp = self._screen.apiclient.send_request(
                "printer/objects/query?autoloader")
            if resp and 'status' in resp:
                return resp['status'].get('autoloader', {})
        except Exception as e:
            logger.warning("sa_settings: query failed: %s", e)
        return {}

    def activate(self):
        self._stack.set_visible_child_name("main")
        sa = self._query_sa()
        self._last_sa = sa
        self._populate_detail(sa)

    def process_update(self, action, data):
        pass
