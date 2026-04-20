import os
import sys
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import logging
from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger('klipperscreen.sa_load_unload')

_BRANDS_DIR = os.path.expanduser("~/stealth-autoloader/filaments/brands")

# Add KlipperScreen root to path so sa_filament_db is importable
_ks_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ks_root not in sys.path:
    sys.path.insert(0, _ks_root)

try:
    import sa_filament_db as _db
except ImportError:
    _db = None
    logger.error("sa_load_unload: could not import sa_filament_db")


class Panel(ScreenPanel):
    """4-step load/unload wizard: path → brand → material/line → color → confirm."""

    def __init__(self, screen, title):
        super().__init__(screen, title or "Load / Unload")

        self._step = 0         # 0=path, 1=brand, 2=material, 3=color
        self._op   = 'load'    # 'load' or 'unload'
        self._wz   = {}        # wizard state
        self._path_states = []

        # Stack holds one page per step
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)

        self._pages = {}
        self._pages['path']     = self._make_path_page()
        self._pages['brand']    = self._make_scroll_page()
        self._pages['material'] = self._make_scroll_page()
        self._pages['color']    = self._make_scroll_page()

        for name, page in self._pages.items():
            self._stack.add_named(page['outer'], name)

        self.content.pack_start(self._stack, True, True, 0)

        # Nav bar
        self._nav = Gtk.Box(spacing=8, margin=6)
        self._back_btn = self._gtk.Button(label="← Back",  style="color2", scale=self.bts)
        self._back_btn.connect("clicked", self._go_back)
        self._next_btn = self._gtk.Button(label="CONFIRM",  style="color1", scale=self.bts)
        self._next_btn.connect("clicked", self._confirm)
        self._nav.pack_start(self._back_btn, True, True, 0)
        self._nav.pack_end(self._next_btn,   True, True, 0)
        self.content.pack_end(self._nav, False, False, 0)

        self._show_step('path')

    # ── Page factories ────────────────────────────────────────────────────────

    def _make_path_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=8)

        hdr = Gtk.Label(label="Select path and operation")
        hdr.set_halign(Gtk.Align.START)
        outer.pack_start(hdr, False, False, 0)

        # Op selector
        op_box = Gtk.Box(spacing=8)
        self._load_btn   = self._gtk.Button(label="LOAD",   style="color1", scale=self.bts)
        self._unload_btn = self._gtk.Button(label="UNLOAD", style="color2", scale=self.bts)
        self._load_btn.connect("clicked",   self._set_op, 'load')
        self._unload_btn.connect("clicked", self._set_op, 'unload')
        op_box.pack_start(self._load_btn,   True, True, 0)
        op_box.pack_start(self._unload_btn, True, True, 0)
        outer.pack_start(op_box, False, False, 0)

        # Path grid
        self._path_grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True,
                                   row_spacing=5, column_spacing=5)
        outer.pack_start(self._path_grid, True, True, 0)

        # Selection status label
        self._path_status = Gtk.Label(label="Select a path above")
        self._path_status.set_halign(Gtk.Align.CENTER)
        outer.pack_start(self._path_status, False, False, 0)

        return {'outer': outer}

    def _make_scroll_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=8)
        hdr = Gtk.Label(label="")
        hdr.set_halign(Gtk.Align.START)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        inner = Gtk.FlowBox()
        inner.set_max_children_per_line(4)
        inner.set_min_children_per_line(2)
        inner.set_selection_mode(Gtk.SelectionMode.NONE)
        inner.set_homogeneous(True)
        scroll.add(inner)
        outer.pack_start(hdr,    False, False, 0)
        outer.pack_start(scroll, True,  True,  0)
        return {'outer': outer, 'hdr': hdr, 'inner': inner}

    # ── Step navigation ───────────────────────────────────────────────────────

    _STEPS = ['path', 'brand', 'material', 'color']

    def _show_step(self, step_name):
        self._cur_step = step_name
        self._stack.set_visible_child_name(step_name)
        idx = self._STEPS.index(step_name)
        self._back_btn.set_sensitive(idx > 0)
        last = (step_name == 'color')
        self._next_btn.set_label("CONFIRM" if last else "Next →")
        self._next_btn.set_sensitive(last and bool(self._wz.get('color_hex')))

    def _go_back(self, widget=None):
        idx = self._STEPS.index(self._cur_step)
        if idx > 0:
            self._show_step(self._STEPS[idx - 1])

    def _set_op(self, widget, op):
        self._op = op
        self._load_btn.get_style_context().remove_class("color1")
        self._load_btn.get_style_context().remove_class("color2")
        self._unload_btn.get_style_context().remove_class("color1")
        self._unload_btn.get_style_context().remove_class("color2")
        if op == 'load':
            self._load_btn.get_style_context().add_class("color1")
            self._unload_btn.get_style_context().add_class("color2")
        else:
            self._load_btn.get_style_context().add_class("color2")
            self._unload_btn.get_style_context().add_class("color1")

    # ── Path page ─────────────────────────────────────────────────────────────

    def _populate_path_page(self):
        for child in self._path_grid.get_children():
            self._path_grid.remove(child)

        num = len(self._path_states)
        for i in range(num):
            state = self._path_states[i] if i < len(self._path_states) else 'unknown'
            dot = {'loaded': '●', 'empty': '○', 'partial': '≈'}.get(state, '?')
            btn = self._gtk.Button(label=f"T{i}\n{dot}", style="color3", scale=self.bts)
            btn.connect("clicked", self._select_path, i)
            self._path_grid.attach(btn, i % 3, i // 3, 1, 1)
        self._path_grid.show_all()

    def _select_path(self, widget, path):
        self._wz['path'] = path
        self._path_status.set_text(f"Selected: T{path} — {self._op.upper()}")
        # Auto-advance to brand (or skip to confirm for unload)
        if self._op == 'unload':
            self._confirm_unload()
        else:
            self._go_to_brand()

    def _go_to_brand(self):
        if _db is None:
            self._screen.show_popup_message("sa_filament_db not available")
            return
        brands = _db.scan_brands(_BRANDS_DIR)
        page = self._pages['brand']
        page['hdr'].set_text(f"T{self._wz['path']} — Select Brand")
        self._fill_flow(page['inner'], [
            (name, self._select_brand, (name, fpath))
            for name, fpath in brands
        ])
        self._show_step('brand')

    def _select_brand(self, widget, args):
        name, fpath = args
        self._wz['brand_name'] = name
        self._wz['brand_path'] = fpath
        brand_data = _db.load_brand(fpath)
        self._wz['brand_data'] = brand_data
        materials = _db.get_materials(brand_data)
        page = self._pages['material']
        page['hdr'].set_text(f"T{self._wz['path']} — {name} — Select Material")
        self._fill_flow(page['inner'], [
            (m, self._select_material, m) for m in materials
        ])
        self._show_step('material')

    def _select_material(self, widget, material):
        self._wz['material'] = material
        lines = _db.get_product_lines(self._wz['brand_data'], material)
        if len(lines) == 1:
            # Only one product line — skip straight to color
            self._select_line(None, lines[0])
            return
        page = self._pages['color']
        page['hdr'].set_text(f"T{self._wz['path']} — Select Product Line")
        self._fill_flow(page['inner'], [
            (f"{pl['display_name']}\n{pl['load_temp']}°C", self._select_line, pl)
            for pl in lines
        ])
        self._show_step('color')
        # Reuse color page temporarily as product line page, then repopulate later
        self._wz['_picking_line'] = True

    def _select_line(self, widget, pl):
        self._wz['line']         = pl.get('line_id', '')
        self._wz['line_name']    = pl.get('display_name', '')
        self._wz['load_temp']    = pl.get('load_temp',    200)
        self._wz['unload_temp']  = pl.get('unload_temp',  185)
        self._wz['purge_speed']  = pl.get('purge_speed',  5)
        self._wz['purge_length'] = pl.get('purge_length', 30)
        self._wz['_picking_line'] = False
        colors = pl.get('colors', [])
        page = self._pages['color']
        page['hdr'].set_text(
            f"T{self._wz['path']} — {pl.get('display_name','')} — Select Color")
        self._fill_flow(page['inner'], [
            (c.get('name','?'), self._select_color, c) for c in colors
        ])
        self._show_step('color')

    def _select_color(self, widget, c):
        self._wz['color_name'] = c.get('name', '')
        self._wz['color_hex']  = c.get('hex', '')
        self._wz['color_id']   = c.get('id', '')
        page = self._pages['color']
        page['hdr'].set_text(
            f"T{self._wz['path']} — Selected: {c.get('name','')}  ({c.get('hex','')})")
        self._next_btn.set_sensitive(True)

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _fill_flow(self, flowbox, items):
        for child in flowbox.get_children():
            flowbox.remove(child)
        for label, callback, arg in items:
            btn = self._gtk.Button(label=label, style="color3", scale=self.bts)
            btn.connect("clicked", callback, arg)
            btn.set_hexpand(True)
            flowbox.add(btn)
        flowbox.show_all()

    def _gcode(self, script):
        self._screen._ws.klippy.gcode_script(script)

    # ── Confirm ───────────────────────────────────────────────────────────────

    def _confirm(self, widget=None):
        wz = self._wz
        path = wz.get('path')
        if path is None:
            return
        if self._op == 'load':
            self._gcode(
                'SA_SET_MATERIAL TOOL={tool} MATERIAL={mat} BRAND="{brand}" '
                'LINE={line} COLOR_NAME="{cname}" COLOR_HEX={chex} '
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
            self._gcode(f"SA_LOAD TOOL={path}")
        self._screen.show_popup_message(
            f"{'Loading' if self._op == 'load' else 'Unloading'} T{path} …")
        self._reset()

    def _confirm_unload(self):
        path = self._wz.get('path')
        if path is None:
            return
        self._gcode(f"SA_UNLOAD TOOL={path}")
        self._screen.show_popup_message(f"Unloading T{path} …")
        self._reset()

    def _reset(self):
        self._wz = {}
        self._show_step('path')

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def activate(self):
        sa = self._printer.data.get("stealth_autoloader", {})
        self._path_states = sa.get("path_states", [])
        self._populate_path_page()
        self._reset()

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("stealth_autoloader")
        if sa is not None:
            self._path_states = sa.get("path_states", self._path_states)
