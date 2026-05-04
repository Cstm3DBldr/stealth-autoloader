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
