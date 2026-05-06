# Background LED animator for the autoloader's empty-path indicator.
#
# Klipper Python extra. Drives a slow "breathing" white pulse on each
# unloaded toolhead's logo LED (chain INDEX=3) while the printer is
# idle. Pauses cleanly when:
#
#   * idle_timeout reports state == "Printing"
#   * autoloader is in any cal_state (load / unload / calibration)
#
# When pause begins, the brightness LERPs toward zero across a few
# ticks (smooth fade-out, no abrupt transition). When idle resumes,
# the breathing waveform picks up smoothly from wherever the LED
# currently is.
#
# Why a Python extra rather than a [delayed_gcode] / jinja loop:
#   - reactor timer runs in Klipper's main thread; no GCode mutex
#     contention with print commands or autoloader sequences
#   - smooth interpolation needs floating-point math the gcode
#     parser can't easily express across many channels
#   - one place to coordinate state across all 6 toolheads
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
        self._tick_interval = 1.0 / self.update_rate_hz
        self._current = {}   # tool_n -> last emitted brightness
        self._led_chains = []
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


def load_config(config):
    return SaLedAnimator(config)
