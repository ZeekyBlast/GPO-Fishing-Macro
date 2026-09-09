"""Thread-safe session statistics and a tiny event bus for UI/webhook fan-out."""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Optional


@dataclass
class Event:
    kind: str            # "fish" | "state" | "warn" | "error" | "fruit" | "info" | "timeout" | ...
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


class EventBus:
    """Publishes events to subscriber callbacks on a dedicated dispatch thread.

    Subscriber exceptions are swallowed (and logged) so a dead GUI or webhook
    can never stall the bot thread.
    """

    def __init__(self) -> None:
        self._queue: "queue.Queue[Optional[Event]]" = queue.Queue()
        self._subscribers: list[Callable[[Event], None]] = []
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def subscribe(self, callback: Callable[[Event], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def publish(self, event: Event) -> None:
        self._queue.put(event)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="event-bus", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        import logging
        log = logging.getLogger("gpo.events")
        while True:
            event = self._queue.get()
            if event is None:
                break
            with self._lock:
                subscribers = list(self._subscribers)
            for cb in subscribers:
                try:
                    cb(event)
                except Exception as exc:
                    log.warning("event subscriber %s failed: %s", getattr(cb, "__name__", cb), exc)


class Stats:
    """Counters safe to read/write from bot, GUI, and webhook threads."""

    def __init__(self, history_minutes: int = 60, event_history: int = 300):
        self._lock = threading.Lock()
        self._start = time.time()
        self._catches: Deque[float] = deque()          # timestamps of catches
        self._fish_total = 0
        self._history_seconds = history_minutes * 60
        self._events: Deque[Event] = deque(maxlen=event_history)
        self._recast_timeouts = 0
        self._reels_completed = 0
        self._fails = 0

    # ---------------------------------------------------------------- writes

    def record_fish(self) -> int:
        now = time.time()
        with self._lock:
            self._fish_total += 1
            self._catches.append(now)
            return self._fish_total

    def record_timeout(self) -> None:
        with self._lock:
            self._recast_timeouts += 1

    def record_reel(self) -> None:
        with self._lock:
            self._reels_completed += 1

    def record_fail(self) -> None:
        with self._lock:
            self._fails += 1

    def add_event(self, event: Event) -> None:
        with self._lock:
            self._events.append(event)

    def reset_session(self) -> None:
        with self._lock:
            self._start = time.time()
            self._catches.clear()
            self._fish_total = 0
            self._recast_timeouts = 0
            self._reels_completed = 0
            self._fails = 0

    # ----------------------------------------------------------------- reads

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            recent = [t for t in self._catches if now - t <= self._history_seconds]
            uptime = max(0.0, now - self._start)
            per_hour = (len(recent) / min(uptime, self._history_seconds) * 3600.0
                        if uptime > 0 else 0.0)
            return {
                "fish_total": self._fish_total,
                "fish_per_hour": per_hour,
                "uptime_seconds": uptime,
                "recast_timeouts": self._recast_timeouts,
                "reels_completed": self._reels_completed,
                "fails": self._fails,
            }

    def recent_events(self, limit: int = 50) -> list[Event]:
        with self._lock:
            return list(self._events)[-limit:]


def format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}"
