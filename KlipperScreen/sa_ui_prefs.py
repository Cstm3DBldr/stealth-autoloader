# sa_ui_prefs.py — persist UI preferences for Stealth Autoloader KlipperScreen panels
#
# Stores prefs in ~/stealth-autoloader/klipperscreen/sa_ui_prefs.json

import json
import os

_PREFS_PATH = os.path.expanduser(
    "~/stealth-autoloader/klipperscreen/sa_ui_prefs.json")

_DEFAULTS = {
    "accent_color":  "#1565C0",   # button background
    "hover_color":   "#1976D2",
    "active_color":  "#0D47A1",
}

_prefs = None


def load():
    global _prefs
    _prefs = dict(_DEFAULTS)
    try:
        if os.path.isfile(_PREFS_PATH):
            with open(_PREFS_PATH) as f:
                _prefs.update(json.load(f))
    except Exception:
        pass
    return _prefs


def save(updates):
    global _prefs
    if _prefs is None:
        load()
    _prefs.update(updates)
    try:
        os.makedirs(os.path.dirname(_PREFS_PATH), exist_ok=True)
        with open(_PREFS_PATH, 'w') as f:
            json.dump(_prefs, f, indent=2)
    except Exception:
        pass


def get(key, default=None):
    if _prefs is None:
        load()
    return _prefs.get(key, _DEFAULTS.get(key, default))
