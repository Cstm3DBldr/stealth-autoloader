# sa_button_style.py — shared button CSS for all Stealth Autoloader panels
#
# Call apply() once per panel __init__ to inject the stylesheet.
# Then use plain Gtk.Button() and add the "sa-btn" CSS class.

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

_CSS = b"""
.sa-btn {
    padding: 4px 8px;
    min-height: 62px;
    min-width: 0px;
    border-radius: 6px;
    background: #1565C0;
    color: white;
}
.sa-btn:hover          { background: #1976D2; }
.sa-btn:active         { background: #0D47A1; }
.sa-btn:disabled       { background: #424242; color: #9E9E9E; }
.sa-btn label          { color: white; }
.sa-btn:disabled label { color: #9E9E9E; }

.sa-btn-alt {
    padding: 4px 8px;
    min-height: 62px;
    min-width: 0px;
    border-radius: 6px;
    background: #37474F;
    color: white;
}
.sa-btn-alt:hover          { background: #455A64; }
.sa-btn-alt:active         { background: #263238; }
.sa-btn-alt:disabled       { background: #424242; color: #9E9E9E; }
.sa-btn-alt label          { color: white; }
.sa-btn-alt:disabled label { color: #9E9E9E; }

.sa-btn-warn {
    padding: 4px 8px;
    min-height: 62px;
    min-width: 0px;
    border-radius: 6px;
    background: #E65100;
    color: white;
}
.sa-btn-warn:hover          { background: #F57C00; }
.sa-btn-warn:active         { background: #BF360C; }
.sa-btn-warn label          { color: white; }

.path-selected {
    border: 3px solid #8BC34A;
}
"""

_applied = False

def apply():
    global _applied
    if _applied:
        return
    css = Gtk.CssProvider()
    css.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), css,
        Gtk.STYLE_PROVIDER_PRIORITY_USER)
    _applied = True


def make(label, style="sa-btn"):
    """Create a plain Gtk.Button with the given sa-btn CSS class."""
    btn = Gtk.Button(label=label)
    btn.get_style_context().add_class(style)
    return btn
