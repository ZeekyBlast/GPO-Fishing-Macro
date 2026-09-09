"""Global hotkeys via pynput.GlobalHotKeys, rebindable at runtime."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from pynput.keyboard import GlobalHotKeys

from .config import HotkeysConfig
from .input import to_pynput_hotkey

log = logging.getLogger("gpo.hotkeys")


class HotkeyManager:
    def __init__(self, get_config: Callable[[], HotkeysConfig],
                 on_toggle: Callable[[], None],
                 on_panic: Callable[[], None]):
        self._get_config = get_config
        self._on_toggle = on_toggle
        self._on_panic = on_panic
        self._listener: Optional[GlobalHotKeys] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self.restart()

    def restart(self) -> None:
        with self._lock:
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:
                    pass
                self._listener = None
            cfg = self._get_config()
            mapping = {
                to_pynput_hotkey(cfg.start_stop): self._safe(self._on_toggle, "toggle"),
                to_pynput_hotkey(cfg.panic): self._safe(self._on_panic, "panic"),
            }
            try:
                self._listener = GlobalHotKeys(mapping)
                self._listener.daemon = True
                self._listener.start()
                log.info("hotkeys active: toggle=%s panic=%s", cfg.start_stop, cfg.panic)
            except Exception as exc:
                log.error("failed to register hotkeys %s/%s: %s",
                          cfg.start_stop, cfg.panic, exc)

    def stop(self) -> None:
        with self._lock:
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:
                    pass
                self._listener = None

    @staticmethod
    def _safe(fn: Callable[[], None], label: str) -> Callable[[], None]:
        def wrapped():
            try:
                fn()
            except Exception:
                log.exception("hotkey %s handler failed", label)
        return wrapped
