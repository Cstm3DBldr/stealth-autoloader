import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sa_button_style as _sbs
import sa_ui_prefs     as _prefs
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_settings')

# Accent color palette — label, hex, auto-derived hover/active
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

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_overlay_scrolling(False)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=10)

        # ── Accent color ──────────────────────────────────────────────────────
        outer.pack_start(self._section("BUTTON ACCENT COLOR"), False, False, 0)

        color_grid = Gtk.Grid(row_spacing=6, column_spacing=6,
                              row_homogeneous=True, column_homogeneous=True)
        current = _prefs.get("accent_color", "#1565C0")

        for idx, (name, hex_c, hover, active) in enumerate(_COLORS):
            btn = Gtk.Button()
            btn.get_style_context().add_class("sa-btn")
            # Override just this button's bg inline so it shows its own color
            css = Gtk.CssProvider()
            css.load_from_data((
                ".sa-color-{i} {{ background: {c}; }}"
                ".sa-color-{i}:hover {{ background: {h}; }}"
                ".sa-color-{i}:active {{ background: {a}; }}"
            ).format(i=idx, c=hex_c, h=hover, a=active).encode())
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), css,
                Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
            btn.get_style_context().add_class(f"sa-color-{idx}")

            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            inner.set_halign(Gtk.Align.CENTER)
            inner.set_valign(Gtk.Align.CENTER)
            lbl = Gtk.Label(label=name)
            lbl.get_style_context().add_class("sa-btn")  # white text via cascade
            inner.pack_start(lbl, False, False, 0)
            if hex_c == current:
                check = Gtk.Label(label="✓")
                inner.pack_start(check, False, False, 0)
                self._active_color_btn = btn
            btn.add(inner)
            btn.connect("clicked", self._set_color, hex_c, hover, active)
            color_grid.attach(btn, idx % 4, idx // 4, 1, 1)

        outer.pack_start(color_grid, False, False, 0)

        # ── Material profiles ─────────────────────────────────────────────────
        outer.pack_start(self._section("MATERIAL PROFILES"), False, False, 0)

        reset_btn = _sbs.make("Reset All Material Profiles", "sa-btn-warn")
        reset_btn.connect("clicked", self._reset_materials)
        outer.pack_start(reset_btn, False, False, 0)

        # ── Feed parameters ───────────────────────────────────────────────────
        outer.pack_start(self._section("FEED PARAMETERS"), False, False, 0)

        params_grid = Gtk.Grid(row_spacing=6, column_spacing=12, margin_start=4)
        self._param_labels = {}
        for row, (key, label) in enumerate([
            ("feed_speed",    "Feed Speed (mm/s)"),
            ("purge_length",  "Default Purge Length (mm)"),
            ("nozzle_dist",   "Nozzle Distance (mm)"),
            ("bowden_length", "Bowden Length (mm)"),
        ]):
            name_lbl = Gtk.Label(label=label, halign=Gtk.Align.START)
            val_lbl  = Gtk.Label(label="—",   halign=Gtk.Align.END)
            params_grid.attach(name_lbl, 0, row, 1, 1)
            params_grid.attach(val_lbl,  1, row, 1, 1)
            self._param_labels[key] = val_lbl

        outer.pack_start(params_grid, False, False, 0)

        scroll.add(outer)
        self.content.pack_start(scroll, True, True, 0)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _section(self, title):
        lbl = Gtk.Label(halign=Gtk.Align.START)
        lbl.set_markup(f'<b><span font_size="large">{title}</span></b>')
        return lbl

    def _set_color(self, widget, hex_c, hover, active):
        _sbs.reapply(hex_c, hover, active)
        self._screen.show_popup_message(
            f"Button color updated — reopen panels to see changes", level=1)

    def _reset_materials(self, widget):
        sa = self._query_sa()
        n  = sa.get("num_paths", 6)
        for i in range(n):
            self._screen._ws.klippy.gcode_script(
                f'SA_SET_MATERIAL TOOL={i} MATERIAL="" BRAND="" LINE="" '
                f'COLOR_NAME="" COLOR_HEX="" '
                f'LOAD_TEMP=200 UNLOAD_TEMP=185 PURGE_SPEED=5 PURGE_LENGTH=30')
        self._screen.show_popup_message("All material profiles cleared", level=1)

    def _query_sa(self):
        try:
            resp = self._screen.apiclient.send_request(
                "printer/objects/query?stealth_autoloader")
            if resp and 'status' in resp:
                return resp['status'].get('stealth_autoloader', {})
        except Exception as e:
            logger.warning("sa_settings: query failed: %s", e)
        return {}

    def activate(self):
        sa = self._query_sa()
        self._update_params(sa)

    def _update_params(self, sa):
        mapping = {
            "feed_speed":    sa.get("feed_speed",    "—"),
            "purge_length":  sa.get("purge_length",  "—"),
            "nozzle_dist":   sa.get("nozzle_distance","—"),
            "bowden_length": sa.get("bowden_length",  "—"),
        }
        for key, val in mapping.items():
            lbl = self._param_labels.get(key)
            if lbl:
                lbl.set_text(str(val))

    def process_update(self, action, data):
        pass
