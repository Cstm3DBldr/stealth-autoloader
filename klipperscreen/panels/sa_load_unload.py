# sa_load_unload.py — Stealth Autoloader KlipperScreen load/unload wizard
#
# 6-step Gtk.Stack wizard:
#   Step 1: Select path + operation (Load / Unload)
#   Step 2: Select brand
#   Step 3: Select material type
#   Step 4: Select product line
#   Step 5: Select color (horizontal scroll swatch wheel)
#   Step 6: Progress display
#
# Brand .cfg files are auto-discovered from filaments/brands/ at runtime.

import os
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import logging

logger = logging.getLogger('klipperscreen.sa_load_unload')

# Adjust this path to match the actual install location on the printer
_BRANDS_DIR = os.path.expanduser(
    "~/stealth-autoloader/filaments/brands")

# Lazy import — sa_filament_db.py sits one level up from panels/
import sys as _sys, os as _os
_db_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _db_dir not in _sys.path:
    _sys.path.insert(0, _db_dir)

try:
    import sa_filament_db as _db
except ImportError:
    _db = None
    logger.error("sa_load_unload: could not import sa_filament_db")

_STATE_STYLE = {
    'loaded':  ('●', '#388E3C'),
    'empty':   ('○', '#616161'),
    'partial': ('≈', '#E65100'),
    'unknown': ('?', '#F9A825'),
}


class Panel:
    """KlipperScreen load/unload wizard panel for Stealth Autoloader."""

    def __init__(self, screen, title):
        self._screen = screen
        self._gtk    = screen.gtk
        self._title  = title

        self._wizard = {
            'path': 0, 'operation': 'load',
            'brand_file': '', 'brand_data': None,
            'material': '', 'line_id': '',
            'color_name': '', 'color_hex': '',
            'load_temp': 200., 'unload_temp': 185.,
            'purge_speed': 5., 'purge_length': 30.,
        }

        self._path_states    = ['unknown'] * 6
        self._progress_timer = None

        self._stack   = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        self._stack.set_transition_duration(200)

        self._pages   = {}
        self._build_path_op_page()
        self._build_brand_page()
        self._build_material_page()
        self._build_product_line_page()
        self._build_color_page()
        self._build_progress_page()

        self.content = self._stack

    # ══════════════════════════════════════════════════════════════════════════
    # Step 1 — Path + Operation
    # ══════════════════════════════════════════════════════════════════════════

    def _build_path_op_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)

        lbl = Gtk.Label(label="Select Path and Operation")
        lbl.get_style_context().add_class('title_2')
        lbl.set_halign(Gtk.Align.START)
        box.pack_start(lbl, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

        # Path grid (2×3)
        path_grid = Gtk.Grid()
        path_grid.set_column_spacing(8)
        path_grid.set_row_spacing(8)
        path_grid.set_homogeneous(True)

        self._path_buttons = []
        for i in range(6):
            btn = Gtk.ToggleButton()
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            t_lbl = Gtk.Label(label="T%d" % i)
            t_lbl.get_style_context().add_class('title_2')
            s_lbl = Gtk.Label(label="?")
            inner.pack_start(t_lbl, False, False, 0)
            inner.pack_start(s_lbl, False, False, 0)
            btn.add(inner)
            btn.connect("toggled", self._on_path_toggled, i)
            btn.set_size_request(80, 70)
            path_grid.attach(btn, i % 3, i // 3, 1, 1)
            self._path_buttons.append((btn, s_lbl))

        body.pack_start(path_grid, True, True, 0)

        # Operation buttons
        op_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        btn_load = Gtk.Button(label="▶  LOAD FILAMENT")
        btn_load.set_size_request(200, 60)
        btn_load.connect("clicked", self._on_op_selected, 'load')
        op_box.pack_start(btn_load, False, False, 0)

        btn_unload = Gtk.Button(label="◀  UNLOAD FILAMENT")
        btn_unload.set_size_request(200, 60)
        btn_unload.connect("clicked", self._on_op_selected, 'unload')
        op_box.pack_start(btn_unload, False, False, 0)

        self._op_status_lbl = Gtk.Label(label="Select a path above")
        self._op_status_lbl.set_halign(Gtk.Align.START)
        op_box.pack_start(self._op_status_lbl, False, False, 0)

        body.pack_start(op_box, False, False, 0)
        box.pack_start(body, True, True, 0)

        # Select T0 by default
        self._path_buttons[0][0].set_active(True)

        self._stack.add_named(box, 'path_op')
        self._pages['path_op'] = box

    def _on_path_toggled(self, btn, path):
        if not btn.get_active():
            return
        # Untoggle all others
        for i, (b, _) in enumerate(self._path_buttons):
            if i != path and b.get_active():
                b.handler_block_by_func(self._on_path_toggled)
                b.set_active(False)
                b.handler_unblock_by_func(self._on_path_toggled)
        self._wizard['path'] = path
        state = self._path_states[path] if path < len(self._path_states) else 'unknown'
        sym, _ = _STATE_STYLE.get(state, ('?', '#F9A825'))
        self._op_status_lbl.set_text("Selected: T%d  %s %s" % (path, sym, state.upper()))

    def _on_op_selected(self, btn, op):
        self._wizard['operation'] = op
        if op == 'unload':
            self._execute_unload()
        else:
            self._go_to('brand')

    # ══════════════════════════════════════════════════════════════════════════
    # Step 2 — Brand
    # ══════════════════════════════════════════════════════════════════════════

    def _build_brand_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)

        lbl = Gtk.Label(label="Select Brand")
        lbl.get_style_context().add_class('title_2')
        lbl.set_halign(Gtk.Align.START)
        box.pack_start(lbl, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._brand_flow = Gtk.FlowBox()
        self._brand_flow.set_max_children_per_line(4)
        self._brand_flow.set_min_children_per_line(2)
        self._brand_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._brand_flow.set_column_spacing(8)
        self._brand_flow.set_row_spacing(8)
        scroll.add(self._brand_flow)
        box.pack_start(scroll, True, True, 0)

        nav = self._make_nav_bar(back_page='path_op', next_cb=None, next_label=None)
        box.pack_end(nav, False, False, 0)

        self._stack.add_named(box, 'brand')
        self._pages['brand'] = box

    def _populate_brands(self):
        for child in self._brand_flow.get_children():
            self._brand_flow.remove(child)

        if _db is None:
            return

        brands = _db.scan_brands(_BRANDS_DIR)
        for display_name, filepath in brands:
            btn = Gtk.Button(label=display_name)
            btn.set_size_request(140, 50)
            btn.connect("clicked", self._on_brand_selected, filepath)
            self._brand_flow.add(btn)

        self._brand_flow.show_all()

    def _on_brand_selected(self, btn, filepath):
        if _db is None:
            return
        self._wizard['brand_file'] = filepath
        self._wizard['brand_data'] = _db.load_brand(filepath)
        self._wizard['material']   = ''
        self._wizard['line_id']    = ''
        self._populate_materials()
        self._go_to('material')

    # ══════════════════════════════════════════════════════════════════════════
    # Step 3 — Material type
    # ══════════════════════════════════════════════════════════════════════════

    def _build_material_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)

        lbl = Gtk.Label(label="Select Material Type")
        lbl.get_style_context().add_class('title_2')
        lbl.set_halign(Gtk.Align.START)
        box.pack_start(lbl, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._material_flow = Gtk.FlowBox()
        self._material_flow.set_max_children_per_line(4)
        self._material_flow.set_min_children_per_line(2)
        self._material_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._material_flow.set_column_spacing(8)
        self._material_flow.set_row_spacing(8)
        scroll.add(self._material_flow)
        box.pack_start(scroll, True, True, 0)

        nav = self._make_nav_bar(back_page='brand', next_cb=None, next_label=None)
        box.pack_end(nav, False, False, 0)

        self._stack.add_named(box, 'material')
        self._pages['material'] = box

    def _populate_materials(self):
        for child in self._material_flow.get_children():
            self._material_flow.remove(child)
        if not self._wizard.get('brand_data'):
            return
        for mat in _db.get_materials(self._wizard['brand_data']):
            btn = Gtk.Button(label=mat)
            btn.set_size_request(120, 50)
            btn.connect("clicked", self._on_material_selected, mat)
            self._material_flow.add(btn)
        self._material_flow.show_all()

    def _on_material_selected(self, btn, material):
        self._wizard['material'] = material
        self._wizard['line_id']  = ''
        self._populate_product_lines()
        self._go_to('product_line')

    # ══════════════════════════════════════════════════════════════════════════
    # Step 4 — Product line
    # ══════════════════════════════════════════════════════════════════════════

    def _build_product_line_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)

        lbl = Gtk.Label(label="Select Product Line")
        lbl.get_style_context().add_class('title_2')
        lbl.set_halign(Gtk.Align.START)
        box.pack_start(lbl, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._pl_flow = Gtk.FlowBox()
        self._pl_flow.set_max_children_per_line(2)
        self._pl_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._pl_flow.set_column_spacing(8)
        self._pl_flow.set_row_spacing(8)
        scroll.add(self._pl_flow)
        box.pack_start(scroll, True, True, 0)

        nav = self._make_nav_bar(back_page='material', next_cb=None, next_label=None)
        box.pack_end(nav, False, False, 0)

        self._stack.add_named(box, 'product_line')
        self._pages['product_line'] = box

    def _populate_product_lines(self):
        for child in self._pl_flow.get_children():
            self._pl_flow.remove(child)
        if not self._wizard.get('brand_data'):
            return
        lines = _db.get_product_lines(
            self._wizard['brand_data'], self._wizard.get('material'))
        for line_id, pl in lines:
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            name_lbl = Gtk.Label(label=pl['display_name'])
            name_lbl.set_line_wrap(True)
            name_lbl.set_justify(Gtk.Justification.CENTER)
            desc_lbl = Gtk.Label(label=pl.get('description', '')[:50])
            desc_lbl.set_line_wrap(True)
            desc_lbl.get_style_context().add_class('small_label')
            temp_lbl = Gtk.Label(
                label="Load: %d°C  Bed: %d°C" % (pl['load_temp'], pl['bed_temp']))
            temp_lbl.get_style_context().add_class('small_label')
            inner.pack_start(name_lbl, False, False, 0)
            inner.pack_start(desc_lbl, False, False, 0)
            inner.pack_start(temp_lbl, False, False, 0)
            btn = Gtk.Button()
            btn.add(inner)
            btn.set_size_request(240, 80)
            btn.connect("clicked", self._on_pl_selected, line_id, pl)
            self._pl_flow.add(btn)
        self._pl_flow.show_all()

    def _on_pl_selected(self, btn, line_id, pl):
        self._wizard['line_id']      = line_id
        self._wizard['load_temp']    = pl['load_temp']
        self._wizard['unload_temp']  = pl['unload_temp']
        self._wizard['purge_speed']  = pl['purge_speed']
        self._wizard['purge_length'] = pl['purge_length']
        self._wizard['color_name']   = ''
        self._wizard['color_hex']    = ''
        self._populate_colors()
        self._go_to('color')

    # ══════════════════════════════════════════════════════════════════════════
    # Step 5 — Color scroll wheel
    # ══════════════════════════════════════════════════════════════════════════

    def _build_color_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)

        lbl = Gtk.Label(label="Select Color")
        lbl.get_style_context().add_class('title_2')
        lbl.set_halign(Gtk.Align.START)
        box.pack_start(lbl, False, False, 0)

        # Horizontal scroll of swatches
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroll.set_size_request(-1, 90)
        self._color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._color_box.set_margin_top(4)
        self._color_box.set_margin_bottom(4)
        scroll.add(self._color_box)
        box.pack_start(scroll, False, False, 0)

        # Color info
        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._color_swatch_big = Gtk.DrawingArea()
        self._color_swatch_big.set_size_request(40, 40)
        self._color_swatch_big.connect("draw", self._draw_swatch_big)
        info_box.pack_start(self._color_swatch_big, False, False, 0)

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._color_name_lbl = Gtk.Label(label="---")
        self._color_name_lbl.set_halign(Gtk.Align.START)
        self._color_name_lbl.get_style_context().add_class('title_2')
        self._color_detail_lbl = Gtk.Label(label="")
        self._color_detail_lbl.set_halign(Gtk.Align.START)
        details.pack_start(self._color_name_lbl,   False, False, 0)
        details.pack_start(self._color_detail_lbl, False, False, 0)
        info_box.pack_start(details, True, True, 0)
        box.pack_start(info_box, False, False, 4)

        nav = self._make_nav_bar(
            back_page='product_line',
            next_cb=self._on_color_confirmed,
            next_label="CONFIRM LOAD →")
        box.pack_end(nav, False, False, 0)

        self._stack.add_named(box, 'color')
        self._pages['color'] = box
        self._selected_color_btn = None

    def _populate_colors(self):
        for child in self._color_box.get_children():
            self._color_box.remove(child)
        if not self._wizard.get('brand_data') or not self._wizard.get('line_id'):
            return
        colors = _db.get_colors(self._wizard['brand_data'], self._wizard['line_id'])
        first_btn = None
        for c in colors:
            btn = Gtk.Button()
            da  = Gtk.DrawingArea()
            da.set_size_request(60, 60)
            hex_val = c['hex']
            da.connect("draw", self._draw_swatch_color, hex_val)
            name_lbl = Gtk.Label(label=c['name'][:8])
            name_lbl.get_style_context().add_class('small_label')
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            vbox.pack_start(da, False, False, 0)
            vbox.pack_start(name_lbl, False, False, 0)
            btn.add(vbox)
            btn.connect("clicked", self._on_color_clicked, c)
            self._color_box.pack_start(btn, False, False, 0)
            if first_btn is None:
                first_btn = (btn, c)
        self._color_box.show_all()
        if first_btn:
            self._on_color_clicked(first_btn[0], first_btn[1])

    def _draw_swatch_color(self, widget, cr, hex_val):
        try:
            rgba = Gdk.RGBA()
            rgba.parse(hex_val)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 1.0)
        except Exception:
            cr.set_source_rgb(0.5, 0.5, 0.5)
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        cr.arc(w / 2, h / 2, min(w, h) / 2 - 2, 0, 6.2832)
        cr.fill()

    def _draw_swatch_big(self, widget, cr):
        self._draw_swatch_color(widget, cr, self._wizard.get('color_hex', '#808080'))

    def _on_color_clicked(self, btn, color):
        self._wizard['color_name'] = color['name']
        self._wizard['color_hex']  = color['hex']
        if self._selected_color_btn:
            self._selected_color_btn.get_style_context().remove_class('button_active')
        btn.get_style_context().add_class('button_active')
        self._selected_color_btn = btn
        self._color_name_lbl.set_text(color['name'])
        bd  = self._wizard.get('brand_data', {})
        bdn = bd.get('display_name', '') if bd else ''
        pl  = bd.get('product_lines', {}).get(self._wizard.get('line_id', ''), {}) if bd else {}
        pln = pl.get('display_name', '') if pl else ''
        self._color_detail_lbl.set_text(
            "%s  ·  %s  ·  Load: %.0f°C  Purge: %.0fmm/s"
            % (color['hex'], pln, self._wizard['load_temp'], self._wizard['purge_speed']))
        self._color_swatch_big.queue_draw()

    def _on_color_confirmed(self, btn):
        if not self._wizard.get('color_hex'):
            return
        self._go_to('progress')
        self._execute_load()

    # ══════════════════════════════════════════════════════════════════════════
    # Step 6 — Progress
    # ══════════════════════════════════════════════════════════════════════════

    def _build_progress_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)

        self._prog_title = Gtk.Label(label="Loading T0...")
        self._prog_title.get_style_context().add_class('title_2')
        self._prog_title.set_halign(Gtk.Align.START)
        box.pack_start(self._prog_title, False, False, 0)

        self._prog_detail = Gtk.Label(label="")
        self._prog_detail.set_halign(Gtk.Align.START)
        box.pack_start(self._prog_detail, False, False, 0)

        self._prog_bar = Gtk.ProgressBar()
        self._prog_bar.set_pulse_step(0.05)
        box.pack_start(self._prog_bar, False, False, 4)

        self._prog_status = Gtk.Label(label="Waiting...")
        self._prog_status.set_halign(Gtk.Align.START)
        box.pack_start(self._prog_status, False, False, 0)

        sensor_grid = Gtk.Grid()
        sensor_grid.set_column_spacing(16)
        for col, lbl in enumerate(['Entry', 'Toolhead', 'Extruder']):
            h = Gtk.Label(label=lbl)
            h.get_style_context().add_class('color4')
            sensor_grid.attach(h, col, 0, 1, 1)
        self._prog_entry_lbl   = Gtk.Label(label="○")
        self._prog_th_lbl      = Gtk.Label(label="○")
        self._prog_ext_lbl     = Gtk.Label(label="○")
        sensor_grid.attach(self._prog_entry_lbl, 0, 1, 1, 1)
        sensor_grid.attach(self._prog_th_lbl,    1, 1, 1, 1)
        sensor_grid.attach(self._prog_ext_lbl,   2, 1, 1, 1)
        box.pack_start(sensor_grid, False, False, 4)

        btn_cancel = Gtk.Button(label="CANCEL")
        btn_cancel.connect("clicked", self._on_cancel)
        box.pack_end(btn_cancel, False, False, 0)

        btn_done = Gtk.Button(label="← BACK TO STATUS")
        btn_done.connect("clicked", lambda b: self._go_to('path_op'))
        box.pack_end(btn_done, False, False, 0)

        self._stack.add_named(box, 'progress')
        self._pages['progress'] = box

    def _execute_load(self):
        wz   = self._wizard
        path = wz['path']
        bd   = wz.get('brand_data', {}) or {}

        op_title = "Loading T%d — %s %s" % (
            path, bd.get('display_name', ''), wz.get('color_name', ''))
        self._prog_title.set_text(op_title)
        self._prog_detail.set_text(
            "Load: %.0f°C  Purge: %.0fmm @ %.0fmm/s"
            % (wz['load_temp'], wz['purge_length'], wz['purge_speed']))
        self._prog_bar.set_fraction(0.0)
        self._prog_status.set_text("Sending profile to printer...")

        # 1. Store material profile
        brand_name = bd.get('brand_name', bd.get('display_name', ''))
        self._send(
            "SA_SET_MATERIAL TOOL=%d MATERIAL=%s BRAND=%s LINE=%s "
            "COLOR_NAME=\"%s\" COLOR_HEX=%s "
            "LOAD_TEMP=%.0f UNLOAD_TEMP=%.0f "
            "PURGE_SPEED=%.1f PURGE_LENGTH=%.0f"
            % (path,
               wz['material'], brand_name, wz['line_id'],
               wz['color_name'], wz['color_hex'],
               wz['load_temp'], wz['unload_temp'],
               wz['purge_speed'], wz['purge_length']))

        # 2. Start load sequence
        self._send("SA_LOAD TOOL=%d" % path)
        self._prog_status.set_text("Load sequence running...")
        self._start_progress_poll()

    def _execute_unload(self):
        path = self._wizard['path']
        self._go_to('progress')
        self._prog_title.set_text("Unloading T%d..." % path)
        self._prog_detail.set_text("")
        self._prog_bar.set_fraction(0.0)
        self._prog_status.set_text("Unload sequence running...")
        self._send("SA_UNLOAD TOOL=%d" % path)
        self._start_progress_poll()

    def _start_progress_poll(self):
        if self._progress_timer:
            GLib.source_remove(self._progress_timer)
        self._progress_timer = GLib.timeout_add(500, self._poll_progress)

    def _poll_progress(self):
        self._prog_bar.pulse()
        try:
            sa = self._screen.printer.get_stat('stealth_autoloader')
            if not sa:
                return True
            path   = self._wizard['path']
            states = sa.get('path_states', [])
            state  = states[path] if path < len(states) else 'unknown'

            entry = sa.get('entry_filament',    [])[path] if path < len(sa.get('entry_filament', [])) else False
            th    = sa.get('toolhead_filament', [])[path] if path < len(sa.get('toolhead_filament', [])) else False
            ex    = sa.get('extruder_filament', [])[path] if path < len(sa.get('extruder_filament', [])) else False

            def _dot(v):
                return '<span foreground="%s">%s</span>' % (
                    ('#66BB6A' if v else '#616161'), ('●' if v else '○'))

            self._prog_entry_lbl.set_markup(_dot(entry))
            self._prog_th_lbl.set_markup(_dot(th))
            self._prog_ext_lbl.set_markup(_dot(ex))

            op = self._wizard['operation']
            done = (state == 'loaded' and op == 'load') or (state == 'empty' and op == 'unload')
            if done:
                self._prog_bar.set_fraction(1.0)
                self._prog_status.set_text("Complete! T%d is now %s." % (path, state.upper()))
                return False
        except Exception as e:
            logger.warning("sa_load_unload poll: %s", e)
        return True

    def _on_cancel(self, btn):
        if self._progress_timer:
            GLib.source_remove(self._progress_timer)
            self._progress_timer = None
        self._send("CANCEL_PRINT")
        self._go_to('path_op')

    # ══════════════════════════════════════════════════════════════════════════
    # Navigation helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _make_nav_bar(self, back_page, next_cb, next_label):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_back = Gtk.Button(label="← BACK")
        btn_back.connect("clicked", lambda b: self._go_to(back_page))
        bar.pack_start(btn_back, False, False, 0)
        if next_cb and next_label:
            btn_next = Gtk.Button(label=next_label)
            btn_next.connect("clicked", next_cb)
            bar.pack_end(btn_next, False, False, 0)
        return bar

    def _go_to(self, page_name):
        if page_name == 'brand':
            self._populate_brands()
        self._stack.set_visible_child_name(page_name)

    def _send(self, cmd):
        self._screen._ws.klippy.gcode_script(cmd)

    # ══════════════════════════════════════════════════════════════════════════
    # KlipperScreen callbacks
    # ══════════════════════════════════════════════════════════════════════════

    def activate(self):
        pass

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("stealth_autoloader")
        if not sa:
            return
        GLib.idle_add(self._update_path_states, sa)

    def _update_path_states(self, sa):
        states = sa.get('path_states', [])
        self._path_states = list(states)
        for i, (btn, s_lbl) in enumerate(self._path_buttons):
            state = states[i] if i < len(states) else 'unknown'
            sym, color = _STATE_STYLE.get(state, ('?', '#F9A825'))
            s_lbl.set_markup('<span foreground="%s">%s</span>' % (color, sym))
        return False

    @staticmethod
    def _draw_swatch_static(widget, cr, hex_val):
        try:
            rgba = Gdk.RGBA()
            rgba.parse(hex_val)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 1.0)
        except Exception:
            cr.set_source_rgb(0.5, 0.5, 0.5)
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        cr.arc(w / 2, h / 2, min(w, h) / 2 - 1, 0, 6.2832)
        cr.fill()


def create_panel(*args):
    return Panel(*args)
