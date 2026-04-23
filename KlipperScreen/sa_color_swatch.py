# sa_color_swatch.py — Cairo swatch drawing for single/dual/tri/gradient filament colors
import math
import cairo
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


def _hex_to_rgb(hex_c):
    h = (hex_c or '808080').lstrip('#')
    if len(h) == 3:
        h = ''.join(c*2 for c in h)
    if len(h) != 6:
        return (0.5, 0.5, 0.5)
    return (int(h[0:2],16)/255.0, int(h[2:4],16)/255.0, int(h[4:6],16)/255.0)


def draw_swatch(cr, cx, cy, radius, hex_list, color_type='single'):
    """Draw a circular color swatch at (cx,cy) with given radius into a Cairo context."""
    if not hex_list:
        hex_list = ['#808080']
    ct = (color_type or 'single').lower()
    if ct == 'gradient' and len(hex_list) >= 2:
        _draw_gradient(cr, cx, cy, radius, hex_list[0], hex_list[1])
    elif ct == 'dual' and len(hex_list) >= 2:
        _draw_dual(cr, cx, cy, radius, hex_list[0], hex_list[1])
    elif ct == 'tri' and len(hex_list) >= 3:
        _draw_tri(cr, cx, cy, radius, hex_list[0], hex_list[1], hex_list[2])
    else:
        r, g, b = _hex_to_rgb(hex_list[0])
        cr.set_source_rgb(r, g, b)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()
    # Subtle outline
    cr.set_source_rgba(0, 0, 0, 0.35)
    cr.arc(cx, cy, radius, 0, 2 * math.pi)
    cr.set_line_width(1.5)
    cr.stroke()


def draw_state_swatch(cr, cx, cy, radius, state):
    """Draw a state-indicator circle for paths with no color assigned."""
    # Background
    cr.set_source_rgb(0.22, 0.22, 0.22)
    cr.arc(cx, cy, radius, 0, 2 * math.pi)
    cr.fill()
    # State mark
    cr.set_line_width(max(2.0, radius * 0.12))
    if state == 'empty':
        cr.set_source_rgb(0.45, 0.45, 0.45)
        cr.arc(cx, cy, radius * 0.55, 0, 2 * math.pi)
        cr.stroke()
        off = radius * 0.38
        cr.move_to(cx - off, cy - off)
        cr.line_to(cx + off, cy + off)
        cr.stroke()
    elif state == 'partial':
        cr.set_source_rgb(0.9, 0.4, 0.0)
        cr.move_to(cx, cy)
        cr.arc(cx, cy, radius * 0.65, -math.pi/2, math.pi/2)
        cr.close_path()
        cr.fill()
    elif state == 'loaded_no_color':
        cr.set_source_rgb(0.55, 0.55, 0.55)
        cr.arc(cx, cy, radius * 0.55, 0, 2 * math.pi)
        cr.fill()
    else:  # unknown
        cr.set_source_rgb(0.98, 0.66, 0.15)
        cr.arc(cx, cy, radius * 0.55, 0, 2 * math.pi)
        cr.fill()
    # Outline
    cr.set_source_rgba(0, 0, 0, 0.35)
    cr.arc(cx, cy, radius, 0, 2 * math.pi)
    cr.set_line_width(1.5)
    cr.stroke()


def _draw_dual(cr, cx, cy, radius, hex1, hex2):
    """Left-half = hex1, right-half = hex2  (clean vertical split via clip+rect)."""
    r1, g1, b1 = _hex_to_rgb(hex1)
    r2, g2, b2 = _hex_to_rgb(hex2)
    cr.save()
    cr.arc(cx, cy, radius, 0, 2 * math.pi)
    cr.clip()
    # Left half
    cr.set_source_rgb(r1, g1, b1)
    cr.rectangle(cx - radius, cy - radius, radius, radius * 2)
    cr.fill()
    # Right half
    cr.set_source_rgb(r2, g2, b2)
    cr.rectangle(cx, cy - radius, radius, radius * 2)
    cr.fill()
    # Thin dividing line at center
    cr.set_source_rgba(0, 0, 0, 0.25)
    cr.set_line_width(1.0)
    cr.move_to(cx, cy - radius)
    cr.line_to(cx, cy + radius)
    cr.stroke()
    cr.restore()


def _draw_tri(cr, cx, cy, radius, hex1, hex2, hex3):
    """Three equal 120° pie sectors starting from top (12 o'clock)."""
    colors = [hex1, hex2, hex3]
    step  = 2 * math.pi / 3
    start = -math.pi / 2
    cr.save()
    cr.arc(cx, cy, radius, 0, 2 * math.pi)
    cr.clip()
    for i, hx in enumerate(colors):
        r, g, b = _hex_to_rgb(hx)
        cr.set_source_rgb(r, g, b)
        cr.move_to(cx, cy)
        a1 = start + i * step
        a2 = start + (i + 1) * step
        cr.arc(cx, cy, radius, a1, a2)
        cr.close_path()
        cr.fill()
    # Sector dividers
    cr.set_source_rgba(0, 0, 0, 0.25)
    cr.set_line_width(1.0)
    for i in range(3):
        a = start + i * step
        cr.move_to(cx, cy)
        cr.line_to(cx + radius * math.cos(a), cy + radius * math.sin(a))
        cr.stroke()
    cr.restore()


def _draw_gradient(cr, cx, cy, radius, hex_start, hex_end):
    r1, g1, b1 = _hex_to_rgb(hex_start)
    r2, g2, b2 = _hex_to_rgb(hex_end)
    cr.save()
    cr.arc(cx, cy, radius, 0, 2 * math.pi)
    cr.clip()
    pat = cairo.LinearGradient(cx - radius, cy, cx + radius, cy)
    pat.add_color_stop_rgb(0.0, r1, g1, b1)
    pat.add_color_stop_rgb(1.0, r2, g2, b2)
    cr.set_source(pat)
    cr.paint()
    cr.restore()


def make_swatch_da(size, hex_list, color_type='single'):
    """Return a Gtk.DrawingArea that renders a circular swatch."""
    da = Gtk.DrawingArea()
    da.set_size_request(size, size)
    def _draw(widget, cr, _hl=hex_list, _ct=color_type):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        r = min(w, h) / 2.0 - 1.5
        draw_swatch(cr, w/2.0, h/2.0, r, _hl, _ct)
        return False
    da.connect("draw", _draw)
    return da


def make_state_da(size, state):
    """Return a Gtk.DrawingArea showing a state indicator (no color)."""
    da = Gtk.DrawingArea()
    da.set_size_request(size, size)
    def _draw(widget, cr, _s=state):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        r = min(w, h) / 2.0 - 1.5
        draw_state_swatch(cr, w/2.0, h/2.0, r, _s)
        return False
    da.connect("draw", _draw)
    return da
