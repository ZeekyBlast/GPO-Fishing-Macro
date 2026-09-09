"""The fishing bot: a single worker thread driving a timeout-guarded state machine.

    FOCUS -> PREP -> CAST -> WAIT -> REEL -> LOOT -> (MAINTENANCE) -> CAST ...
                      ^                                    |
                      +---------- black screen / timeout --+

Every state has a timeout or recovery path; a panic/failsafe releases the
mouse button immediately. The thread never touches the GUI directly - it
publishes to the EventBus and reads the ConfigStore.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum

import numpy as np

from .capture import ScreenGrabber, WindowTracker, focus_window, is_foreground
from .config import AppConfig
from .input import FailsafeError, InputController
from .stats import Event, EventBus, Stats
from . import controller
from . import tasks
from . import vision

log = logging.getLogger("gpo.fisher")


class State(Enum):
    IDLE = "idle"
    FOCUS = "focus"
    PREP = "prep"
    CAST = "cast"
    WAIT = "wait"
    REEL = "reel"
    LOOT = "loot"
    MAINTENANCE = "maintenance"
    RECOVER = "recover"     # Roblox loading / black screen
    PAUSED = "paused"       # Roblox lost foreground


class FishingBot(threading.Thread):
    def __init__(self, cfg: AppConfig, stats: Stats, bus: EventBus):
        super().__init__(name="fishing-bot", daemon=True)
        self.cfg = cfg
        self.stats = stats
        self.bus = bus
        self._stop_event = threading.Event()
        self._toggle_pause = threading.Event()

        self.state = State.IDLE
        self._tracker = WindowTracker()
        self._input: InputController | None = None

        self._deadline = 0.0
        self._black_streak = 0
        self._bite_streak = 0
        self._last_seen = 0.0
        self._reel_started = 0.0
        self._paused_from = None
        self._reel_frames = 0
        self._reel_presses = 0
        self._reel_releases = 0
        self._last_fish_y: float | None = None
        self._prev_bar_y: float | None = None
        self._bar_vel = 0.0
        self._fish_vel = 0.0
        self._last_overlap_time: float | None = None
        self._reel_last_debug = 0.0
        self._reel_error_published = False
        self._last_frame_time = 0.0
        self._catches_since_purchase = 0
        self._catches_since_craft = 0
        self._last_preview_publish = 0.0
        self._preview_lock = threading.Lock()
        self._preview_frame: np.ndarray | None = None
        self._reel_on_bar = 0
        self._telemetry: dict | None = None

    # ------------------------------------------------------------ lifecycle

    def stop(self) -> None:
        self._stop_event.set()

    def request_pause_toggle(self) -> None:
        """Hotkey/GUI pause: pauses the loop and releases the mouse."""
        self._toggle_pause.set()

    def panic(self) -> None:
        self._release_mouse_safely()
        self._stop_event.set()

    def preview_frame(self) -> np.ndarray | None:
        with self._preview_lock:
            return None if self._preview_frame is None else self._preview_frame.copy()

    def telemetry(self) -> dict | None:
        """Snapshot of the current fight for the dashboard, or None when idle.

        Published as a whole dict per frame, so a reader always gets a set of
        numbers that belong to the same frame without taking a lock.
        """
        return self._telemetry

    def _publish(self, kind: str, message: str, **data) -> None:
        event = Event(kind=kind, message=message, data=data)
        self.stats.add_event(event)
        self.bus.publish(event)

    def _set_state(self, state: State) -> None:
        if state != self.state:
            log.debug("state %s -> %s", self.state.value, state.value)
            self.state = state
            if state not in (State.WAIT, State.REEL):  # high-frequency states stay quiet
                self._publish("state", state.value)

    def _release_mouse_safely(self) -> None:
        if self._input is not None and self._input.mouse_held:
            try:
                self._input.release_mouse()
            except Exception:
                pass

    # ------------------------------------------------------------------ run

    def run(self) -> None:
        grabber = ScreenGrabber()  # mss instances are thread-local
        self._input = InputController(self._tracker, jitter=self.cfg.fishing.action_jitter)
        self._publish("info", "bot started")
        self._set_state(State.FOCUS)

        try:
            while not self._stop_event.is_set():
                if self._toggle_pause.is_set():
                    self._toggle_pause.clear()
                    if self.state != State.PAUSED:
                        self._paused_from = self.state
                        self._set_state(State.PAUSED)
                        self._publish("info", "paused by user")
                    else:
                        self._set_state(State.CAST)
                        self._publish("info", "resumed")
                if self._stop_event.is_set():
                    break

                self._check_focus()
                handler = {
                    State.FOCUS: self._do_focus,
                    State.PREP: self._do_prep,
                    State.CAST: self._do_cast,
                    State.WAIT: self._do_wait,
                    State.REEL: self._do_reel,
                    State.LOOT: self._do_loot,
                    State.MAINTENANCE: self._do_maintenance,
                    State.RECOVER: self._do_recover,
                    State.PAUSED: self._do_paused,
                }.get(self.state)
                if handler:
                    handler(grabber)
                else:
                    time.sleep(0.1)
                time.sleep(self.cfg.detection.scan_loop_delay)
        except FailsafeError as exc:
            self._publish("error", f"FAILSAFE triggered: {exc} - bot stopped")
        except Exception as exc:
            log.exception("bot crashed")
            self._publish("error", f"bot crashed: {exc!r}")
        finally:
            self._release_mouse_safely()
            self.state = State.IDLE
            self._publish("info", "bot stopped")

    # ------------------------------------------------------------- handlers

    def _check_focus(self) -> None:
        if not self.cfg.fishing.pause_on_focus_lost or self.state == State.PAUSED:
            return
        if self.state in (State.FOCUS,):
            return
        info = self._tracker.refresh()
        if info is None or not is_foreground(info.hwnd):
            self._release_mouse_safely()
            self._paused_from = self.state
            self._set_state(State.PAUSED)
            self._publish("warn", "Roblox lost focus - paused (toggle to resume)")

    def _do_focus(self, grabber: ScreenGrabber) -> None:
        info = self._tracker.refresh()
        if info is None:
            self._publish("warn", "Roblox window not found - waiting")
            time.sleep(1.5)
            return
        if not self.cfg.scan_region.valid():
            self._publish("error", "scan region not calibrated - open the Calibration tab")
            self._stop_event.set()
            return
        focus_window(info.hwnd)
        time.sleep(0.2)
        self._set_state(State.PREP)

    def _do_prep(self, grabber: ScreenGrabber) -> None:
        f = self.cfg.fishing
        if f.equip_rod:
            self._input.press_key(f.rod_key, delay_after=0.3)
        self._set_state(State.CAST)

    def _do_cast(self, grabber: ScreenGrabber) -> None:
        info = self._tracker.refresh()
        if info is None:
            self._set_state(State.FOCUS)
            return
        focus_window(info.hwnd)
        time.sleep(0.15)
        self._input.cast(self.cfg.fishing.cast_hold_duration)
        time.sleep(self.cfg.fishing.post_cast_delay)
        self._black_streak = 0
        self._deadline = time.monotonic() + self.cfg.fishing.recast_timeout
        self._set_state(State.WAIT)

    def _grab_scan(self, grabber: ScreenGrabber) -> np.ndarray | None:
        try:
            return grabber.grab_region(self._tracker, self.cfg.scan_region)
        except Exception as exc:
            log.debug("grab failed: %s", exc)
            return None

    def _maybe_publish_preview(self, frame: np.ndarray, reading) -> None:
        if not self.cfg.ui.live_preview:
            return
        now = time.monotonic()
        if now - self._last_preview_publish < 0.25:
            return
        self._last_preview_publish = now
        annotated = vision.annotate(frame, reading)
        with self._preview_lock:
            self._preview_frame = annotated

    def _do_wait(self, grabber: ScreenGrabber) -> None:
        frame = self._grab_scan(grabber)
        if frame is None:
            return
        det = self.cfg.detection
        self._maybe_publish_preview(frame, None)

        if vision.black_ratio(frame) > det.black_screen_threshold:
            self._black_streak += 1
            if self._black_streak >= 3:
                self._publish("info", "black/loading screen detected - waiting it out")
                self._set_state(State.RECOVER)
            return
        self._black_streak = 0

        if vision.find_bar(frame, det) is not None:
            # Require two consecutive hits so a one-frame flicker doesn't
            # start a reel session against a half-appeared gauge.
            self._bite_streak += 1
            if self._bite_streak >= 2:
                self._bite_streak = 0
                self._reel_started = time.monotonic()
                self._last_frame_time = self._reel_started
                self._last_seen = self._reel_started
                self._reel_frames = 0
                self._reel_on_bar = 0
                self._reel_presses = 0
                self._reel_releases = 0
                self._last_fish_y = None
                self._prev_bar_y = None
                self._bar_vel = 0.0
                self._fish_vel = 0.0
                self._last_overlap_time = None
                self._reel_error_published = False
                self._set_state(State.REEL)
            return
        self._bite_streak = 0

        if time.monotonic() > self._deadline:
            self.stats.record_timeout()
            self._publish("timeout",
                          f"no bite within {self.cfg.fishing.recast_timeout:.0f}s - recasting")
            self._set_state(State.CAST)

    def _end_reel(self, reason: str, warn: bool) -> None:
        """Leave REEL: release the mouse, then judge the outcome.

        A catch is only counted when the fish was on the bar in the last moment
        the gauge was READABLE - GPO also ends the minigame on a miss, which
        must not count as a fish. Freshness is measured against that last good
        frame rather than against now: confirming the gauge is gone takes over
        a second on its own, so a "within 0.8 s of now" test never passes.
        """
        self._release_mouse_safely()
        self._telemetry = None
        self.stats.record_reel()
        overlap_age = (self._last_seen - self._last_overlap_time
                       if self._last_overlap_time is not None else None)
        summary = (f"reel ended ({reason}): {self._reel_frames} readable frames, "
                   f"{self._reel_presses} holds / {self._reel_releases} releases, "
                   f"{100.0 * self._reel_on_bar / max(1, self._reel_frames):.0f}% on the bar, "
                   f"last on-bar {'never' if overlap_age is None else f'{overlap_age:.1f}s'} "
                   f"before the end")

        if reason == "gauge disappeared" and overlap_age is not None and overlap_age <= 0.8:
            self._publish("info", summary)
            self._set_state(State.LOOT)
        elif reason == "gauge disappeared":
            self.stats.record_fail()
            self._publish("warn", summary + " - fish got away (not hooked at the end)")
            self._set_state(State.CAST)
        else:
            self._publish("warn", summary)
            self._set_state(State.CAST)

    def _do_reel(self, grabber: ScreenGrabber) -> None:
        frame = self._grab_scan(grabber)
        if frame is None:
            return
        now = time.monotonic()

        if now - self._reel_started > self.cfg.fishing.reel_max_duration:
            self._end_reel("max duration exceeded", warn=True)
            return

        try:
            reading = vision.find_bar(frame, self.cfg.detection,
                                      prev_fish_y=self._last_fish_y)
        except Exception as exc:
            reading = None
            if not self._reel_error_published:
                self._reel_error_published = True
                log.exception("find_bar failed mid-reel")
                self._publish("error", f"detection error during reel: {exc!r}")
        self._maybe_publish_preview(frame, reading)

        if reading is None:
            # Gone once it has been unreadable for a real 1.2 s. Counting misses
            # and multiplying by scan_loop_delay under-measured it - a grab and
            # detect pass costs more than the sleep alone.
            if now - self._last_seen > 1.2:
                self._end_reel("gauge disappeared", warn=False)
            return
        self._last_seen = now
        self._reel_frames += 1
        prev_fish_y = self._last_fish_y
        self._last_fish_y = reading.seg_center_y
        if reading.overlap:
            self._last_overlap_time = now
            self._reel_on_bar += 1

        # Smoothed velocities in px/s, positive = moving DOWN the gauge. Both
        # come from sampled positions, so they lag reality by a frame or two -
        # which is what the lead times in the controller are sized against.
        dt = now - self._last_frame_time
        self._last_frame_time = now
        if dt > 1e-3:
            if self._prev_bar_y is not None:
                self._bar_vel = (0.7 * self._bar_vel
                                 + 0.3 * (reading.marker_y - self._prev_bar_y) / dt)
            if prev_fish_y is not None:
                self._fish_vel = (0.7 * self._fish_vel
                                  + 0.3 * (reading.seg_center_y - prev_fish_y) / dt)
        self._prev_bar_y = reading.marker_y

        hold = controller.should_hold(reading.marker_y, self._bar_vel,
                                      reading.seg_center_y, self._fish_vel,
                                      self.cfg.controller)
        if hold and not self._input.mouse_held:
            self._input.press_mouse()
            self._reel_presses += 1
        elif not hold and self._input.mouse_held:
            self._input.release_mouse()
            self._reel_releases += 1

        self._telemetry = {
            "bar_y": reading.marker_y, "fish_y": reading.seg_center_y,
            "bar_vel": self._bar_vel, "fish_vel": self._fish_vel,
            "error": reading.marker_y - reading.seg_center_y,
            "hold": hold, "on_bar": reading.overlap,
            "on_bar_pct": 100.0 * self._reel_on_bar / max(1, self._reel_frames),
            "frames": self._reel_frames, "elapsed": now - self._reel_started,
            "bar_size": reading.marker_size,
            "pill": (reading.track_top, reading.track_bottom),
        }

        if self.cfg.ui.live_preview and now - self._reel_last_debug > 0.4:
            self._reel_last_debug = now
            self._publish("debug",
                          f"reel: bar={reading.marker_y:.0f}({self._bar_vel:+.0f}) "
                          f"fish={reading.seg_center_y:.0f}({self._fish_vel:+.0f}) "
                          f"err={reading.marker_y - reading.seg_center_y:+.0f} "
                          f"{'HOLD' if hold else 'drop '} on={int(reading.overlap)}")

    def _do_loot(self, grabber: ScreenGrabber) -> None:
        time.sleep(self.cfg.fishing.loot_delay)
        total = self.stats.record_fish()
        self._catches_since_purchase += 1
        self._catches_since_craft += 1
        self._publish("fish", f"caught fish #{total}", total=total)
        webhook = self.cfg.webhook
        if webhook.log_milestones and webhook.url and total > 0 \
                and total % max(1, webhook.milestone_every) == 0:
            self._publish("milestone", f"milestone: {total} fish this session", total=total)
        self._set_state(State.MAINTENANCE)

    def _do_maintenance(self, grabber: ScreenGrabber) -> None:
        bait = self.cfg.bait
        try:
            # Every task in here clicks menus. The cast aims at wherever the
            # cursor is, so the cursor has to end up back over the water.
            with self._input.preserved_position():
                if (bait.auto_buy
                        and self._catches_since_purchase >= max(1, bait.loops_per_purchase)):
                    self._catches_since_purchase = 0
                    tasks.buy_bait(self._input, bait, self._publish)
                if bait.auto_craft and self._catches_since_craft >= max(1, bait.loops_per_craft):
                    self._catches_since_craft = 0
                    tasks.craft_bait(self._input, bait, self._publish)
                if self.cfg.fruit.auto_store:
                    tasks.store_fruits(self._input, grabber, self._tracker,
                                       self.cfg.fruit, self.cfg.fishing.rod_key,
                                       self._publish)   # (stored, dropped)
        except FailsafeError:
            raise
        except Exception as exc:
            log.exception("maintenance failed")
            self._publish("error", f"maintenance task failed: {exc!r}")
        self._set_state(State.CAST)

    def _do_recover(self, grabber: ScreenGrabber) -> None:
        deadline = time.monotonic() + 30.0
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            frame = self._grab_scan(grabber)
            if frame is not None and \
                    vision.black_ratio(frame) <= self.cfg.detection.black_screen_threshold:
                break
            time.sleep(1.0)
        self._publish("info", "recovered from loading screen")
        self._set_state(State.CAST)

    def _do_paused(self, grabber: ScreenGrabber) -> None:
        time.sleep(0.4)
