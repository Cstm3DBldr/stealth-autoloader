# sa_subscription.py — shared object-subscription builder for autoloader panels.
#
# Problem this solves:
#   `printer.objects.subscribe` REPLACES the previous subscription on a
#   websocket connection (Moonraker docs: "Successive requests will
#   overwrite the previous subscriptions"). So when an autoloader panel
#   activates and subscribes to only `autoloader`, KlipperScreen's base
#   panel stops receiving the `toolhead.extruder` updates that drive its
#   active-extruder temperature display — the toolhead-temp icon in the
#   top-left freezes on whichever extruder was active when the panel
#   opened, and won't follow tool changes that the autoloader triggers
#   during a load/unload.
#
# Fix:
#   Every autoloader panel calls build_subscription() to produce a dict
#   that includes BOTH the standard KS objects (so toolhead, extruder
#   temps, heaters, etc. keep flowing to base_panel) AND the autoloader
#   itself. The result is one combined subscribe call that doesn't
#   starve any consumer.

# ── Global popup watcher ─────────────────────────────────────────────────────
# KlipperScreen's screen.process_update only forwards status updates to the
# CURRENTLY ACTIVE panel. So a watcher that lives in sa_main.process_update
# (or any other autoloader panel) only fires when that panel is on screen.
# That meant: if the user kicked off a load/unload from Mainsail while KS was
# on the home screen (or any non-autoloader panel), KS never saw cal_state
# transition to 'load_purge' / 'unload_done' and never opened sa_post_load —
# the post-load action popup only appeared in Mainsail.
#
# Fix: monkey-patch screen.process_update once, the first time any autoloader
# panel calls install_global_popup_watcher(). The wrapped version delegates
# to the original (so all existing per-panel handlers keep working), then
# also checks autoloader.cal_state and opens sa_post_load on the load_purge /
# unload_done transitions regardless of which panel is currently active.

_watcher_installed = False
_last_cal_state    = None
_last_entry        = []
_initialized       = False  # baseline from first observation (no trigger)

# When the user explicitly dismisses sa_post_load (Park / Exit / Load-Same),
# Klipper's SA_RESPOND processing is asynchronous — there's a window where
# subsequent status updates still report the SAME cal_state value because
# the gcode hasn't been picked up yet. Without this flag, our global watcher
# (or sa_main's panel-local watcher) sees what looks like a fresh transition
# into load_purge / unload_done on the very next status reprocess and reopens
# sa_post_load. The flag stores the cal_state at dismiss time and suppresses
# popup reopening as long as cal_state matches; it clears automatically when
# cal_state actually transitions to a different value (i.e. Klipper has
# processed the response).
_dismissed_at_cal_state = None


def mark_user_dismissed(cal_state):
    """Called from sa_post_load._close() when the user explicitly dismisses
    the action popup. Suppresses popup reopen on the same cal_state value."""
    global _dismissed_at_cal_state
    _dismissed_at_cal_state = cal_state

def install_global_popup_watcher(screen):
    """Monkey-patch screen.process_update so the post-load action popup and
    the on-insert load wizard fire from any KlipperScreen panel, matching
    Mainsail's always-on behavior. Idempotent — only patches once per run.
    """
    global _watcher_installed
    if _watcher_installed:
        return
    _watcher_installed = True
    original = screen.process_update

    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        try:
            _on_status(screen, *args)
        except Exception:
            import logging
            logging.exception("sa_subscription: popup watcher failed")
        return result

    screen.process_update = wrapped


def _on_status(screen, *args):
    """Inspect a status update for autoloader transitions and open the
    appropriate popup panel via screen.show_panel."""
    global _last_cal_state, _last_entry, _initialized
    if not args or args[0] != "notify_status_update":
        return
    if len(args) < 2 or not isinstance(args[1], dict):
        return
    sa = args[1].get("autoloader")
    if not isinstance(sa, dict):
        return

    global _dismissed_at_cal_state
    cal   = sa.get("cal_state")
    entry = sa.get("entry_filament")

    # First observation establishes the baseline without firing any popup.
    # Otherwise a printer that already has filament inserted at boot would
    # auto-open the load wizard, and a stale cal_state would auto-open the
    # post-load panel.
    if not _initialized:
        _last_cal_state = cal if cal is not None else _last_cal_state
        if isinstance(entry, list):
            _last_entry = list(entry)
        _initialized = True
        return

    # Clear the user-dismissed flag once cal_state actually changes away
    # from the value at dismiss-time. This re-arms the popup for the next
    # legitimate transition into load_purge / unload_done.
    if (_dismissed_at_cal_state is not None
            and cal != _dismissed_at_cal_state):
        _dismissed_at_cal_state = None

    from gi.repository import GLib

    # cal_state transition → post-load action popup
    if cal is not None and cal != _last_cal_state:
        import logging
        logging.info(
            "sa_subscription: cal_state %r -> %r (dismissed_at=%r)"
            % (_last_cal_state, cal, _dismissed_at_cal_state))
        # Skip popup if user already explicitly dismissed at this cal_state
        # value. The dismiss flag clears automatically (above) once cal_state
        # actually transitions away.
        if cal == _dismissed_at_cal_state:
            logging.info("sa_subscription: suppressed (user dismissed)")
        elif cal in ("load_purge", "unload_done"):
            # Popup-on-complete is now unconditional — the per-user toggle
            # in sa_settings was removed because the popup behaviour worked
            # well enough that gating it added complexity without value.
            logging.info("sa_subscription: opening sa_post_load")
            GLib.idle_add(
                screen.show_panel, "sa_post_load", "SA Action")
        elif cal:
            logging.info("sa_subscription: opening sa_cal_prompt")
            GLib.idle_add(
                screen.show_panel, "sa_cal_prompt", "SA Calibration")
        _last_cal_state = cal

    # entry-sensor rising edge → load/unload wizard popup (filament inserted)
    if isinstance(entry, list):
        for i, active in enumerate(entry):
            was = _last_entry[i] if i < len(_last_entry) else False
            if not was and active:
                GLib.idle_add(
                    screen.show_panel, "sa_load_unload", "Load / Unload")
        _last_entry = list(entry)


def build_subscription(screen, num_paths=0, include_encoders=False):
    """Combined subscription dict for an autoloader panel.

    screen           : the KlipperScreen instance (self._screen in panels)
    num_paths        : number of autoloader paths (only used if include_encoders)
    include_encoders : if True, subscribe to each `sa_encoder N` object too
                       (needed by sa_main; not needed by load/unload panels)
    """
    objs = {
        # Autoloader's own status object — drives the panel UI.
        "autoloader":         None,
        # Toolhead — base_panel reads `toolhead.extruder` to know which
        # extruder's temperature to display in the top bar. WITHOUT this,
        # the temp icon won't follow autoloader-triggered tool changes.
        "toolhead":           ["homed_axes", "extruder", "position",
                               "estimated_print_time", "print_time",
                               "max_accel", "max_velocity",
                               "minimum_cruise_ratio",
                               "square_corner_velocity"],
        # Standard KS panels watch these for state and progress display.
        "gcode_move":         ["extrude_factor", "gcode_position",
                               "homing_origin", "speed_factor", "speed"],
        "idle_timeout":       ["state"],
        "pause_resume":       ["is_paused"],
        "print_stats":        ["print_duration", "total_duration",
                               "filament_used", "filename", "state",
                               "message", "info"],
        "virtual_sdcard":     ["file_position", "is_active", "progress"],
        "webhooks":           ["state", "state_message"],
        "motion_report":      ["live_position", "live_velocity",
                               "live_extruder_velocity"],
        "fan":                ["speed"],
        "display_status":     ["progress", "message"],
    }
    if include_encoders:
        for i in range(num_paths):
            objs["sa_encoder %d" % i] = None
    # All extruders — base_panel's per-extruder temp boxes need these.
    try:
        for tool in screen.printer.get_tools():
            objs[tool] = ["target", "temperature", "pressure_advance",
                          "smooth_time", "power"]
        for h in screen.printer.get_heaters():
            objs[h] = ["target", "temperature", "power"]
        for s in screen.printer.get_temp_sensors():
            objs[s] = ["temperature"]
        for f in screen.printer.get_fans():
            objs[f] = ["speed"]
        for fs in screen.printer.get_filament_sensors():
            objs[fs] = ["enabled", "filament_detected"]
    except Exception:
        # If printer object isn't fully initialized yet, return what we have.
        pass
    return objs
