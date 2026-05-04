import os
import sys
import math
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import logging

_panels_dir = os.path.dirname(os.path.abspath(__file__))
_ks_root    = os.path.dirname(_panels_dir)
for _p in (_ks_root, _panels_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sa_button_style as _sbs
import sa_subscription as _sasub
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_load_unload')

try:
    import sa_color_swatch as _cs
except ImportError:
    _cs = None
    logger.error("sa_load_unload: could not import sa_color_swatch")

_BRANDS_DIR = os.path.expanduser("~/printer_data/config/autoloader/filament_profiles")

try:
    import sa_filament_db as _db
except ImportError:
    _db = None
    logger.error("sa_load_unload: could not import sa_filament_db")

COLOR_SWATCH   = '\u2b24'
EMPTY_SWATCH   = '\u2298'
UNKNOWN_SWATCH = '\u25cc'
PARTIAL_SWATCH = '\u25d1'

_LIME = '#8BC34A'


def _rgba_from_hex(hex_c):
    rgba = Gdk.RGBA()
    if hex_c and Gdk.RGBA.parse(rgba, hex_c):
        return rgba
    if hex_c and Gdk.RGBA.parse(rgba, '#' + hex_c):
        return rgba
    return None


def _hex_to_rgb01(hex_c):
    h = (hex_c or '808080').lstrip('#')
    if len(h) == 3:
        h = ''.join(c*2 for c in h)
    if len(h) != 6:
        return (0.5, 0.5, 0.5)
    return (int(h[0:2], 16)/255.0, int(h[2:4], 16)/255.0, int(h[4:6], 16)/255.0)


def _luminance(r, g, b):
    return 0.2126*r + 0.7152*g + 0.0722*b


def _draw_color_swatch(widget, cr, r, g, b):
    w = widget.get_allocated_width()
    h = widget.get_allocated_height()
    rad = 5
    cr.set_source_rgb(r, g, b)
    cr.arc(rad,   rad,   rad, math.pi,       3*math.pi/2)
    cr.arc(w-rad, rad,   rad, 3*math.pi/2,   0)
    cr.arc(w-rad, h-rad, rad, 0,              math.pi/2)
    cr.arc(rad,   h-rad, rad, math.pi/2,     math.pi)
    cr.close_path()
    cr.fill()
    return False


class Panel(ScreenPanel):
    """Load/Unload wizard: select path -> set material -> load/unload."""

    def __init__(self, screen, title):
        super().__init__(screen, title or "Load / Unload")
        _sbs.apply()

        self._op             = 'load'
        self._wz             = {}
        self._path_states      = []
        self._path_hexes       = []
        self._path_mats        = []
        self._path_color_types = []
        self._path_hex2s       = []
        self._path_hex3s       = []
        self._path_entry       = []
        self._path_th        = []
        self._path_ex        = []
        self._sel_path       = None
        self._sel_btn        = None
        self._profile_timers = {}   # path -> GLib timeout id

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

        # ── Nav bar ──────────────────────────────────────────────────────────
        nav = Gtk.Box(spacing=4, margin=4)

        self._back_btn = _sbs.make("\u2190 Back",    "sa-btn")
        self._save_btn = _sbs.make("SAVE ONLY",      "sa-btn-warn")
        self._conf_btn = _sbs.make("Next \u2192",    "sa-btn")
        self._conf_btn.set_no_show_all(True)  # prevent show_all() from overriding hide
        self._conf_btn.set_visible(False)     # hidden on path page; shown only in wizard

        self._back_btn.connect("clicked", self._go_back)
        self._save_btn.connect("clicked", self._save_only)
        self._conf_btn.connect("clicked", self._confirm)

        for btn in (self._back_btn, self._save_btn, self._conf_btn):
            # Match the action-button row height so the bottom strip stays
            # compact and the whole panel fits on a 480px screen.
            btn.set_size_request(-1, 50)
            nav.pack_start(btn, True, True, 0)

        self.content.pack_end(nav, False, False, 0)

        self._show_page('path')

    # ── Page factories ────────────────────────────────────────────────────

    def _make_path_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin=4)

        hdr = Gtk.Label(label="Select a tool path")
        hdr.set_halign(Gtk.Align.CENTER)
        outer.pack_start(hdr, False, False, 0)

        self._path_grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True,
                                   row_spacing=4, column_spacing=4)
        outer.pack_start(self._path_grid, True, True, 0)

        op_box = Gtk.Box(spacing=4)
        self._load_btn   = _sbs.make("\u25b6  LOAD",   "sa-btn")
        self._unload_btn = _sbs.make("\u25c0  UNLOAD", "sa-btn")
        self._setmat_btn = _sbs.make("\u270e  MATERIAL", "sa-btn")
        self._clear_btn  = _sbs.make("\u2715  CLEAR",  "sa-btn-warn")
        self._load_btn.connect("clicked",    self._do_load)
        self._unload_btn.connect("clicked",  self._do_unload)
        self._setmat_btn.connect("clicked",  self._do_set_material)
        self._clear_btn.connect("clicked",   self._do_clear_profile)
        # Cap action-button height so the row doesn't blow up to default GTK
        # button height on KS themes that draw tall buttons.
        for btn in (self._load_btn, self._unload_btn, self._setmat_btn, self._clear_btn):
            btn.set_size_request(-1, 50)
            op_box.pack_start(btn, True, True, 0)
        outer.pack_start(op_box, False, False, 0)

        self._path_status = Gtk.Label(label="No tool selected")
        self._path_status.set_halign(Gtk.Align.CENTER)
        self._path_status.set_size_request(-1, 22)
        outer.pack_start(self._path_status, False, False, 0)

        return {'outer': outer}

    def _make_list_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=6)
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
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=6)
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

    def _has_profile(self, path):
        """True if this path already has a material/color profile assigned."""
        if path is None:
            return False
        return bool(self._path_hexes[path] if path < len(self._path_hexes) else False)

    def _show_page(self, name):
        self._cur = name
        self._nb.set_current_page(self._page_index(name))
        idx       = self._page_index(name)
        is_path   = (name == 'path')
        is_color  = (name == 'color')
        has_path  = self._sel_path is not None
        has_color = bool(self._wz.get('color_hex'))
        has_prof  = self._has_profile(self._sel_path)

        self._back_btn.set_sensitive(idx > 0)

        # SAVE ONLY: only on color page once a color is chosen
        self._save_btn.set_sensitive(is_color and has_color)

        if is_path:
            state = self._effective_state(self._sel_path) if has_path else 'unknown'
            # LOAD: needs profile, and not already fully loaded
            self._load_btn.set_sensitive(has_path and has_prof and state != 'loaded')
            # UNLOAD: needs path with filament present (not empty)
            self._unload_btn.set_sensitive(has_path and state != 'empty')
            # SET MATERIAL: always available when a path is selected
            self._setmat_btn.set_sensitive(has_path)
            # CLEAR PROFILE: only when path is empty (no filament to protect)
            self._clear_btn.set_sensitive(has_path and has_prof and state == 'empty')
            # conf_btn never shown on path page
            self._conf_btn.set_visible(False)
        elif is_color:
            self._conf_btn.set_visible(True)
            self._conf_btn.set_label("DONE")
            self._conf_btn.set_sensitive(has_color)
        else:
            self._conf_btn.set_visible(True)
            self._conf_btn.set_label("Next \u2192")
            self._conf_btn.set_sensitive(True)

    def _go_back(self, widget=None):
        idx = self._page_index(self._cur)
        if idx > 0:
            self._show_page(self._STEPS[idx - 1])

    # ── Path page ─────────────────────────────────────────────────────────

    def _path_btn_h(self):
        # Vertical budget on the path page (480px screen example):
        #   ~30  page header label
        #   ~50  action-button row (LOAD/UNLOAD/MATERIAL/CLEAR — height-capped)
        #   ~22  path status label (height-capped)
        #   ~50  bottom nav bar (Back/Save Only/Next — height-capped)
        #   ~24  margins + spacing between rows
        # Reserve 176px → leaves ~304 for the path grid → 152/row max.
        # Cap at 64px so the rest of the column has breathing room and no
        # element gets clipped on smaller screens.
        avail = self._screen.height - 176
        num   = len(self._path_states) or 6
        rows  = (num + 2) // 3
        return max(48, min(64, avail // rows))

    def _populate_path_page(self):
        for child in self._path_grid.get_children():
            self._path_grid.remove(child)

        self._sel_btn = None
        num   = len(self._path_states)
        btn_h = self._path_btn_h()

        for i in range(num):
            state      = self._path_states[i]      if i < num                          else 'unknown'
            hex_c      = self._path_hexes[i]        if i < len(self._path_hexes)       else ''
            mat        = self._path_mats[i]         if i < len(self._path_mats)        else ''
            color_type = self._path_color_types[i]  if i < len(self._path_color_types) else 'single'
            hex_2      = self._path_hex2s[i]        if i < len(self._path_hex2s)       else ''
            hex_3      = self._path_hex3s[i]        if i < len(self._path_hex3s)       else ''

            btn = self._make_path_btn(i, state, hex_c, mat, color_type, hex_2, hex_3)
            btn.set_size_request(-1, btn_h)
            if i == self._sel_path:
                btn.get_style_context().add_class('path-selected')
                self._sel_btn = btn
            self._path_grid.attach(btn, i % 3, i // 3, 1, 1)

        self._path_grid.show_all()
        # Refresh button states after path grid rebuild
        if self._cur == 'path':
            has_path = self._sel_path is not None
            has_prof = self._has_profile(self._sel_path)
            state    = self._effective_state(self._sel_path) if has_path else 'unknown'
            self._load_btn.set_sensitive(has_path and has_prof and state != 'loaded')
            self._unload_btn.set_sensitive(has_path and state != 'empty')
            self._setmat_btn.set_sensitive(has_path)
            self._clear_btn.set_sensitive(has_path and has_prof and state == 'empty')
            self._conf_btn.set_sensitive(False)
            self._conf_btn.set_visible(False)

    def _make_path_btn(self, i, state, hex_c, mat,
                       color_type='single', hex_2='', hex_3=''):
        btn = Gtk.Button()
        btn.get_style_context().add_class("sa-btn")

        btn_h   = self._path_btn_h()
        # Mid-size swatch — between the original (44–88) and the recent
        # too-compact (28–36). Aimed at 36–48 px depending on button height.
        sw_size = max(36, min(48, btn_h - 24))

        # 3-column equal-width grid: T# | swatch | material
        # column_homogeneous makes each column exactly 1/3 of the button
        # width, which puts the swatch's column centered horizontally. The
        # swatch widget then halign=CENTER inside that column, so it lands at
        # the geometric center of the button regardless of label widths.
        row = Gtk.Grid()
        row.set_column_homogeneous(True)
        row.set_valign(Gtk.Align.CENTER)
        row.set_halign(Gtk.Align.FILL)
        row.set_hexpand(True)

        # Tool number — centered in the left third
        t_lbl = Gtk.Label()
        t_lbl.set_markup('<b><span font_size="large">T%d</span></b>' % i)
        t_lbl.set_halign(Gtk.Align.CENTER)
        t_lbl.set_hexpand(True)

        # Color swatch DrawingArea — centered in the middle third
        if _cs is not None:
            if hex_c:
                hexes = [hex_c]
                if hex_2: hexes.append(hex_2)
                if hex_3: hexes.append(hex_3)
                swatch_w = _cs.make_swatch_da(sw_size, hexes, color_type)
            elif state == 'empty':
                swatch_w = _cs.make_state_da(sw_size, 'empty')
            elif state == 'partial':
                swatch_w = _cs.make_state_da(sw_size, 'partial')
            elif state == 'loaded':
                swatch_w = _cs.make_state_da(sw_size, 'loaded_no_color')
            else:
                swatch_w = _cs.make_state_da(sw_size, 'unknown')
        else:
            swatch_w = Gtk.Label()
            h = hex_c if (hex_c and hex_c.startswith('#')) else ('#' + hex_c if hex_c else '#888888')
            swatch_w.set_markup('<span font_size="xx-large" foreground="%s">%s</span>' % (h, COLOR_SWATCH))
        swatch_w.set_halign(Gtk.Align.CENTER)
        swatch_w.set_valign(Gtk.Align.CENTER)
        swatch_w.set_hexpand(True)

        # Material label — centered in the right third
        mat_lbl = Gtk.Label()
        mat_lbl.set_markup('<span font_size="small">%s</span>' % (mat[:8] if mat else '---'))
        mat_lbl.set_halign(Gtk.Align.CENTER)
        mat_lbl.set_hexpand(True)
        mat_lbl.set_ellipsize(3)
        mat_lbl.set_max_width_chars(8)

        row.attach(t_lbl,    0, 0, 1, 1)
        row.attach(swatch_w, 1, 0, 1, 1)
        row.attach(mat_lbl,  2, 0, 1, 1)
        btn.add(row)
        btn.connect("clicked", self._select_path, i)
        return btn

    def _select_path(self, widget, path):
        # Defensive: clear the class from EVERY button in the grid before
        # marking the new selection. Relying on self._sel_btn alone misses
        # cases where a button got the class from _populate_path_page (after
        # a status update or _on_filament_inserted set _sel_path) but
        # self._sel_btn was reset to None by _reset() in between.
        self._clear_path_selection()
        widget.get_style_context().add_class('path-selected')
        self._sel_btn  = widget
        self._sel_path = path
        self._wz['path'] = path
        self._update_path_status()
        self._show_page('path')

    def _clear_path_selection(self):
        """Remove the 'path-selected' class from every path button in the grid."""
        for child in self._path_grid.get_children():
            child.get_style_context().remove_class('path-selected')
        self._sel_btn = None

    def _update_path_status(self):
        if self._sel_path is None:
            self._path_status.set_text("No tool selected")
            return
        i      = self._sel_path
        state  = self._effective_state(i)
        mat    = self._path_mats[i]  if i < len(self._path_mats)  else ''
        hex_c  = self._path_hexes[i] if i < len(self._path_hexes) else ''
        info   = ' \u00b7 %s' % mat if mat else ''
        if hex_c:
            h = hex_c if hex_c.startswith('#') else '#' + hex_c
            swatch_mu = ' <span foreground="%s">%s</span>' % (h, COLOR_SWATCH)
        else:
            swatch_mu = ''
        has_prof = self._has_profile(i)
        hint = '' if has_prof else '  \u2014 Set material to enable LOAD'
        self._path_status.set_markup(
            '<span foreground="%s"><b>T%d</b></span>%s  %s%s%s'
            % (_LIME, i, swatch_mu, state.upper(), info, hint))

    def _effective_state(self, i):
        stored = self._path_states[i] if i < len(self._path_states) else 'unknown'
        entry  = self._path_entry[i] if i < len(self._path_entry) else None
        th     = self._path_th[i]    if i < len(self._path_th)    else None
        ex     = self._path_ex[i]    if i < len(self._path_ex)    else None
        # No sensor data → trust backend stored state
        if entry is None:
            return stored
        th = th if th is not None else False
        ex = ex if ex is not None else False
        # All sensors clear → definitively empty
        if not entry and not th and not ex:
            return 'empty'
        # Filament at extruder and toolhead → loaded (entry may be clear if roll ran out)
        if th and ex:
            return 'loaded'
        # Any sensor active → partial
        if entry or th or ex:
            return 'partial'
        return stored

    # ── Brand page ────────────────────────────────────────────────────────

    def _go_to_brand(self):
        if _db is None:
            self._screen.show_popup_message("sa_filament_db not available")
            return
        brands = _db.scan_brands(_BRANDS_DIR)
        page = self._pages['brand']
        page['hdr'].set_text("T%d \u2014 Select Brand" % self._sel_path)
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
        page['hdr'].set_text("T%d \u2014 %s \u2014 Select Material" % (self._sel_path, name))
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
            "T%d \u2014 %s %s \u2014 Select Product Line"
            % (self._sel_path, self._wz['brand_name'], material))
        self._fill_list(page['vbox'], [
            ("%s  \u00b7  %s\u00b0C  \u00b7  Bed %s\u00b0C"
             % (pl['display_name'], pl['load_temp'], pl.get('bed_temp', '\u2014')),
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
            "T%d \u2014 %s \u2014 Select Color" % (self._sel_path, pl.get('display_name', '')))
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
        hex_c      = c.get('hex', '')
        name       = c.get('name', '?')
        color_type = c.get('color_type', 'single')
        hex_list   = _db.get_color_hexes(c) if _db else [hex_c]

        btn = Gtk.Button()
        btn.get_style_context().add_class("sa-btn")
        # Compact color-picker chip (was 72×82) so the FlowBox can fit more
        # colors without overflowing on small screens.
        btn.set_size_request(60, 70)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        if _cs is not None:
            # Cairo swatch — supports pie/gradient for multi-color
            da = _cs.make_swatch_da(40, hex_list, color_type)
        else:
            da = Gtk.DrawingArea()
            da.set_size_request(-1, 40)
            r, g, b = _hex_to_rgb01(hex_c)
            da.connect("draw", lambda w, cr, _r=r, _g=g, _b=b: _draw_color_swatch(w, cr, _r, _g, _b))

        r, g, b = _hex_to_rgb01(hex_c)
        lum = _luminance(r, g, b)
        fg  = "#FFFFFF" if lum < 0.45 else "#212121"
        name_lbl = Gtk.Label()
        name_lbl.set_ellipsize(3)
        name_lbl.set_max_width_chars(9)
        name_lbl.set_markup('<span font_size="x-small" foreground="%s">%s</span>' % (fg, name))
        name_lbl.set_halign(Gtk.Align.CENTER)

        vbox.pack_start(da,       True,  True,  0)
        vbox.pack_start(name_lbl, False, False, 3)
        btn.add(vbox)
        btn.connect("clicked", self._select_color, c)
        return btn

    def _select_color(self, widget, c):
        self._wz['color_name']  = c.get('name', '')
        self._wz['color_hex']   = c.get('hex',  '')
        self._wz['color_id']    = c.get('id',   '')
        self._wz['color_type']  = c.get('color_type', 'single')
        self._wz['color_hex_2'] = c.get('hex_2', '')
        self._wz['color_hex_3'] = c.get('hex_3', '')
        page = self._pages['color']
        hex_c = self._wz['color_hex']
        h = hex_c if hex_c.startswith('#') else '#' + hex_c if hex_c else '#808080'
        page['hdr'].set_markup(
            "T%d \u2014 "
            '<span foreground="%s">%s</span>'
            " %s  (%s)"
            % (self._sel_path, h, COLOR_SWATCH,
               self._wz['color_name'], hex_c))
        self._conf_btn.set_sensitive(True)
        self._save_btn.set_sensitive(True)

    # ── Utilities ─────────────────────────────────────────────────────────

    def _list_btn_h(self):
        return max(60, min(90, self._screen.height // 5))

    def _fill_list(self, vbox, items):
        for child in vbox.get_children():
            vbox.remove(child)
        for label, callback, arg in items:
            btn = _sbs.make(label)
            btn.set_size_request(-1, self._list_btn_h())
            btn.connect("clicked", callback, arg)
            vbox.pack_start(btn, False, False, 0)
        vbox.show_all()

    def _fill_flow(self, flowbox, items):
        for child in flowbox.get_children():
            flowbox.remove(child)
        for label, callback, arg in items:
            btn = _sbs.make(label)
            btn.set_hexpand(True)
            btn.connect("clicked", callback, arg)
            flowbox.add(btn)
        flowbox.show_all()

    def _set_material_gcode(self):
        wz = self._wz
        return (
            'SA_SET_MATERIAL TOOL={tool} MATERIAL="{mat}" BRAND="{brand}" '
            'LINE="{line}" COLOR_NAME="{cname}" COLOR_HEX="{chex}" '
            'LOAD_TEMP={lt} UNLOAD_TEMP={ut} '
            'PURGE_SPEED={ps} PURGE_LENGTH={pl}'.format(
                tool=self._sel_path,
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

    # ── Confirm / Save-only / Load / Unload ──────────────────────────────

    def _confirm(self, widget=None):
        if self._cur == 'path':
            if self._sel_path is None:
                return
            self._go_to_brand()
        elif self._cur == 'color':
            if self._wz.get('color_hex'):
                self._save_profile_and_return(show_msg=True)
        else:
            idx = self._page_index(self._cur)
            if idx < len(self._STEPS) - 1:
                self._show_page(self._STEPS[idx + 1])

    def _save_only(self, widget=None):
        if not self._wz.get('color_hex'):
            return
        self._save_profile_and_return(show_msg=False)

    def _save_profile_and_return(self, show_msg=True):
        """Commit wizard profile to path, refresh grid, start auto-clear timer."""
        i = self._sel_path
        if i is None:
            return
        self._gcode(self._set_material_gcode())
        if show_msg:
            self._screen.show_popup_message(
                "T%d material saved \u2014 press LOAD to begin" % i, level=1)
        else:
            self._screen.show_popup_message(
                "T%d material profile saved" % i, level=1)
        # Update local cache immediately so grid refreshes without waiting for
        # a status update from Klipper
        if i < len(self._path_hexes):
            self._path_hexes[i] = self._wz.get('color_hex', '')
            self._path_mats[i]  = self._wz.get('material', '')
        self._wz = {}
        self._populate_path_page()   # rebuild grid with new color/material
        self._show_page('path')
        self._update_path_status()
        self._start_profile_timer(i)

    # ── 5-minute auto-clear timer ─────────────────────────────────────────────

    def _start_profile_timer(self, path):
        """If path isn't loaded within 5 minutes of profile save, clear it."""
        self._cancel_profile_timer(path)
        tid = GLib.timeout_add(5 * 60 * 1000, self._profile_timer_fired, path)
        self._profile_timers[path] = tid

    def _cancel_profile_timer(self, path):
        tid = self._profile_timers.pop(path, None)
        if tid is not None:
            GLib.source_remove(tid)

    def _profile_timer_fired(self, path):
        """Auto-clear profile if path still hasn't been loaded."""
        self._profile_timers.pop(path, None)
        state = self._effective_state(path)
        if state in ('empty', 'unknown', 'partial'):
            logger.info("sa_load_unload: profile timer — clearing T%d (state=%s)", path, state)
            self._clear_profile_gcode(path)
        return False  # don't repeat

    def _do_set_material(self, widget=None):
        if self._sel_path is None:
            return
        self._go_to_brand()

    def _do_load(self, widget=None):
        path = self._sel_path
        if path is None:
            return
        if not self._has_profile(path):
            self._screen.show_popup_message(
                "Set material profile first", level=2)
            return
        self._cancel_profile_timer(path)
        self._gcode("SA_LOAD TOOL=%d" % path)
        self._screen.show_panel('sa_main', 'SA Status')

    def _do_unload(self, widget=None):
        path = self._sel_path
        if path is None:
            return
        self._gcode("SA_UNLOAD TOOL=%d" % path)
        self._screen.show_popup_message("Unloading T%d \u2026" % path, level=1)
        self._reset()

    def _do_clear_profile(self, widget=None):
        path = self._sel_path
        if path is None:
            return
        state = self._effective_state(path)
        if state != 'empty':
            self._screen.show_popup_message(
                "Cannot clear profile — unload filament first", level=2)
            return
        self._cancel_profile_timer(path)
        self._clear_profile_gcode(path)
        # Update local cache
        if path < len(self._path_hexes):
            self._path_hexes[path] = ''
            self._path_mats[path]  = ''
        self._populate_path_page()
        self._update_path_status()
        self._screen.show_popup_message("T%d profile cleared" % path, level=1)

    def _clear_profile_gcode(self, path):
        self._gcode(
            'SA_SET_MATERIAL TOOL=%d MATERIAL="" BRAND="" LINE="" '
            'COLOR_NAME="" COLOR_HEX="" LOAD_TEMP=200 UNLOAD_TEMP=185 '
            'PURGE_SPEED=5 PURGE_LENGTH=30' % path)

    def _reset(self):
        self._wz       = {}
        self._sel_path = None
        self._sel_btn  = None
        self._show_page('path')

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def _query_sa(self):
        try:
            resp = self._screen.apiclient.send_request(
                "printer/objects/query?autoloader")
            if resp and 'status' in resp:
                return resp['status'].get('autoloader', {})
        except Exception as e:
            logger.error("sa_load_unload: query failed: %s", e)
        return {}

    def activate(self):
        # Combined subscription so base_panel's toolhead-temp display
        # keeps updating during autoloader-triggered tool changes.
        self._screen._ws.klippy.object_subscription(
            {"objects": _sasub.build_subscription(self._screen)})
        sa = self._query_sa()
        self._apply_sa(sa)
        self._reset()

    def _apply_sa(self, sa):
        num = sa.get("num_paths", 0)
        self._path_states      = sa.get("path_states",      ['unknown'] * num)
        self._path_hexes       = sa.get("path_color_hexes", [''] * num)
        self._path_mats        = sa.get("path_materials",   [''] * num)
        self._path_color_types = [sa.get('color_type_%d' % i, 'single') for i in range(num)]
        self._path_hex2s       = [sa.get('color_hex_2_%d' % i, '')      for i in range(num)]
        self._path_hex3s       = [sa.get('color_hex_3_%d' % i, '')      for i in range(num)]
        self._path_entry       = sa.get("entry_filament",   [False] * num)
        self._path_th          = sa.get("toolhead_filament",[False] * num)
        self._path_ex          = sa.get("extruder_filament",[False] * num)
        self._populate_path_page()

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("autoloader")
        if sa is None:
            return

        new_entry  = sa.get("entry_filament",    self._path_entry)
        new_th     = sa.get("toolhead_filament",  self._path_th)
        new_ex     = sa.get("extruder_filament",  self._path_ex)
        new_states = sa.get("path_states",        self._path_states)
        new_hexes  = sa.get("path_color_hexes",   self._path_hexes)
        new_mats   = sa.get("path_materials",     self._path_mats)
        num        = len(new_states)
        new_ctypes = [sa.get('color_type_%d' % i, self._path_color_types[i]
                             if i < len(self._path_color_types) else 'single')
                      for i in range(num)]
        new_hex2s  = [sa.get('color_hex_2_%d' % i, self._path_hex2s[i]
                             if i < len(self._path_hex2s) else '')
                      for i in range(num)]
        new_hex3s  = [sa.get('color_hex_3_%d' % i, self._path_hex3s[i]
                             if i < len(self._path_hex3s) else '')
                      for i in range(num)]

        for i in range(len(new_entry)):
            old_entry = self._path_entry[i] if i < len(self._path_entry) else False
            old_th    = self._path_th[i]    if i < len(self._path_th)    else False
            old_ex    = self._path_ex[i]    if i < len(self._path_ex)    else False

            if not old_entry and new_entry[i]:
                GLib.idle_add(self._on_filament_inserted, i)

            had_fil = old_entry or old_th or old_ex
            has_fil = (new_entry[i] or
                       (new_th[i] if i < len(new_th) else False) or
                       (new_ex[i] if i < len(new_ex) else False))
            if had_fil and not has_fil:
                GLib.idle_add(self._clear_material, i)

        changed = (new_states != self._path_states or
                   new_hexes  != self._path_hexes  or
                   new_mats   != self._path_mats   or
                   new_ctypes != self._path_color_types or
                   new_hex2s  != self._path_hex2s  or
                   new_hex3s  != self._path_hex3s  or
                   new_entry  != self._path_entry  or
                   new_th     != self._path_th     or
                   new_ex     != self._path_ex)
        if changed:
            self._path_states      = new_states
            self._path_hexes       = new_hexes
            self._path_mats        = new_mats
            self._path_color_types = new_ctypes
            self._path_hex2s       = new_hex2s
            self._path_hex3s       = new_hex3s
            self._path_entry       = new_entry
            self._path_th          = new_th
            self._path_ex          = new_ex
            GLib.idle_add(self._populate_path_page)
            if self._sel_path is not None:
                GLib.idle_add(self._update_path_status)

    def _on_filament_inserted(self, path):
        self._screen.show_panel('sa_load_unload', 'Load / Unload')
        self._sel_path = path
        self._wz['path'] = path
        self._populate_path_page()
        self._update_path_status()
        self._show_page('path')
        return False

    def _clear_material(self, path):
        self._cancel_profile_timer(path)
        self._clear_profile_gcode(path)
        return False
        return False
