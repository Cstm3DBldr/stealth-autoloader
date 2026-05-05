# sa_button_style.py — shared button CSS for all Autoloader panels

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sa_ui_prefs as _prefs

_provider = None


def _build_css(accent, hover, active):
    return ("""
.sa-btn {{
    padding: 4px 8px;
    min-height: 62px;
    min-width: 0px;
    border-radius: 6px;
    background: {accent};
    color: white;
}}
.sa-btn:hover          {{ background: {hover}; }}
.sa-btn:active         {{ background: {active}; }}
.sa-btn:disabled       {{ background: #424242; color: #9E9E9E; }}
.sa-btn label          {{ color: white; }}
.sa-btn:disabled label {{ color: #9E9E9E; }}

.sa-btn-alt {{
    padding: 4px 8px;
    min-height: 62px;
    min-width: 0px;
    border-radius: 6px;
    background: #37474F;
    color: white;
}}
.sa-btn-alt:hover          {{ background: #455A64; }}
.sa-btn-alt:active         {{ background: #263238; }}
.sa-btn-alt:disabled       {{ background: #424242; color: #9E9E9E; }}
.sa-btn-alt label          {{ color: white; }}
.sa-btn-alt:disabled label {{ color: #9E9E9E; }}

.sa-btn-warn {{
    padding: 4px 8px;
    min-height: 62px;
    min-width: 0px;
    border-radius: 6px;
    background: #E65100;
    color: white;
}}
.sa-btn-warn:hover  {{ background: #F57C00; }}
.sa-btn-warn:active {{ background: #BF360C; }}
.sa-btn-warn label  {{ color: white; }}

.sa-btn-nav {{
    padding: 2px 8px;
    min-height: 42px;
    min-width: 0px;
    border-radius: 6px;
    background: #37474F;
    color: white;
}}
.sa-btn-nav:hover          {{ background: #455A64; }}
.sa-btn-nav:active         {{ background: #263238; }}
.sa-btn-nav:disabled       {{ background: #424242; color: #9E9E9E; }}
.sa-btn-nav label          {{ color: white; }}
.sa-btn-nav:disabled label {{ color: #9E9E9E; }}

.path-selected {{ border: 3px solid #8BC34A; }}
""".format(accent=accent, hover=hover, active=active)).encode()


def apply():
    global _provider
    p = _prefs.load()
    css = Gtk.CssProvider()
    css.load_from_data(_build_css(
        p.get("accent_color",  "#1565C0"),
        p.get("hover_color",   "#1976D2"),
        p.get("active_color",  "#0D47A1"),
    ))
    screen = Gdk.Screen.get_default()
    if _provider is not None:
        Gtk.StyleContext.remove_provider_for_screen(screen, _provider)
    Gtk.StyleContext.add_provider_for_screen(
        screen, css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    _provider = css


def reapply(accent, hover=None, active=None):
    """Change accent color, derive hover/active if not given, persist and reapply."""
    # Simple brightness shift for hover/active if not explicit
    if hover is None:
        hover = _lighten(accent, 0.1)
    if active is None:
        active = _darken(accent, 0.15)
    _prefs.save({
        "accent_color": accent,
        "hover_color":  hover,
        "active_color": active,
    })
    apply()


def make(label, style="sa-btn"):
    btn = Gtk.Button(label=label)
    btn.get_style_context().add_class(style)
    return btn


def _lighten(hex_c, amt):
    r, g, b = _parse(hex_c)
    r = min(255, int(r + 255 * amt))
    g = min(255, int(g + 255 * amt))
    b = min(255, int(b + 255 * amt))
    return "#{:02X}{:02X}{:02X}".format(r, g, b)


def _darken(hex_c, amt):
    r, g, b = _parse(hex_c)
    r = max(0, int(r - 255 * amt))
    g = max(0, int(g - 255 * amt))
    b = max(0, int(b - 255 * amt))
    return "#{:02X}{:02X}{:02X}".format(r, g, b)


def _parse(hex_c):
    h = hex_c.lstrip('#')
    if len(h) == 3:
        h = ''.join(c*2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ── Progress dots ────────────────────────────────────────────────────────────
# Shared horizontal dot strip used by sa_calibration_guide (and any future
# multi-step wizard). Returns a Pango markup string suitable for
# Gtk.Label.set_markup().
#
# step_idx : 0-based current step
# total    : total step count
# name     : optional human-readable step name appended to the right
#
# Colors:  done = green     ●
#          current = blue   ●  (bold)
#          upcoming = grey  ○

_DONE   = "#388E3C"
_NOW    = "#1565C0"
_UPCOME = "#424242"
_LINE   = "#424242"


def progress_dots(step_idx, total, name=None):
    # Dots are deliberately at "medium" size (was "x-large" — that
    # combined with the step name pushed the nav-bar natural width past
    # the 720 px content area on a 480 px display, which cascaded into
    # other widgets in the same wrapper Box being clipped on the right
    # edge). The page already has a visible step header, so the name
    # arg is now ignored unless the caller really wants it appended.
    parts = []
    for i in range(total):
        if i < step_idx:
            parts.append(
                '<span foreground="%s" font_size="medium">●</span>' % _DONE)
        elif i == step_idx:
            parts.append(
                '<span foreground="%s" font_size="medium" weight="bold">●</span>'
                % _NOW)
        else:
            parts.append(
                '<span foreground="%s" font_size="medium">○</span>' % _UPCOME)
    sep = '<span foreground="%s">━</span>' % _LINE
    dots = sep.join(parts)
    suffix = "Step %d of %d" % (step_idx + 1, total)
    return '%s   <span font_size="small">%s</span>' % (dots, suffix)

