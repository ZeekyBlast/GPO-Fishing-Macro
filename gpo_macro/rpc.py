"""Headless engine: commands in, frames out, over whichever transport asks.

Everything that touches the screen, the mouse or OpenCV stays here; a shell
draws and asks. Two shells speak to it:

- the C# window spawns `python main.py --rpc` and uses newline-delimited
  JSON on stdin/stdout (run_rpc below);
- the browser page gets the same frames over Server-Sent Events and sends
  the same commands by POST (web.py).

    shell -> engine   {"id": 7, "cmd": "start"}
    engine -> shell   {"t": "reply",  "id": 7, "ok": true, "result": {...}}
                      {"t": "event",  "kind": "fish", "message": "..."}
                      {"t": "tick",   "state": "reel", "stats": {...}, ...}

The Engine writes frames to a `sink` callable and dispatch() hands the reply
back to the caller, so the transport decides where each goes. In RPC mode
stdout carries the protocol and nothing else: it is captured on startup and
`sys.stdout` is repointed at stderr, so a stray print from any library lands
in the log instead of corrupting a frame.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from . import APP_NAME, __version__, form, theme, vision
from .capture import ScreenGrabber, WindowTracker
from .config import AppConfig, ConfigStore, Region, dict_into_dataclass
from .fisher import FishingBot, State, prepare_cast
from .hotkeys import HotkeyManager
from .input import InputController
from .notify import DiscordNotifier, valid_url
from .sound_alert import SoundListener
from .stats import Event, EventBus, Stats

log = logging.getLogger("gpo.rpc")

TICK_HZ = 10.0
PREVIEW_HZ = 5.0

Sink = Callable[[dict], None]


class StdoutFrames:
    """The RPC transport's sink: one JSON line per frame on the real stdout,
    which is taken over so nothing else can write a partial frame."""

    def __init__(self) -> None:
        self._out = sys.stdout
        self._lock = threading.Lock()
        sys.stdout = sys.stderr   # protocol guard: nothing else may write a frame

    def __call__(self, message: dict) -> None:
        line = json.dumps(message, default=str)
        with self._lock:
            self._out.write(line + "\n")
            self._out.flush()


def _png_base64(frame: np.ndarray) -> str:
    import cv2
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise ValueError("could not encode the frame")
    return base64.b64encode(buf.tobytes()).decode("ascii")


class Engine:
    def __init__(self, store: ConfigStore, sink: Sink | None = None):
        self.store = store
        self.bus = EventBus()
        self.stats = Stats()
        self.notifier = DiscordNotifier(self.bus, self.stats, lambda: self.store.config)
        self.hotkeys = HotkeyManager(lambda: self.store.config.hotkeys,
                                     self._on_toggle, self._on_panic)
        self.sound: SoundListener | None = None
        self.bot: FishingBot | None = None

        self._tracker = WindowTracker()

        self._window_lost = False   # sticky until recheck or restart

        self._no_gauge = False      # sticky until a test passes
        self._grabber: ScreenGrabber | None = None   # lazily made on the command thread
        self._sink: Sink = sink or (lambda message: None)
        self._running = threading.Event()
        self._last_preview = 0.0
        self._capture: np.ndarray | None = None      # the last client-area capture

    @property
    def cfg(self) -> AppConfig:
        return self.store.config

    def grabber(self) -> ScreenGrabber:
        if self._grabber is None:
            self._grabber = ScreenGrabber()
        return self._grabber

    # ----------------------------------------------------------------- output

    def send(self, **message: Any) -> None:
        try:
            self._sink(message)
        except (BrokenPipeError, ValueError, OSError):
            self._running.clear()   # shell went away

    def _on_event(self, event: Event) -> None:
        self.send(t="event", kind=event.kind, message=event.message,
                  data=event.data, ts=event.timestamp, id=event.id)

    # --------------------------------------------------------------- lifecycle

    def start(self) -> None:
        self.bus.subscribe(self._on_event)
        self.bus.start()
        self.notifier.start()
        self.hotkeys.start()
        self._sync_sound()
        self._running.set()
        self.send(**self.hello_frame())
        threading.Thread(target=self._tick_loop, name="rpc-tick", daemon=True).start()

    def hello_frame(self) -> dict[str, Any]:
        """Everything a shell needs before its first tick: the settings schema,
        the config, and the theme. A shell draws state and event colours from
        these rather than keeping its own table, so what a colour *means*
        stays in theme.py. Rebuilt for every client that connects late."""
        return dict(t="hello", app=APP_NAME, version=__version__,
                    schema=form.schema(), notes=form.SECTION_NOTES,
                    config=self.cfg.to_dict(), defaults=AppConfig().to_dict(),
                    settings_path=str(self.store.path),
                    state_colors=theme.STATE_COLORS,
                    event_styles={kind: {"mark": mark, "colour": colour}
                                  for kind, (mark, colour) in theme.EVENT_STYLES.items()},
                    theme={"palette": dict(theme.PALETTE), "mono": theme.MONO,
                           "sans": theme.SANS, "sizes": dict(theme.SIZES)},
                    templates=self._templates(), platform=sys.platform)

    def _templates(self) -> dict[str, bool]:
        """Which snipped templates exist on disk. The browser cannot look."""
        cfg = self.cfg
        return {"fruit": Path(cfg.fruit.template_path).is_file(),
                "prompt": Path(cfg.bait.prompt_template).is_file(),
                "tag": Path(cfg.bait.tag_template).is_file()}

    def recent_event_frames(self, limit: int = 100) -> list[dict[str, Any]]:
        """The tail of the log, as event frames, for a shell that joins late."""
        return [dict(t="event", kind=e.kind, message=e.message, data=e.data,
                     ts=e.timestamp, id=e.id) for e in self.stats.recent_events(limit)]

    def shutdown(self) -> None:
        self._running.clear()
        try:
            if self.bot and self.bot.is_alive():
                self.bot.stop()
                self.bot.join(timeout=3)
            self.hotkeys.stop()
            self.notifier.stop()
            if self.sound:
                self.sound.stop()
            self.store.update()
            self.bus.stop()
        except Exception:
            log.exception("shutdown failed")

    # Hotkeys fire on pynput's thread; they act directly and the tick reports it.
    def _on_toggle(self) -> None:
        self.cmd_toggle({})

    def _on_panic(self) -> None:
        self.cmd_panic({})

    def _sync_sound(self) -> None:
        want = self.cfg.sound.enabled
        alive = self.sound is not None and self.sound.is_alive()
        if want and not alive:
            self.sound = SoundListener(self.cfg.sound, self.bus)
            self.sound.start()
        elif not want and self.sound is not None:
            self.sound.stop()
            self.sound = None

    # -------------------------------------------------------------- tick loop

    def _tick_loop(self) -> None:
        period = 1.0 / TICK_HZ
        while self._running.is_set():
            try:
                self.send(**self._tick())
            except Exception:
                log.exception("tick failed")
            time.sleep(period)

    def _tick(self) -> dict[str, Any]:
        bot = self.bot
        running = bot is not None and bot.is_alive()
        state = bot.state.value if bot is not None and running else State.IDLE.value
        info = self._tracker.refresh()
        status = self.notifier.status()
        if self.bot and not running and self.bot.stop_reason == "window_lost":
            self._window_lost = True
            self.bot.stop_reason = ""
        payload: dict[str, Any] = {
            "t": "tick",
            "running": running,
            "state": state,
            "stats": self.stats.snapshot(),
            "telemetry": bot.telemetry() if bot is not None and running else None,
            "window": ({"title": info.title, "left": info.left, "top": info.top,
                        "width": info.right - info.left, "height": info.bottom - info.top}
                       if info else None),
            "region_valid": self.cfg.scan_region.valid(),
            "fault": self._fault(info, status),
            "webhook": {"configured": status.configured, "sent": status.sent,
                        "failed": status.failed, "dropped": status.dropped,
                        "suppressed": status.suppressed, "queued": status.queued,
                        "last_result": status.last_result},
        }
        preview = self._preview_png(running)
        if preview is not None:
            payload["preview"] = preview
        return payload

    def _fault(self, info, status) -> str | None:
        """One fault at a time, worst first. A fault never clears itself
        silently: window_lost waits for a recheck or a restart, no_gauge for a
        test that passes, and the live ones for the state to actually change."""
        if self._window_lost:
            return "window_lost"
        if info is None:
            return "no_window"
        if self._no_gauge:
            return "no_gauge"
        if status.configured and status.failed > 0 \
                and not status.last_result.startswith("delivered"):
            return "webhook_failed"
        return None

    def _preview_png(self, running: bool) -> str | None:
        now = time.monotonic()
        if not (running and self.cfg.ui.live_preview):
            return None
        if now - self._last_preview < 1.0 / PREVIEW_HZ:
            return None
        frame = self.bot.preview_frame() if self.bot else None
        if frame is None:
            return None
        self._last_preview = now
        import cv2
        h, w = frame.shape[:2]
        scale = max(0.12, min(self.cfg.ui.preview_scale, 520.0 / max(1, w)))
        small = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))))
        ok, buf = cv2.imencode(".png", small)
        return base64.b64encode(buf.tobytes()).decode("ascii") if ok else None

    # --------------------------------------------------------------- commands

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        """Run one command and return its reply frame. The transport delivers
        it: the stdin loop writes it out, the web server answers the POST."""
        name = str(request.get("cmd", ""))
        handler = getattr(self, "cmd_" + name, None) if name.isidentifier() else None
        if handler is None:
            return dict(t="reply", id=request.get("id"), ok=False,
                        error="unknown command: " + name)
        try:
            result = handler(request) or {}
            return dict(t="reply", id=request.get("id"), ok=True, result=result)
        except Exception as exc:
            log.exception("command %s failed", name)
            return dict(t="reply", id=request.get("id"), ok=False, error=str(exc))

    def cmd_ping(self, _req) -> dict:
        return {"pong": time.time()}

    def cmd_start(self, _req) -> dict:
        if self.bot and self.bot.is_alive():
            return {"started": False, "reason": "already running"}
        if not self.cfg.scan_region.valid():
            return {"started": False, "reason": "scan region not calibrated"}
        self._window_lost = False
        self.bot = FishingBot(self.cfg, self.stats, self.bus)
        self.bot.start()
        return {"started": True}

    def cmd_stop(self, _req) -> dict:
        if self.bot and self.bot.is_alive():
            self.bot.stop()
            return {"stopped": True}
        return {"stopped": False}

    def cmd_toggle(self, req) -> dict:
        """The hotkey: start, or stop - and a paused bot resumes, which is what
        the dashboard's "press f6 to resume" promises. Stop stays a separate
        command so the button that says Stop always means it."""
        if self.bot and self.bot.is_alive():
            if self.bot.state == State.PAUSED:
                return self.cmd_pause(req)
            return self.cmd_stop(req)
        return self.cmd_start(req)

    def cmd_pause(self, _req) -> dict:
        if self.bot and self.bot.is_alive():
            self.bot.request_pause_toggle()
            return {"toggled": True}
        return {"toggled": False}

    def cmd_panic(self, _req) -> dict:
        if self.bot and self.bot.is_alive():
            self.bot.panic()
            self.bus.publish(Event(kind="error", message="PANIC - stopped, mouse released"))
            return {"panicked": True}
        return {"panicked": False}

    def cmd_reset_stats(self, _req) -> dict:
        self.stats.reset_session()
        return {"reset": True}

    def cmd_get_config(self, _req) -> dict:
        return {"config": self.cfg.to_dict(), "templates": self._templates()}

    def cmd_set_config(self, req) -> dict:
        patch = req.get("patch") or {}
        if not isinstance(patch, dict):
            raise ValueError("patch must be an object")
        self._check_hotkeys(patch.get("hotkeys"))
        self._one_bait_mode(patch.get("bait"))
        dict_into_dataclass(self.cfg, patch)
        self.store.update()
        self.hotkeys.restart()
        self.notifier.restart_periodic()
        self._sync_sound()
        return {"config": self.cfg.to_dict(), "templates": self._templates()}

    def _check_hotkeys(self, hotkeys: Any) -> None:
        """Refuse a key the listener cannot bind, or both actions on one key,
        before anything is written: a saved pair that never registers again
        is a settings file that has to be edited by hand."""
        if not isinstance(hotkeys, dict):
            return
        from .input import to_pynput_hotkey
        start = str(hotkeys.get("start_stop", self.cfg.hotkeys.start_stop))
        panic = str(hotkeys.get("panic", self.cfg.hotkeys.panic))
        try:
            same = to_pynput_hotkey(start) == to_pynput_hotkey(panic)
        except ValueError as exc:
            raise ValueError(f"hotkey: {exc}") from None
        if same:
            if "start_stop" in hotkeys and "panic" not in hotkeys:
                raise ValueError(f"{start} is already the panic key")
            if "panic" in hotkeys and "start_stop" not in hotkeys:
                raise ValueError(f"{panic} is already the toggle key")
            raise ValueError("the toggle and panic keys must differ")

    def _one_bait_mode(self, bait: Any) -> None:
        """Craft or buy, never both. The one just switched on wins; the form
        shows the whole bait section, so the loser is whichever was already on."""
        if not isinstance(bait, dict) or not (bait.get("auto_buy") and bait.get("auto_craft")):
            return
        loser = "auto_buy" if self.cfg.bait.auto_buy else "auto_craft"
        bait[loser] = False
        self.bus.publish(Event(kind="warn", message="auto-buy and auto-craft are one or "
                               f"the other - turned {loser.replace('_', '-')} off"))

    def cmd_window(self, _req) -> dict:
        """Where the Roblox client area sits, so the shell can turn a screen
        click into the window-relative coordinates every setting is stored in."""
        info = self._tracker.refresh()
        if info is None:
            return {"found": False, "origin_x": 0, "origin_y": 0}
        ox, oy = self._tracker.origin
        cw, ch = self._tracker.client_size()
        return {"found": True, "title": info.title, "origin_x": ox, "origin_y": oy,
                "client_width": cw, "client_height": ch,
                "left": info.left, "top": info.top,
                "width": info.right - info.left, "height": info.bottom - info.top}

    def cmd_recheck_window(self, _req) -> dict:
        """The window_lost recovery: found again means the fault is over."""
        info = self._tracker.refresh()
        if info is not None:
            self._window_lost = False
        return {"found": info is not None}

    def cmd_auto_calibrate(self, _req) -> dict:
        result = vision.auto_calibrate(self.grabber(), self._tracker, self.cfg.detection)
        self._no_gauge = result.region is None
        if result.region is not None:
            self.cfg.scan_region = result.region
            self.store.update()
        region = self.cfg.scan_region
        return {"message": result.message, "confidence": result.confidence,
                "notes": list(result.notes),
                "region": ({"x1": region.x1, "y1": region.y1,
                            "x2": region.x2, "y2": region.y2}
                           if result.region is not None else None)}

    def cmd_test_cast(self, _req) -> dict:
        """One cast with the configured hold and aim, so the hold can be tuned
        by watching where the bobber lands. GPO has no charge meter to read."""
        if self.bot and self.bot.is_alive():
            raise ValueError("stop the bot first")
        info = self._tracker.refresh()
        if info is None:
            raise ValueError("no Roblox window")
        f = self.cfg.fishing
        input_ctl = InputController(self._tracker, jitter=0.0)
        self._tracker.system.focus_window(info.hwnd)
        time.sleep(0.3)
        if f.equip_rod and not f.equip_every_cast:
            input_ctl.press_key(f.rod_key, delay_after=0.3)
        prepare_cast(input_ctl, self.cfg, lambda *a, **k: None)
        input_ctl.cast(f.cast_hold_duration)
        return {"hold": f.cast_hold_duration, "aimed": bool(f.cast_point),
                "point": list(f.cast_point)}

    def cmd_test_detection(self, _req) -> dict:
        import cv2
        if not self.cfg.scan_region.valid():
            raise ValueError("set a scan region first")
        frame = self.grabber().grab_region(self._tracker, self.cfg.scan_region)
        reading = vision.find_bar(frame, self.cfg.detection)
        self._no_gauge = reading is None
        annotated = vision.annotate(frame, reading)
        out = Path("test_detection.png").resolve()
        cv2.imwrite(str(out), annotated)
        if reading is not None:
            detail = (f"bar_y={reading.marker_y:.0f} fish_y={reading.seg_center_y:.0f} "
                      f"overlap={reading.overlap}")
        else:
            detail = "no gauge"
        # The file is what the C# shell reads; the browser gets the pixels inline.
        return {"path": str(out), "detail": detail, "found": reading is not None,
                "png": _png_base64(annotated)}

    def cmd_capture(self, _req) -> dict:
        """The game's client area, as a PNG, for the browser to pick on.

        Every coordinate the page reads off this picture is window-relative
        by construction, which is what every setting stores. Where grabs come
        from the screen (Windows), the game is brought to the front first so
        the browser is not what gets captured; on X11 the window's own pixels
        come through regardless. The frame is kept so a colour sample or a
        template snip reads the very pixels the user clicked on."""
        info = self._tracker.refresh()
        if info is None:
            raise ValueError("no Roblox window - launch GPO first")
        if sys.platform == "win32":
            self._tracker.system.focus_window(info.hwnd)
            time.sleep(0.25)
        frame = self.grabber().grab_client(self._tracker)
        self._capture = frame
        height, width = frame.shape[:2]
        return {"png": _png_base64(frame), "width": int(width), "height": int(height),
                "origin_x": self._tracker.origin[0], "origin_y": self._tracker.origin[1]}

    def _capture_crop(self, region: Region) -> np.ndarray:
        """A box out of the last capture, for from_capture commands."""
        if self._capture is None:
            raise ValueError("nothing captured yet")
        height, width = self._capture.shape[:2]
        x1, y1 = max(0, region.x1), max(0, region.y1)
        x2, y2 = min(width, region.x2), min(height, region.y2)
        if x2 <= x1 or y2 <= y1:
            raise ValueError("that box is outside the capture")
        return self._capture[y1:y2, x1:x2]

    def cmd_sample_color(self, req) -> dict:
        """Median RGB of a 7x7 patch at a window-relative point - live, or
        from the last capture when the page says from_capture."""
        x, y = int(req["x"]), int(req["y"])
        patch = Region(x - 3, y - 3, x + 4, y + 4)
        if req.get("from_capture"):
            frame = self._capture_crop(patch)
        else:
            frame = self.grabber().grab_region(self._tracker, patch)
        med = np.median(frame.reshape(-1, 3), axis=0).astype(int)
        rgb = [int(med[2]), int(med[1]), int(med[0])]        # BGR -> RGB
        attr = req.get("attr")
        if attr in ("bar_blue", "track_gray"):
            setattr(self.cfg.detection, attr, rgb)
            self.store.update()
        return {"rgb": rgb, "attr": attr}

    def cmd_save_template(self, req) -> dict:
        import cv2

        from .tasks import reset_template_cache
        region = Region(int(req["x1"]), int(req["y1"]), int(req["x2"]), int(req["y2"]))
        if not region.valid():
            raise ValueError("empty region")
        if req.get("from_capture"):
            frame = self._capture_crop(region)
        else:
            frame = self.grabber().grab_region(self._tracker, region)
        path = Path(req.get("path") or self.cfg.fruit.template_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), frame)
        reset_template_cache()
        return {"path": str(path), "width": region.width(), "height": region.height()}

    def cmd_webhook_test(self, _req) -> dict:
        problem = self.notifier.send_test()
        return {"problem": problem, "ok": not problem}

    def cmd_validate_webhook(self, req) -> dict:
        return {"valid": valid_url(str(req.get("url", "")).strip())}

    def cmd_shutdown(self, _req) -> dict:
        self._running.clear()
        return {"bye": True}


def run_rpc(store: ConfigStore) -> int:
    engine = Engine(store, sink=StdoutFrames())
    engine.start()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                engine.send(t="reply", id=None, ok=False, error="bad JSON: " + str(exc))
                continue
            engine.send(**engine.dispatch(request))
            if not engine._running.is_set():
                break
    except KeyboardInterrupt:
        pass
    finally:
        engine.shutdown()
    return 0
