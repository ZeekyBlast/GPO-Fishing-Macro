"""Headless engine driven over stdin/stdout with newline-delimited JSON.

The C# shell (ui-csharp/) spawns `python main.py --rpc` and is the only UI.
Everything that touches the screen, the mouse or OpenCV stays here; the shell
draws and asks.

    shell -> engine   {"id": 7, "cmd": "start"}
    engine -> shell   {"t": "reply",  "id": 7, "ok": true, "result": {...}}
                      {"t": "event",  "kind": "fish", "message": "..."}
                      {"t": "tick",   "state": "reel", "stats": {...}, ...}

stdout carries the protocol and nothing else: it is captured on startup and
`sys.stdout` is repointed at stderr, so a stray print from any library lands in
the log instead of corrupting a frame.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import APP_NAME, __version__
from . import form
from . import theme
from . import vision
from .capture import ScreenGrabber, WindowTracker, client_size
from .config import AppConfig, ConfigStore, Region, dict_into_dataclass
from .fisher import FishingBot, State
from .hotkeys import HotkeyManager
from .notify import DiscordNotifier, valid_url
from .sound_alert import SoundListener
from .stats import Event, EventBus, Stats

log = logging.getLogger("gpo.rpc")

TICK_HZ = 10.0
PREVIEW_HZ = 5.0


class Engine:
    def __init__(self, store: ConfigStore):
        self.store = store
        self.bus = EventBus()
        self.stats = Stats()
        self.notifier = DiscordNotifier(self.bus, self.stats, lambda: self.store.config)
        self.hotkeys = HotkeyManager(lambda: self.store.config.hotkeys,
                                     self._on_toggle, self._on_panic)
        self.sound: Optional[SoundListener] = None
        self.bot: Optional[FishingBot] = None

        self._tracker = WindowTracker()
        self._grabber: Optional[ScreenGrabber] = None   # lazily made on the command thread
        self._out = sys.stdout
        self._out_lock = threading.Lock()
        self._running = threading.Event()
        self._last_preview = 0.0

        sys.stdout = sys.stderr   # protocol guard: nothing else may write a frame

    @property
    def cfg(self) -> AppConfig:
        return self.store.config

    def grabber(self) -> ScreenGrabber:
        if self._grabber is None:
            self._grabber = ScreenGrabber()
        return self._grabber

    # ----------------------------------------------------------------- output

    def send(self, **message: Any) -> None:
        line = json.dumps(message, default=str)
        with self._out_lock:
            try:
                self._out.write(line + "\n")
                self._out.flush()
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
        # The shell draws state and event colours from these rather than
        # keeping its own table: what a colour *means* stays in theme.py.
        self.send(t="hello", app=APP_NAME, version=__version__,
                  schema=form.schema(), notes=form.SECTION_NOTES,
                  config=self.cfg.to_dict(), settings_path=str(self.store.path),
                  state_colors=theme.STATE_COLORS,
                  event_styles={kind: {"mark": mark, "colour": colour}
                                for kind, (mark, colour) in theme.EVENT_STYLES.items()})
        threading.Thread(target=self._tick_loop, name="rpc-tick", daemon=True).start()

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
        running = bool(self.bot and self.bot.is_alive())
        state = self.bot.state.value if running else State.IDLE.value
        info = self._tracker.refresh()
        status = self.notifier.status()
        payload: dict[str, Any] = {
            "t": "tick",
            "running": running,
            "state": state,
            "stats": self.stats.snapshot(),
            "telemetry": self.bot.telemetry() if running else None,
            "window": ({"title": info.title, "left": info.left, "top": info.top,
                        "width": info.right - info.left, "height": info.bottom - info.top}
                       if info else None),
            "region_valid": self.cfg.scan_region.valid(),
            "webhook": {"configured": status.configured, "sent": status.sent,
                        "failed": status.failed, "dropped": status.dropped,
                        "suppressed": status.suppressed, "queued": status.queued,
                        "last_result": status.last_result},
        }
        preview = self._preview_png(running)
        if preview is not None:
            payload["preview"] = preview
        return payload

    def _preview_png(self, running: bool) -> Optional[str]:
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
        return base64.b64encode(buf).decode("ascii") if ok else None

    # --------------------------------------------------------------- commands

    def dispatch(self, request: dict[str, Any]) -> None:
        name = str(request.get("cmd", ""))
        handler = getattr(self, "cmd_" + name, None) if name.isidentifier() else None
        if handler is None:
            self.send(t="reply", id=request.get("id"), ok=False,
                      error="unknown command: " + name)
            return
        try:
            result = handler(request) or {}
            self.send(t="reply", id=request.get("id"), ok=True, result=result)
        except Exception as exc:
            log.exception("command %s failed", name)
            self.send(t="reply", id=request.get("id"), ok=False, error=str(exc))

    def cmd_ping(self, _req) -> dict:
        return {"pong": time.time()}

    def cmd_start(self, _req) -> dict:
        if self.bot and self.bot.is_alive():
            return {"started": False, "reason": "already running"}
        if not self.cfg.scan_region.valid():
            return {"started": False, "reason": "scan region not calibrated"}
        self.bot = FishingBot(self.cfg, self.stats, self.bus)
        self.bot.start()
        return {"started": True}

    def cmd_stop(self, _req) -> dict:
        if self.bot and self.bot.is_alive():
            self.bot.stop()
            return {"stopped": True}
        return {"stopped": False}

    def cmd_toggle(self, req) -> dict:
        if self.bot and self.bot.is_alive():
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
        return {"config": self.cfg.to_dict()}

    def cmd_set_config(self, req) -> dict:
        patch = req.get("patch") or {}
        if not isinstance(patch, dict):
            raise ValueError("patch must be an object")
        dict_into_dataclass(self.cfg, patch)
        self.store.update()
        self.hotkeys.restart()
        self.notifier.restart_periodic()
        self._sync_sound()
        return {"config": self.cfg.to_dict()}

    def cmd_window(self, _req) -> dict:
        """Where the Roblox client area sits, so the shell can turn a screen
        click into the window-relative coordinates every setting is stored in."""
        info = self._tracker.refresh()
        if info is None:
            return {"found": False, "origin_x": 0, "origin_y": 0}
        ox, oy = self._tracker.origin
        cw, ch = client_size(info.hwnd)
        return {"found": True, "title": info.title, "origin_x": ox, "origin_y": oy,
                "client_width": cw, "client_height": ch,
                "left": info.left, "top": info.top,
                "width": info.right - info.left, "height": info.bottom - info.top}

    def cmd_auto_calibrate(self, _req) -> dict:
        result = vision.auto_calibrate(self.grabber(), self._tracker, self.cfg.detection)
        if result.region is not None:
            self.cfg.scan_region = result.region
            self.store.update()
        region = self.cfg.scan_region
        return {"message": result.message, "confidence": result.confidence,
                "notes": list(result.notes),
                "region": ({"x1": region.x1, "y1": region.y1,
                            "x2": region.x2, "y2": region.y2}
                           if result.region is not None else None)}

    def cmd_test_detection(self, _req) -> dict:
        import cv2
        if not self.cfg.scan_region.valid():
            raise ValueError("set a scan region first")
        frame = self.grabber().grab_region(self._tracker, self.cfg.scan_region)
        reading = vision.find_bar(frame, self.cfg.detection)
        out = Path("test_detection.png").resolve()
        cv2.imwrite(str(out), vision.annotate(frame, reading))
        if reading is not None:
            detail = ("bar_y=%.0f fish_y=%.0f overlap=%s"
                      % (reading.marker_y, reading.seg_center_y, reading.overlap))
        else:
            detail = "no gauge"
        return {"path": str(out), "detail": detail, "found": reading is not None}

    def cmd_sample_color(self, req) -> dict:
        """Median RGB of a 7x7 patch at a window-relative point."""
        import numpy as np
        x, y = int(req["x"]), int(req["y"])
        frame = self.grabber().grab_region(self._tracker, Region(x - 3, y - 3, x + 4, y + 4))
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
    engine = Engine(store)
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
            engine.dispatch(request)
            if not engine._running.is_set():
                break
    except KeyboardInterrupt:
        pass
    finally:
        engine.shutdown()
    return 0
