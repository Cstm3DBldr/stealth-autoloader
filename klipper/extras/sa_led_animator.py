# Background LED animator for the autoloader's toolhead LEDs.
#
# Klipper Python extra. Two responsibilities, both running off one
# reactor timer in Klipper's main thread:
#
#   1. Empty-path breathing pulse — slow white sine wave on the logo
#      LED (INDEX=3) of each unloaded toolhead while idle. Pauses
#      cleanly when actively printing or during any autoloader op.
#
#   2. Temp-aware active-tool nozzle — when the printer is NOT
#      actively printing (idle, ready, or paused), the active tool's
#      nozzle pair (INDEX=1,2) reflects hotend warmth as a safety
#      indicator: red-orange while still warm, dim blue once cooled.
#      The "warm" signal is read from the toolhead's heater_fan state
#      (which Klipper already manages with a >= 50 C threshold) so we
#      don't duplicate the threshold logic. This keeps the nozzle's
#      hot/cold state up to date as the hotend cools after a pause
#      times out, without any external poll loop or state-machine
#      logic in macros.
#
# When pause begins, the breathing brightness LERPs toward zero
# across a few ticks (smooth fade-out, no abrupt transition). When
# idle resumes, the waveform picks up smoothly from wherever the LED
# currently is.
#
# Why a Python extra rather than a [delayed_gcode] / jinja loop:
#   - reactor timer runs in Klipper's main thread; no GCode mutex
#     contention with print commands or autoloader sequences
#   - smooth interpolation needs floating-point math the gcode
#     parser can't easily express across many channels
#   - one place to coordinate state across all 6 toolheads
#   - cheap to poll heater_fan state every tick (<1ms across 6 tools)
#
# Configuration (auto-loaded from a [sa_led_animator] block in cfg):
#
#   [sa_led_animator]
#   #breathing_period: 4.0      # seconds per full pulse cycle
#   #min_brightness: 0.0        # brightness at the trough of the pulse
#   #max_brightness: 0.40       # brightness at the peak (~1/2 of full)
#   #update_rate_hz: 5.0        # ticks per second
#   #smoothing_factor: 0.30     # LERP step toward target each tick
#                               # (1.0 = snap, 0.1 = very slow easing)
#   #hotend_fan_template: "heater_fan T%d_hotend_fan"
#                               # printf-style template resolving each
#                               # tool number to its hotend heater_fan
#                               # name. Override if the toolchanger
#                               # config uses a different naming
#                               # convention.

import logging
import math


class SaLedAnimator:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.breathing_period = config.getfloat(
            'breathing_period', 4.0, above=0.5, maxval=30.0)
        self.min_brightness = config.getfloat(
            'min_brightness', 0.0, minval=0.0, maxval=1.0)
        self.max_brightness = config.getfloat(
            'max_brightness', 0.40, minval=0.0, maxval=1.0)
        self.update_rate_hz = config.getfloat(
            'update_rate_hz', 5.0, above=0.5, maxval=60.0)
        self.smoothing = config.getfloat(
            'smoothing_factor', 0.30, above=0.01, maxval=1.0)
        self.hotend_fan_template = config.get(
            'hotend_fan_template', 'heater_fan T%d_hotend_fan')
        self._tick_interval = 1.0 / self.update_rate_hz
        self._current = {}   # tool_n -> last emitted brightness
        self._led_chains = []
        # tool_n -> last (r,g,b,w) emitted to nozzle pair, used to
        # avoid retransmitting the same color tick after tick.
        self._nozzle_last = {}
        self.printer.register_event_handler('klippy:ready', self._handle_ready)

    def _handle_ready(self):
        self.gcode    = self.printer.lookup_object('gcode')
        self.reactor  = self.printer.get_reactor()

        # Discover et0_leds .. et7_leds (one per toolhead). chain_count
        # of 3 is the StealthBurner standard; INDEX=3 is the logo on
        # this build (verified via _SA_LED_TEST_T0).
        #
        # We grab each neopixel's `led_helper` directly. Driving LEDs
        # via run_script_from_command("SET_LED ...") from a reactor
        # timer fails silently — that gcode helper is only safe to
        # call from inside another gcode command's handler, where the
        # gcode mutex is held. The led_helper's _set_color +
        # _check_transmit pair is the documented internal path that
        # other animation extras use from reactor callbacks.
        for i in range(8):
            obj = self.printer.lookup_object('neopixel et%d_leds' % i, None)
            if obj is None:
                continue
            led_helper = getattr(obj, 'led_helper', None)
            if led_helper is None:
                logging.info(
                    "sa_led_animator: neopixel et%d_leds has no "
                    "led_helper attribute; skipping", i)
                continue
            self._led_chains.append((i, 'et%d_leds' % i, led_helper))
            self._current[i] = 0.0

        if not self._led_chains:
            logging.info("sa_led_animator: no et*_leds chains found; "
                         "animator will not run")
            return

        # Start ~5s after ready, matching the [delayed_gcode]
        # _SA_LEDS_STARTUP fallback timing — gives the autoloader
        # extra a moment to restore path_color_hexes from
        # save_variables before we start checking path_states.
        self._timer = self.reactor.register_timer(
            self._animate, self.reactor.monotonic() + 5.0)
        logging.info(
            "sa_led_animator: started — %d chain(s), period=%.1fs, "
            "max_brightness=%.2f, rate=%.1fHz, smoothing=%.2f",
            len(self._led_chains), self.breathing_period,
            self.max_brightness, self.update_rate_hz, self.smoothing)

    # ──────────────────────────────────────────────────────────────────
    # Reactor timer callback — fires at update_rate_hz
    # ──────────────────────────────────────────────────────────────────

    def _animate(self, eventtime):
        try:
            self._tick(eventtime)
        except Exception:
            logging.exception("sa_led_animator: tick failed (suppressed)")
        return eventtime + self._tick_interval

    def _tick(self, eventtime):
        # 1. Read printer-wide state ───────────────────────────────────
        idle_state = self._idle_state(eventtime)
        sa         = self.printer.lookup_object('autoloader', None)
        if sa is None:
            return  # autoloader not loaded — nothing to animate

        cal_state    = getattr(sa, '_cal_state', '') or ''
        path_states  = list(getattr(sa, 'path_states', []) or [])
        active_tool  = self._active_tool_number()

        # Pause animation entirely while a print is running OR while the
        # autoloader has any operation in flight. Both are short windows
        # but stomping on the load/unload's own _SA_LED_PARKED transition
        # at the end would look wrong.
        paused = (idle_state == 'printing') or bool(cal_state)

        # 2. Compute the ideal brightness for this tick from a sine
        #    wave (smooth 0 -> 1 -> 0 -> ... over breathing_period).
        phase = (eventtime % self.breathing_period) / self.breathing_period
        wave  = (math.sin(2.0 * math.pi * phase) + 1.0) / 2.0
        ideal = self.min_brightness + wave * (
            self.max_brightness - self.min_brightness)

        # 3. Per-toolhead. Animate any path whose state is "no filament
        #    here" — that means either explicit 'empty' (after a
        #    successful SA_UNLOAD) OR 'unknown' (the default after
        #    Klipper boot, before SA_LOAD/SA_UNLOAD has run on this
        #    path). Stale color hex from a prior session does NOT
        #    suppress the breathing — the path is conceptually empty
        #    until a load actually completes. 'loaded' and 'partial'
        #    paths are left to _SA_LED_PARKED / _SA_LED_FROM_STATE.
        for tool_n, led_name, led_helper in self._led_chains:
            # Active mounted tool: leave alone (other macros handle it)
            if tool_n == active_tool:
                self._current[tool_n] = 0.0
                continue

            state = (path_states[tool_n]
                     if tool_n < len(path_states)
                     else 'unknown')

            if state not in ('empty', 'unknown'):
                # 'loaded', 'partial', or anything else — let the
                # existing macros drive the logo
                self._current[tool_n] = 0.0
                continue

            target = 0.0 if paused else ideal

            # LERP toward target with smoothing factor; produces the
            # gentle fade-out when transitioning into a print and the
            # gentle fade-in coming out of one.
            current  = self._current.get(tool_n, 0.0)
            smoothed = current + (target - current) * self.smoothing
            self._current[tool_n] = smoothed

            self._emit(led_helper, smoothed)

        # 4. Active-tool nozzle: temp-aware safety indicator.
        # ─────────────────────────────────────────────────────────
        # When the printer is NOT actively printing (i.e. idle,
        # ready, or paused mid-print), the active tool's nozzle pair
        # reflects hotend warmth: red-orange "still warm" while the
        # heater_fan is on (extruder >= 50 C, the threshold Klipper
        # already manages), dim blue once it has cooled below the
        # fan's off-threshold.
        #
        # During an actual print (idle_state == 'printing' AND not
        # paused), STATUS_PRINTING / STATUS_HEATING / etc. control
        # the nozzle and we yield. The same is true during any
        # autoloader operation (cal_state non-empty) — the load /
        # unload sequence may want a different nozzle treatment.
        is_paused = self._is_paused(eventtime)
        actively_printing = (idle_state == 'printing') and not is_paused
        animator_owns_nozzle = (not actively_printing
                                and not bool(cal_state)
                                and active_tool >= 0)
        if animator_owns_nozzle:
            warm = self._is_tool_warm(active_tool, eventtime)
            color = self._get_nozzle_color(
                'heating' if warm else 'parked_cold')
            if color is not None:
                for tool_n, _, helper in self._led_chains:
                    if tool_n == active_tool:
                        if self._nozzle_last.get(tool_n) != color:
                            self._emit_nozzle(helper, color)
                            self._nozzle_last[tool_n] = color
                        break
        else:
            # If we previously owned a tool's nozzle and now don't,
            # forget the cached value so the next animator-owned
            # tick will retransmit (handles cases where some other
            # macro changed the nozzle while we were yielded).
            self._nozzle_last.pop(active_tool, None)

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    def _idle_state(self, eventtime):
        """Return idle_timeout state lowercased ('idle' / 'ready' / 'printing')."""
        it = self.printer.lookup_object('idle_timeout', None)
        if it is None:
            return 'idle'
        try:
            return str(it.get_status(eventtime).get('state', 'Idle')).lower()
        except Exception:
            return 'idle'

    def _active_tool_number(self):
        """Return the currently mounted tool's number, or -1 if none."""
        tpe = self.printer.lookup_object('tool_probe_endstop', None)
        if tpe is None:
            return -1
        try:
            return int(getattr(tpe, 'active_tool_number', -1))
        except Exception:
            return -1

    def _emit(self, led_helper, brightness):
        """Push one logo-LED update via the neopixel's led_helper directly.

        Bypasses the gcode dispatcher entirely. Safe to call from a
        reactor timer because led_helper._check_transmit registers a
        reactor callback (with mutex) for the actual chip transmit —
        we just stage the new color in led_state.

        Logo is INDEX=3 in user-space (1-based), which maps to the
        same 1-based index passed to _set_color.
        """
        b = max(0.0, min(1.0, brightness))
        try:
            led_helper._set_color(3, (b, b, b, b))
            led_helper._check_transmit()
        except Exception:
            # Log once at warning level on first failure, then suppress
            # to avoid log spam from a recurring issue.
            if not getattr(self, '_emit_failed_logged', False):
                logging.exception("sa_led_animator: _emit failed "
                                  "(further failures suppressed)")
                self._emit_failed_logged = True

    def _emit_nozzle(self, led_helper, color):
        """Push one nozzle-pair update (INDEX=1 and INDEX=2)."""
        r, g, b, w = color
        try:
            led_helper._set_color(1, (r, g, b, w))
            led_helper._set_color(2, (r, g, b, w))
            led_helper._check_transmit()
        except Exception:
            if not getattr(self, '_nozzle_emit_failed_logged', False):
                logging.exception("sa_led_animator: _emit_nozzle failed "
                                  "(further failures suppressed)")
                self._nozzle_emit_failed_logged = True

    def _is_paused(self, eventtime):
        """True if PAUSE / M600 is currently in effect."""
        pr = self.printer.lookup_object('pause_resume', None)
        if pr is None:
            return False
        try:
            return bool(pr.get_status(eventtime).get('is_paused', False))
        except Exception:
            return False

    def _is_tool_warm(self, tool_n, eventtime):
        """True if tool N's hotend heater_fan is running.

        Reading the fan's speed is cheaper and more authoritative than
        polling extruder temp + duplicating the threshold — Klipper
        already manages the >= 50 C threshold via the heater_fan's
        heater_temp config (Voron StealthChanger default). When the
        fan is on, the hotend is at or above that threshold; when
        off, below.
        """
        name = self.hotend_fan_template % tool_n
        fan = self.printer.lookup_object(name, None)
        if fan is None:
            return False
        try:
            return float(fan.get_status(eventtime).get('speed', 0.0)) > 0.0
        except Exception:
            return False

    def _get_nozzle_color(self, state_name):
        """Look up an (r,g,b,w) tuple from _sa_led_vars.colors.nozzle.

        Reading from the leds.cfg variable bag means the canonical
        color values stay in one place — adjusting a state's RGB in
        leds.cfg automatically updates what the animator emits.
        Returns None if the macro / state isn't found (caller should
        skip the emit in that case rather than guess).
        """
        macro = self.printer.lookup_object(
            'gcode_macro _sa_led_vars', None)
        if macro is None or not hasattr(macro, 'variables'):
            return None
        try:
            colors = macro.variables.get('colors', {})
            c = colors.get('nozzle', {}).get(state_name)
            if c is None:
                return None
            return (float(c.get('r', 0.0)), float(c.get('g', 0.0)),
                    float(c.get('b', 0.0)), float(c.get('w', 0.0)))
        except Exception:
            return None


def load_config(config):
    return SaLedAnimator(config)
