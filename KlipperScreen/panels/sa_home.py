import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ks_includes.screen_panel import ScreenPanel

import sa_subscription as _sasub

logger = logging.getLogger('klipperscreen.sa_home')


def _effective_state(i, states, entry, toolhead, extruder):
    """Combine stored path state with sensor readings to determine
    actual filament state. Mirrors sa_main._effective_state so both
    panels report the same loaded count.

    Without this, sa_home shows a "0/6 paths loaded" preview while
    the actual SA Status panel shows "4 loaded" — because sa_home
    was reading only the backend-stored `path_states` list, which
    isn't always set to "loaded" (e.g. if the path was filled by
    manual feed instead of SA_LOAD, or after a Klipper restart).
    The three sensors are ground truth for what's physically there.
    """
    e  = entry[i]    if i < len(entry)    else None
    th = toolhead[i] if i < len(toolhead) else None
    ex = extruder[i] if i < len(extruder) else None
    if e is None:
        return states[i] if i < len(states) else 'unknown'
    if not e and not th and not ex:
        return 'empty'
    if e and th and ex:
        return 'loaded'
    if e or th or ex:
        return 'partial'
    return states[i] if i < len(states) else 'unknown'


# Concept B layout — hero row + utility row.
#   Top  (~65% h): STATUS and LOAD/UNLOAD as large hero tiles with a live
#                   status preview line below the label.
#   Bot  (~35% h): MACROS / CALIBRATION / SETTINGS / CONFIG — compact
#                   buttons in one row, all equal width.

_HERO = [
    # (label,           panel,                  panel_title,         color,    icon)
    ("STATUS",         "sa_main",              "SA Status",         "color1", "spoolman"),
    ("LOAD / UNLOAD",  "sa_load_unload",       "Load / Unload",     "color3", "load"),
]

_UTIL = [
    ("MACROS",         "sa_macros",            "SA Macros",         "color2", "move"),
    ("CALIBRATION",    "sa_calibration_guide", "SA Calibration",    "color1", "settings"),
    ("SETTINGS",       "sa_settings",          "SA Settings",       "color3", "settings"),
    ("CONFIG",         "sa_config",            "SA Config",         "color2", "fine_tune"),
]

_UTIL_ROW_PX = 110   # fixed height for the utility row


class Panel(ScreenPanel):
    """Autoloader home — hero row + utility row with live preview lines."""

    def __init__(self, screen, title):
        super().__init__(screen, title or "Autoloader")

        # Preview label widgets — populated by _update_previews().
        self._status_preview = None
        self._load_preview   = None
        self._last_sa        = {}

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        spacing=8, margin=8)
        outer.set_homogeneous(False)

        # ── Hero row (vexpand absorbs leftover height) ────────────────────────
        hero_row = Gtk.Box(spacing=8)
        hero_row.set_hexpand(True)
        hero_row.set_vexpand(True)

        for slot, (label, panel, ptitle, color, icon) in zip(("status", "load"), _HERO):
            btn, preview = self._build_hero_btn(icon, label, color)
            btn.connect("clicked", self._open_panel, panel, ptitle)
            hero_row.pack_start(btn, True, True, 0)
            if   slot == "status": self._status_preview = preview
            elif slot == "load":   self._load_preview   = preview

        outer.pack_start(hero_row, True, True, 0)

        # ── Utility row (fixed height) ────────────────────────────────────────
        util_row = Gtk.Box(spacing=8)
        util_row.set_hexpand(True)
        util_row.set_size_request(-1, _UTIL_ROW_PX)
        for label, panel, ptitle, color, icon in _UTIL:
            btn = self._gtk.Button(icon, label, color)
            btn.connect("clicked", self._open_panel, panel, ptitle)
            util_row.pack_start(btn, True, True, 0)
        outer.pack_start(util_row, False, False, 0)

        self.content.add(outer)

    # ── Hero button factory ────────────────────────────────────────────────

    def _build_hero_btn(self, icon_name, label, color_class):
        """Hero tile: large icon + bold label + preview text line.

        Returns (button, preview_label) so the caller can later update the
        preview text by calling preview_label.set_markup(...).
        """
        btn = Gtk.Button()
        btn.set_hexpand(True)
        btn.set_vexpand(True)
        btn.get_style_context().add_class(color_class)
        # Same focus-reset hook KS attaches to its standard Buttons.
        btn.connect("clicked", self._screen.screensaver.reset_timeout)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_valign(Gtk.Align.CENTER)
        vbox.set_halign(Gtk.Align.CENTER)

        # Icon — 2× standard KS button image scale for hero emphasis.
        scale     = self._gtk.button_image_scale * 2.0
        icon_size = int(self._gtk.img_scale * scale)
        img = self._gtk.Image(icon_name, icon_size, icon_size)
        img.set_halign(Gtk.Align.CENTER)
        vbox.pack_start(img, False, False, 0)

        # Main label (bold).
        lbl = Gtk.Label()
        lbl.set_markup(
            '<span font_size="large" weight="bold">%s</span>' % label)
        lbl.set_halign(Gtk.Align.CENTER)
        vbox.pack_start(lbl, False, False, 0)

        # Preview line (small, dimmed). Updated by _update_previews().
        preview = Gtk.Label()
        preview.set_markup(
            '<span font_size="small" foreground="#BDBDBD">…</span>')
        preview.set_halign(Gtk.Align.CENTER)
        preview.set_max_width_chars(32)
        preview.set_ellipsize(3)
        vbox.pack_start(preview, False, False, 2)

        btn.add(vbox)
        return btn, preview

    # ── Click → open panel ─────────────────────────────────────────────────

    def _open_panel(self, widget, panel_name, panel_title):
        self._screen.show_panel(panel_name, panel_title)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def activate(self):
        self._screen._ws.klippy.object_subscription(
            {"objects": _sasub.build_subscription(self._screen)})
        _sasub.install_global_popup_watcher(self._screen)
        # One-shot query so previews show real data immediately.
        try:
            resp = self._screen.apiclient.send_request(
                "printer/objects/query?autoloader")
            if resp and 'status' in resp:
                self._last_sa = resp['status'].get('autoloader', {}) or {}
        except Exception as e:
            logger.warning("sa_home: query failed: %s", e)
        self._update_previews()

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("autoloader")
        if sa is None:
            return
        self._last_sa.update(sa)
        GLib.idle_add(self._update_previews)

    # ── Preview text builders ─────────────────────────────────────────────

    def _update_previews(self):
        self._set_preview_markup(self._status_preview, self._build_status_text())
        self._set_preview_markup(self._load_preview,   self._build_load_text())
        return False

    def _build_status_text(self):
        sa        = self._last_sa
        states    = sa.get("path_states", [])
        materials = sa.get("path_materials", [])
        entry     = sa.get("entry_filament",    [])
        toolhead  = sa.get("toolhead_filament", [])
        extruder  = sa.get("extruder_filament", [])
        total     = len(states)

        # Use effective state (stored + sensors) so this preview matches
        # the count shown by sa_main on the actual STATUS panel.
        eff = [_effective_state(i, states, entry, toolhead, extruder)
               for i in range(total)]
        loaded = sum(1 for s in eff if s == "loaded")

        # Up to 2 loaded paths' material names inline; otherwise just the count.
        seen = []
        for i, m in enumerate(materials):
            if m and i < len(eff) and eff[i] == "loaded" and len(seen) < 2:
                seen.append("T%d %s" % (i, m))
        if seen:
            return "%d/%d loaded · %s" % (loaded, total, " · ".join(seen))
        if total:
            return "%d / %d paths loaded" % (loaded, total)
        return "Autoloader idle"

    def _build_load_text(self):
        sa      = self._last_sa
        cur     = sa.get("current_path", -1)
        engaged = sa.get("servo_engaged", False)
        if cur is None or cur < 0:
            return "Selector unhomed"
        msg = "Selector at T%d" % cur
        if engaged:
            msg += " · drive engaged"
        return msg

    @staticmethod
    def _set_preview_markup(label, text):
        if label is None:
            return
        from xml.sax.saxutils import escape as _xml_escape
        label.set_markup(
            '<span font_size="small" foreground="#BDBDBD">%s</span>'
            % _xml_escape(text))
