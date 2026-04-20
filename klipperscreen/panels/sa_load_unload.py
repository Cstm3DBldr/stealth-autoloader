import os
import sys
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import logging
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_load_unload')

_BRANDS_DIR = os.path.expanduser("~/stealth-autoloader/filaments/brands")

_ks_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ks_root not in sys.path:
    sys.path.insert(0, _ks_root)

try:
    import sa_filament_db as _db
except ImportError:
    _db = None
    logger.error("sa_load_unload: could not import sa_filament_db")

COLOR_SWATCH = '⬤'
EMPTY_SWATCH = '⊘'   # crossed circle → empty/no filament
UNKNOWN_SWATCH = '◌'  # dotted outline → unknown
PARTIAL_SWATCH = '◑'  # half-filled → partial

_LIME = '#8BC34A'   # selection highlight colour

# Inject CSS once at module level
_css_loaded = False

def _ensure_css():
    global _css_loaded
    if _css_loaded:
        return
    css = Gtk.CssProvider()
    css.load_from_data(b"""
.path-selected {
    border: 3px solid #8BC34A;
}
""")
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), css,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_loaded = True


def _rgba_from_hex(hex_c):
    rgba = Gdk.RGBA()
    if hex_c and Gdk.RGBA.parse(rgba, hex_c):
        return rgba
    if hex_c and Gdk.RGBA.parse(rgba, '#' + hex_c):
        return rgba
    return None


class Panel(ScreenPanel):
    """Load/Unload wizard: select path → pick op → wizard → START."""

    def __init__(self, screen, title):
        super().__init__(screen, title or "Load / Unload")
        _ensure_css()

        self._op          = 'load'
        self._wz          = {}
        self._path_states = []
        self._path_hexes  = []   # current colour per path (for swatch)
        self._path_mats   = []   # current material per path (for label)
        self._sel_path    = None
        self._sel_btn     = None  # currently highlighted path button

        # ── Notebook ────────────────────────────────────────────────────────
        self._nb = Gtk.Notebook()
        self._nb.set_show_tabs(False)

        self._pages = {}
        self._pages['path']     = self._make_path_page()
        self._pages['brand']    = self._make_list_page()
        self._pages['material'] = self._make_scroll_page()
        self._pages['line']     = self._make_list_page()
        self._pages['color']    = self._make_scroll_page(color_mode=True)

        for name in ('path', 'brand', 'material', 'line', 'color'):
            self._nb.append_page(self._pages[name]['outer'], None)

        self.content.pack_start(self._nb, True, True, 0)

        # ── Nav bar ─────────────────────────────────────────────────────────
        nav = Gtk.Box(spacing=6, margin=6)

        self._back_btn = self._gtk.Button(label="← Back",    style="color2", scale=self.bts)
        self._back_btn.connect("clicked", self._go_back)

        self._save_btn = self._gtk.Button(label="SAVE ONLY",  style="color3", scale=self.bts)
        self._save_btn.connect("clicked", self._save_only)

        self._conf_btn = self._gtk.Button(label="Next →",    style="color1", scale=self.bts)
        self._conf_btn.connect("clicked", self._confirm)

        nav.pack_start(self._back_btn, True, True, 0)
        nav.pack_start(self._save_btn, True, True, 0)
        nav.pack_end(self._conf_btn,   True, True, 0)
        self.content.pack_end(nav, False, False, 0)

        self._show_page('path')

    # ── Page factories ────────────────────────────────────────────────────

    def _make_path_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=8)

        hdr = Gtk.Label(label="Select tool, then choose Load or Unload")
        hdr.set_halign(Gtk.Align.START)
        outer.pack_start(hdr, False, False, 0)

        self._path_grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True,
                                   row_spacing=5, column_spacing=5)
        outer.pack_start(self._path_grid, True, True, 0)

        # Op toggle row
        op_box = Gtk.Box(spacing=8)
        self._load_btn   = self._gtk.Button(label="▶  LOAD",   style="color1", scale=self.bts)
        self._unload_btn = self._gtk.Button(label="◀  UNLOAD", style="color2", scale=self.bts)
        self._load_btn.connect("clicked",   self._set_op, 'load')
        self._unload_btn.connect("clicked", self._set_op, 'unload')
        op_box.pack_start(self._load_btn,   True, True, 0)
        op_box.pack_start(self._unload_btn, True, True, 0)
        outer.pack_start(op_box, False, False, 0)

        self._path_status = Gtk.Label(label="No tool selected")
        self._path_status.set_halign(Gtk.Align.CENTER)
        outer.pack_start(self._path_status, False, False, 0)

        return {'outer': outer}

    def _make_list_page(self):
        """Single-column scrollable list with always-visible sidebar."""
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=8)
        hdr = Gtk.Label(label="")
        hdr.set_halign(Gtk.Align.START)
        scroll = Gtk.ScrolledWindow()
        scroll.set_overlay_scrolling(False)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroll.add(vbox)
        outer.pack_start(hdr,    False, False, 0)
        outer.pack_start(scroll, True,  True,  0)
        return {'outer': outer, 'hdr': hdr, 'vbox': vbox}

    def _make_scroll_page(self, color_mode=False):
        """FlowBox grid for material chips or colour swatches."""
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=8)
        hdr = Gtk.Label(label="")
        hdr.set_halign(Gtk.Align.START)
        scroll = Gtk.ScrolledWindow()
        scroll.set_overlay_scrolling(False)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
        inner = Gtk.FlowBox()
        if color_mode:
            inner.set_max_children_per_line(6)
            inner.set_min_children_per_line(4)
        else:
            inner.set_max_children_per_line(4)
            inner.set_min_children_per_line(2)
        inner.set_selection_mode(Gtk.SelectionMode.NONE)
        inner.set_homogeneous(True)
        scroll.add(inner)
        outer.pack_start(hdr,    False, False, 0)
        outer.pack_start(scroll, True,  True,  0)
        return {'outer': outer, 'hdr': hdr, 'inner': inner}

    # ── Page navigation ───────────────────────────────────────────────────

    _STEPS = ['path', 'brand', 'material', 'line', 'color']

    def _page_index(self, name):
        return self._STEPS.index(name)

    def _show_page(self, name):
        self._cur = name
        self._nb.set_current_page(self._page_index(name))
        idx = self._page_index(name)

        # Back button
        self._back_btn.set_sensitive(idx > 0)

        # Save-only: visible only on colour page
        is_color = (name == 'color')
        self._save_btn.set_visible(is_color)
        self._save_btn.set_sensitive(is_color and bool(self._wz.get('color_hex')))

        # Confirm button label & sensitivity
        if name == 'path':
            if self._op == 'unload' and self._sel_path is not None:
                self._conf_btn.set_label("START UNLOAD")
                self._conf_btn.set_sensitive(True)
            else:
                self._conf_btn.set_label("Next →")
                self._conf_btn.set_sensitive(self._sel_path is not None)
        elif is_color:
            self._conf_btn.set_label("START LOAD")
            self._conf_btn.set_sensitive(bool(self._wz.get('color_hex')))
        else:
            self._conf_btn.set_label("Next →")
            self._conf_btn.set_sensitive(True)

    def _go_back(self, widget=None):
        idx = self._page_index(self._cur)
        if idx > 0:
            self._show_page(self._STEPS[idx - 1])

    # ── Path page ─────────────────────────────────────────────────────────

    def _populate_path_page(self):
        for child in self._path_grid.get_children():
            self._path_grid.remove(child)

        self._sel_btn = None
        num = len(self._path_states)
        btn_h = max(80, self._screen.height // 5)

        for i in range(num):
            state = self._path_states[i] if i < num else 'unknown'
            hex_c = self._path_hexes[i]  if i < len(self._path_hexes) else ''
            mat   = self._path_mats[i]   if i < len(self._path_mats)  else ''

            btn = self._make_path_btn(i, state, hex_c, mat)
            btn.set_size_request(-1, btn_h)
            # Re-apply selection highlight if this was the selected path
            if i == self._sel_path:
                btn.get_style_context().add_class('path-selected')
                self._sel_btn = btn
            self._path_grid.attach(btn, i % 3, i // 3, 1, 1)

        self._path_grid.show_all()

    def _make_path_btn(self, i, state, hex_c, mat):
        btn = Gtk.Button()
        btn.get_style_context().add_class("color3")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)

        # T# label
        t_lbl = Gtk.Label()
        t_lbl.set_markup(f'<b>T{i}</b>')

        # Swatch symbol + colour
        swatch = Gtk.Label()
        if state == 'loaded' and hex_c:
            h = hex_c if hex_c.startswith('#') else '#' + hex_c
            swatch.set_markup(f'<span font_size="x-large" foreground="{h}">{COLOR_SWATCH}</span>')
        elif state == 'loaded':
            swatch.set_markup(f'<span font_size="x-large" foreground="#888888">{COLOR_SWATCH}</span>')
        elif state == 'empty':
            swatch.set_markup(f'<span font_size="x-large" foreground="#666666">{EMPTY_SWATCH}</span>')
        elif state == 'partial':
            swatch.set_markup(f'<span font_size="x-large" foreground="#E65100">{PARTIAL_SWATCH}</span>')
        else:
            swatch.set_markup(f'<span font_size="x-large" foreground="#F9A825">{UNKNOWN_SWATCH}</span>')

        # Short material label
        mat_lbl = Gtk.Label()
        mat_lbl.set_markup(f'<span font_size="small">{mat[:6] if mat else "---"}</span>')

        box.pack_start(t_lbl,   False, False, 0)
        box.pack_start(swatch,  False, False, 0)
        box.pack_start(mat_lbl, False, False, 0)
        btn.add(box)
        btn.connect("clicked", self._select_path, i)
        return btn

    def _set_op(self, widget, op):
        self._op = op
        self._update_path_status()
        self._show_page('path')

    def _select_path(self, widget, path):
        # Deselect previous
        if self._sel_btn is not None:
            self._sel_btn.get_style_context().remove_class('path-selected')
        # Highlight new
        widget.get_style_context().add_class('path-selected')
        self._sel_btn  = widget
        self._sel_path = path
        self._wz['path'] = path
        self._update_path_status()
        self._show_page('path')

    def _update_path_status(self):
        if self._sel_path is None:
            self._path_status.set_text("No tool selected")
        else:
            op_txt = "LOAD" if self._op == 'load' else "UNLOAD"
            state  = self._path_states[self._sel_path] if self._sel_path < len(self._path_states) else 'unknown'
            mat    = self._path_mats[self._sel_path]   if self._sel_path < len(self._path_mats)   else ''
            info   = f" · {mat}" if mat else ''
            self._path_status.set_markup(
                f'<span foreground="{_LIME}"><b>T{self._sel_path}</b></span>'
                f'  {state.upper()}{info}  —  {op_txt}')

    # ── Brand page ────────────────────────────────────────────────────────

    def _go_to_brand(self):
        if _db is None:
            self._screen.show_popup_message("sa_filament_db not available")
            return
        brands = _db.scan_brands(_BRANDS_DIR)
        page = self._pages['brand']
        page['hdr'].set_text(f"T{self._sel_path} — Select Brand")
        self._fill_list(page['vbox'], [
            (name, self._select_brand, (name, fpath))
            for name, fpath in brands
        ])
        self._show_page('brand')

    def _select_brand(self, widget, args):
        name, fpath = args
        self._wz['brand_name'] = name
        self._wz['brand_path'] = fpath
        brand_data = _db.load_brand(fpath)
        self._wz['brand_data'] = brand_data
        materials = _db.get_materials(brand_data)
        page = self._pages['material']
        page['hdr'].set_text(f"T{self._sel_path} — {name} — Select Material")
        self._fill_flow(page['inner'], [
            (m, self._select_material, m) for m in materials
        ])
        self._show_page('material')

    # ── Material page ─────────────────────────────────────────────────────

    def _select_material(self, widget, material):
        self._wz['material'] = material
        raw = _db.get_product_lines(self._wz['brand_data'], material)
        lines = [{**pl, 'line_id': lid} for lid, pl in raw]
        if len(lines) == 1:
            self._select_line(None, lines[0])
            return
        page = self._pages['line']
        page['hdr'].set_text(
            f"T{self._sel_path} — {self._wz['brand_name']} {material} — Select Product Line")
        self._fill_list(page['vbox'], [
            (f"{pl['display_name']}  ·  {pl['load_temp']}°C  ·  Bed {pl.get('bed_temp','—')}°C",
             self._select_line, pl)
            for pl in lines
        ])
        self._show_page('line')

    # ── Line page ─────────────────────────────────────────────────────────

    def _select_line(self, widget, pl):
        self._wz['line']         = pl.get('line_id', '')
        self._wz['line_name']    = pl.get('display_name', '')
        self._wz['load_temp']    = pl.get('load_temp',    200)
        self._wz['unload_temp']  = pl.get('unload_temp',  185)
        self._wz['purge_speed']  = pl.get('purge_speed',  5)
        self._wz['purge_length'] = pl.get('purge_length', 30)
        colors = pl.get('colors', [])
        page = self._pages['color']
        page['hdr'].set_text(
            f"T{self._sel_path} — {pl.get('display_name','')} — Select Color")
        self._fill_color_flow(page['inner'], colors)
        self._wz['color_hex'] = ''
        self._show_page('color')

    # ── Color page ────────────────────────────────────────────────────────

    def _fill_color_flow(self, flowbox, colors):
        for child in flowbox.get_children():
            flowbox.remove(child)
        for c in colors:
            flowbox.add(self._make_color_button(c))
        flowbox.show_all()

    def _make_color_button(self, c):
        hex_c = c.get('hex', '')
        name  = c.get('name', '?')
        btn = Gtk.Button()
        btn.get_style_context().add_class("color3")

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        vbox.set_valign(Gtk.Align.CENTER)
        vbox.set_halign(Gtk.Align.CENTER)

        swatch = Gtk.Label()
        h = hex_c if hex_c.startswith('#') else ('#' + hex_c if hex_c else '#808080')
        swatch.set_markup(f'<span font_size="xx-large" foreground="{h}">{COLOR_SWATCH}</span>')

        name_lbl = Gtk.Label()
        name_lbl.set_line_wrap(True)
        name_lbl.set_max_width_chars(9)
        name_lbl.set_justify(Gtk.Justification.CENTER)
        name_lbl.set_markup(f'<span font_size="small">{name}</span>')

        vbox.pack_start(swatch,   False, False, 0)
        vbox.pack_start(name_lbl, False, False, 0)
        btn.add(vbox)
        btn.connect("clicked", self._select_color, c)
        return btn

    def _select_color(self, widget, c):
        self._wz['color_name'] = c.get('name', '')
        self._wz['color_hex']  = c.get('hex',  '')
        self._wz['color_id']   = c.get('id',   '')
        page = self._pages['color']
        hex_c = self._wz['color_hex']
        h = hex_c if hex_c.startswith('#') else '#' + hex_c if hex_c else '#808080'
        page['hdr'].set_markup(
            f"T{self._sel_path} — "
            f'<span foreground="{h}">{COLOR_SWATCH}</span>'
            f" {self._wz['color_name']}  ({hex_c})")
        # Enable START LOAD and SAVE ONLY
        self._conf_btn.set_sensitive(True)
        self._save_btn.set_sensitive(True)

    # ── Utilities ─────────────────────────────────────────────────────────

    def _list_btn_h(self):
        return max(80, self._screen.height // 5)

    def _fill_list(self, vbox, items):
        for child in vbox.get_children():
            vbox.remove(child)
        for label, callback, arg in items:
            btn = self._gtk.Button(label=label, style="color3", scale=self.bts)
            btn.set_size_request(-1, self._list_btn_h())
            btn.connect("clicked", callback, arg)
            vbox.pack_start(btn, False, False, 0)
        vbox.show_all()

    def _fill_flow(self, flowbox, items):
        for child in flowbox.get_children():
            flowbox.remove(child)
        for label, callback, arg in items:
            btn = self._gtk.Button(label=label, style="color3", scale=self.bts)
            btn.connect("clicked", callback, arg)
            btn.set_hexpand(True)
            flowbox.add(btn)
        flowbox.show_all()

    def _set_material_gcode(self):
        wz = self._wz
        path = self._sel_path
        return (
            'SA_SET_MATERIAL TOOL={tool} MATERIAL="{mat}" BRAND="{brand}" '
            'LINE="{line}" COLOR_NAME="{cname}" COLOR_HEX="{chex}" '
            'LOAD_TEMP={lt} UNLOAD_TEMP={ut} '
            'PURGE_SPEED={ps} PURGE_LENGTH={pl}'.format(
                tool=path,
                mat=wz.get('material', ''),
                brand=wz.get('brand_name', ''),
                line=wz.get('line', ''),
                cname=wz.get('color_name', ''),
                chex=wz.get('color_hex', ''),
                lt=wz.get('load_temp', 200),
                ut=wz.get('unload_temp', 185),
                ps=wz.get('purge_speed', 5),
                pl=wz.get('purge_length', 30),
            ))

    def _gcode(self, script):
        self._screen._ws.klippy.gcode_script(script)

    # ── Confirm / Save-only ───────────────────────────────────────────────

    def _confirm(self, widget=None):
        if self._cur == 'path':
            if self._sel_path is None:
                return
            if self._op == 'unload':
                self._do_unload()
            else:
                self._go_to_brand()
        elif self._cur == 'color':
            if self._wz.get('color_hex'):
                self._gcode(self._set_material_gcode())
                self._gcode(f"SA_LOAD TOOL={self._sel_path}")
                self._screen.show_popup_message(f"Loading T{self._sel_path} …", level=1)
                self._reset()
        else:
            idx = self._page_index(self._cur)
            if idx < len(self._STEPS) - 1:
                self._show_page(self._STEPS[idx + 1])

    def _save_only(self, widget=None):
        """Save material profile without triggering a load."""
        if not self._wz.get('color_hex'):
            return
        self._gcode(self._set_material_gcode())
        self._screen.show_popup_message(
            f"T{self._sel_path} — material profile saved", level=1)
        self._reset()

    def _do_unload(self):
        path = self._sel_path
        if path is None:
            return
        self._gcode(f"SA_UNLOAD TOOL={path}")
        self._screen.show_popup_message(f"Unloading T{path} …", level=1)
        self._reset()

    def _reset(self):
        self._wz       = {}
        self._sel_path = None
        self._sel_btn  = None
        self._show_page('path')

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def _query_sa(self):
        try:
            resp = self._screen.apiclient.send_request(
                "printer/objects/query?stealth_autoloader")
            if resp and 'status' in resp:
                return resp['status'].get('stealth_autoloader', {})
        except Exception as e:
            logger.error("sa_load_unload: query failed: %s", e)
        return {}

    def activate(self):
        self._screen._ws.klippy.object_subscription(
            {"objects": {"stealth_autoloader": None}})
        sa = self._query_sa()
        self._apply_sa(sa)
        self._reset()

    def _apply_sa(self, sa):
        num = sa.get("num_paths", 0)
        self._path_states = sa.get("path_states",     ['unknown'] * num)
        self._path_hexes  = sa.get("path_color_hexes", [''] * num)
        self._path_mats   = sa.get("path_materials",   [''] * num)
        self._populate_path_page()

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("stealth_autoloader")
        if sa is None:
            return
        new_states = sa.get("path_states", self._path_states)
        # If a loaded path goes empty/unknown, clear its stored material
        for i, (old, new) in enumerate(zip(self._path_states, new_states)):
            if old == 'loaded' and new in ('empty', 'unknown'):
                GLib.idle_add(
                    self._gcode,
                    f'SA_SET_MATERIAL TOOL={i} MATERIAL="" BRAND="" LINE="" '
                    f'COLOR_NAME="" COLOR_HEX="" LOAD_TEMP=200 UNLOAD_TEMP=185 '
                    f'PURGE_SPEED=5 PURGE_LENGTH=30')
        new_hexes = sa.get("path_color_hexes", self._path_hexes)
        new_mats  = sa.get("path_materials",   self._path_mats)
        changed = (new_states != self._path_states or
                   new_hexes  != self._path_hexes  or
                   new_mats   != self._path_mats)
        if changed:
            self._path_states = new_states
            self._path_hexes  = new_hexes
            self._path_mats   = new_mats
            GLib.idle_add(self._populate_path_page)
