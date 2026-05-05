import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sa_button_style as _sbs
import sa_subscription as _sasub
from ks_includes.screen_panel import ScreenPanel


# Module-level guard so the CSS provider is installed exactly once per
# KlipperScreen session — adding it repeatedly would stack rules.
_action_bar_css_installed = False


def _install_action_bar_css():
    """Inject explicit padding/margin on base_panel's action_bar buttons.

    Per-child diagnostic showed each visible action_bar button reports
    ~4 px more natural height on first attach (121/120) than after the
    layout settles on subsequent attaches (117/116) — a 16 px overflow
    on a 480-px screen that clips the bottom power icon. Root cause:
    the default GTK button padding from base.css (margin: .2em;
    padding: .25em) leaves the natural height dependent on font/em
    measurement timing, which differs between first realize and later
    layout passes.

    Pinning padding/margin to fixed pixel values via a high-priority
    CSS provider stabilizes each button's natural height across all
    layout passes — first attach matches subsequent attach.
    """
    global _action_bar_css_installed
    if _action_bar_css_installed:
        return
    try:
        css = Gtk.CssProvider()
        # Visible-change marker (red border) confirms our CSS is reaching
        # the buttons. Padding/margin pinned to fixed pixels to stabilize
        # natural height across first/subsequent layout passes. Multiple
        # selectors so whatever the actual class structure of the buttons
        # is, at least one match wins.
        css.load_from_data(
            b".action_bar button,"
            b" .action_bar > button,"
            b" box.action_bar button {"
            b"  margin: 0;"
            b"  padding: 2px;"
            b"  min-height: 0;"
            b"  min-width: 0;"
            b"}"
            # Stabilize section header label sizing on first attach.
            # Default theme rules use em-based padding/margin which
            # measures slightly differently before vs after the first
            # font-metric realize pass, inflating each header by ~4 px
            # on first attach (4 headers × 4 px ≈ 16 px of content
            # overflow that stretches the rail).
            b".sa-section-header {"
            b"  margin: 0;"
            b"  padding: 0;"
            b"  min-height: 0;"
            b"}"
        )
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_USER + 100)
        _action_bar_css_installed = True
        logging.info("sa_macros: action_bar CSS provider installed")
    except Exception:
        logging.exception("sa_macros: failed to install action_bar CSS")

logger = logging.getLogger('klipperscreen.sa_macros')


# Concept B layout: 3 grouped sections instead of one flat 4×3 grid.
#   DAILY        — buttons used during normal operation
#   DIAGNOSTICS  — confirm motors/wiring without committing to a calibration
#   CALIBRATION  — one-time setup commands
#
# (label, gcode_template, needs_tool)
# Use {t} as placeholder for TOOL=N when needs_tool=True.

_DAILY = [
    ("HOME SELECTOR",   "SA_HOME",                          False),
    ("SELECT PATH",     "SA_SELECT TOOL={t}",               True),
    ("ENGAGE",          "SA_ENGAGE",                        False),
    ("DISENGAGE",       "SA_DISENGAGE",                     False),
]

_DIAG = [
    ("STATUS REPORT",   "SA_STATUS",                        False),
    ("BUZZ DRIVE",      "SA_BUZZ_DRIVE",                    False),
    ("BUZZ SELECTOR",   "SA_BUZZ_SELECTOR",                 False),
]

# CALIBRATION is laid out as TWO rows under one section header:
#   row 1 (globals, 3 buttons)
#   row 2 (per-tool, 2 buttons — both open the tool picker on click)
#
# Encoder cals are TWO different things despite the similar names:
#   SA_CALIBRATE_ENCODER_SPEED  - global, finds max reliable feed
#                                 speed before the encoder slips
#   SA_CALIBRATE_ENCODER TOOL=N - per-tool, measures mm-per-pulse
#                                 calibration for one path
# Don't merge them.
#
# Labels stack on two lines via embedded \n for visual consistency
# across both rows. The 3-column row 1 has enough width for the full
# "Encoder Speed" label; the 5-column variant had to abbreviate to
# "Enc Speed".
_CAL_GLOBAL = [
    ("Calibrate\nSelector",      "SA_CALIBRATE_SELECTOR",        False),
    ("Calibrate\nDrive",         "SA_CALIBRATE_DRIVE",           False),
    ("Calibrate\nEncoder Speed", "SA_CALIBRATE_ENCODER_SPEED",   False),
]
_CAL_PERTOOL = [
    ("Calibrate\nEncoder",       "SA_CALIBRATE_ENCODER TOOL={t}", True),
    ("Calibrate\nBowden",        "SA_CALIBRATE_BOWDEN TOOL={t}",  True),
]


class Panel(ScreenPanel):
    """Autoloader macros — grouped by frequency of use.

    DAILY first, large; DIAGNOSTICS middle; CALIBRATION at the bottom with
    smaller buttons since those are run once per setup.
    """

    def __init__(self, screen, title):
        super().__init__(screen, title or "SA Macros")
        _sbs.apply()
        # Install CSS pinning action_bar button padding/margin to fixed
        # pixel values. Idempotent across panels and panel re-creation.
        _install_action_bar_css()

        self._num_paths   = 6
        self._pending_cmd = None   # gcode template waiting for tool selection

        # Use Gtk.Notebook (with tabs hidden) instead of Gtk.Stack for
        # page switching. Stack's vhomogeneous flag — even when set to
        # False before adding children — doesn't reliably take effect on
        # the first allocation pass after KlipperScreen restart, so the
        # rail stretches and the power icon clips off-screen on first
        # open. Notebook sizes each page independently with no shared
        # homogeneous semantics, matching sa_load_unload's known-good
        # behaviour. The page index map keeps the call sites readable
        # (set_page("main") / set_page("tool")) instead of bare indices.
        self._stack       = Gtk.Notebook()
        self._stack.set_show_tabs(False)
        self._stack.set_show_border(False)
        self._page_index  = {}
        self._page_index["main"] = self._stack.append_page(
            self._build_main_page(), None)
        self._page_index["tool"] = self._stack.append_page(
            self._build_tool_page(), None)
        self._stack.set_current_page(self._page_index["main"])

        self.content.pack_start(self._stack, True, True, 0)

        # Override screen_panel.py's default vexpand=True on self.content
        # AND pin its size_request to KS's precomputed content_height.
        # Together these stop self.content from competing with the
        # action_bar's vexpand=True for leftover vertical space — the
        # content widget is exactly content_height tall on every
        # allocation, so the grid has no leftover to distribute and the
        # action_bar gets exactly the screen.height it requested.
        try:
            ch = int(getattr(self._gtk, 'content_height', 0))
        except Exception:
            ch = 0
        if ch > 0:
            self.content.set_vexpand(False)
            self.content.set_size_request(-1, ch)
            self._stack.set_size_request(-1, ch)


    def _set_page(self, name):
        """Notebook equivalent of Stack.set_visible_child_name."""
        idx = self._page_index.get(name)
        if idx is not None:
            self._stack.set_current_page(idx)

    # ── Section header ────────────────────────────────────────────────────

    def _section_header(self, title):
        """Small-caps dimmed label used as a section divider.

        First-render correctness note: Pango markup attributes that
        depend on font metrics (e.g. font_size="x-small",
        letter_spacing="2000") produced different natural heights on
        first vs subsequent realize passes. Use a fixed pt-size and
        skip letter_spacing so the label measures the same on every
        pass. The .sa-section-header CSS class (installed alongside
        the action_bar overrides) pins margin/padding to fixed pixels
        for the same reason.
        """
        lbl = Gtk.Label(halign=Gtk.Align.START, xalign=0.0)
        lbl.set_markup(
            '<span font="11" foreground="#9E9E9E">── %s ──</span>' % title)
        lbl.get_style_context().add_class("sa-section-header")
        return lbl

    def _section_row(self, items, btn_h):
        """Single row of equal-width buttons for a section.

        Three layers of overflow protection:
          1. row.set_homogeneous(True) splits available width equally
             across N children regardless of label length.
          2. Each button's label wraps to up to 2 lines (word-aware) so
             a long two-word label like "HOME SELECTOR" stacks vertically
             instead of being chopped with "…".
          3. set_lines(2) caps wrapping at 2 lines and falls back to
             ellipsize if the label is still too wide (rare with the
             current label set, but safe).
        """
        from gi.repository import Pango
        row = Gtk.Box(spacing=6)
        row.set_hexpand(True)
        row.set_homogeneous(True)
        for label, gcode, needs_tool in items:
            btn = _sbs.make(label)
            btn.set_size_request(-1, btn_h)
            child = btn.get_child()
            if isinstance(child, Gtk.Label):
                child.set_line_wrap(True)
                child.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                child.set_lines(2)
                child.set_justify(Gtk.Justification.CENTER)
                child.set_ellipsize(Pango.EllipsizeMode.END)
                child.set_max_width_chars(12)
            if needs_tool:
                btn.connect("clicked", self._pick_tool, gcode)
            else:
                btn.connect("clicked", self._send, gcode)
            row.pack_start(btn, True, True, 0)
        return row

    # ── Main page ─────────────────────────────────────────────────────────

    def _build_main_page(self):
        # Sized to fill the available 444 px of content_height (480 -
        # titlebar) with appealing breathing room. With QUICK RE-CAL
        # removed (its 3 buttons were duplicates of the first 3 in
        # CALIBRATION), the remaining 3 sections grow to use the freed
        # ~75 px.
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_margin_top(10)
        outer.set_margin_start(8)
        outer.set_margin_end(8)
        outer.set_margin_bottom(14)

        # CALIBRATION renders as 3+2 rows under one section header.
        # Heights are tuned so all four button rows + 3 section headers
        # + spacing + margins still leave ~50 px for the bottom spacer
        # to absorb visibly.
        outer.pack_start(self._section_header("DAILY"),              False, False, 0)
        outer.pack_start(self._section_row(_DAILY,        btn_h=80), False, False, 0)

        outer.pack_start(self._section_header("DIAGNOSTICS"),        False, False, 0)
        outer.pack_start(self._section_row(_DIAG,         btn_h=66), False, False, 0)

        outer.pack_start(self._section_header("CALIBRATION"),        False, False, 0)
        outer.pack_start(self._section_row(_CAL_GLOBAL,   btn_h=64), False, False, 0)
        outer.pack_start(self._section_row(_CAL_PERTOOL,  btn_h=64), False, False, 0)

        # vexpand spacer at the end — REQUIRED for first-render correctness.
        #
        # In landscape mode base_panel.py:75 sets action_bar.set_vexpand(True)
        # and action_bar.set_size_request(action_bar_width, screen.height).
        # The action_bar spans both grid rows in column 0, so it's competing
        # with the content row for vertical budget.
        #
        # If every child of `outer` is pack_start(False, False, 0), the Box's
        # natural height is just the sum of those children — and on first
        # allocation, GTK's grid pass hands action_bar more height than the
        # content row, squeezing the buttons until they overflow or stretch
        # the rail icons. (sa_load_unload's path page doesn't have this bug
        # because its inner _path_grid is packed with expand=True; that one
        # expanding child is enough to make the page claim all available
        # vertical space on every allocation pass.)
        #
        # A single vexpand=True spacer at the end of `outer` is the minimal
        # equivalent — the Box now always claims its full slice of the
        # content row, action_bar gets exactly its set_size_request budget,
        # and the layout is identical on first open and re-open.
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        outer.pack_start(spacer, True, True, 0)

        return outer

    # ── Tool picker page ──────────────────────────────────────────────────

    def _build_tool_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        lbl = Gtk.Label()
        lbl.set_markup('<b><span font_size="large">Select Path</span></b>')
        lbl.set_margin_top(10)
        lbl.set_margin_bottom(6)
        outer.pack_start(lbl, False, False, 0)

        self._tool_grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True,
                                   row_spacing=6, column_spacing=6,
                                   margin_start=8, margin_end=8)
        outer.pack_start(self._tool_grid, True, True, 0)

        back_btn = _sbs.make("←  Cancel", "sa-btn-alt")
        back_btn.set_margin_start(8)
        back_btn.set_margin_end(8)
        back_btn.set_margin_top(6)
        back_btn.set_margin_bottom(8)
        back_btn.connect("clicked",
                         lambda w: self._set_page("main"))
        outer.pack_start(back_btn, False, False, 0)

        # The tool page already has _tool_grid packed with expand=True,
        # which makes the page claim all available vertical space — so
        # no extra vexpand spacer is needed here (unlike _build_main_page).
        return outer

    def _rebuild_tool_buttons(self, num_paths):
        for child in self._tool_grid.get_children():
            self._tool_grid.remove(child)
        for i in range(num_paths):
            btn = _sbs.make("T%d" % i)
            btn.connect("clicked", self._tool_selected, i)
            self._tool_grid.attach(btn, i % 3, i // 3, 1, 1)
        self._tool_grid.show_all()

    # ── Handlers ──────────────────────────────────────────────────────────

    def _send(self, widget, gcode):
        self._screen._ws.klippy.gcode_script(gcode)

    def _pick_tool(self, widget, gcode_template):
        self._pending_cmd = gcode_template
        self._rebuild_tool_buttons(self._num_paths)
        self._set_page("tool")

    def _tool_selected(self, widget, tool_idx):
        if self._pending_cmd:
            cmd = self._pending_cmd.replace("{t}", str(tool_idx))
            self._screen._ws.klippy.gcode_script(cmd)
            self._pending_cmd = None
        self._set_page("main")

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def activate(self):
        # Same combined subscription + global popup watcher pattern as the
        # other autoloader panels — keeps base_panel toolhead-temp updating
        # and lets popups fire from any KS panel.
        self._screen._ws.klippy.object_subscription(
            {"objects": _sasub.build_subscription(self._screen)})
        _sasub.install_global_popup_watcher(self._screen)

        sa = self._printer.data.get("autoloader", {})
        self._num_paths = sa.get("num_paths", 6)
        self._set_page("main")

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        sa = data.get("autoloader")
        if sa is not None:
            n = sa.get("num_paths")
            if n is not None:
                self._num_paths = n
        return False
