"""Test doubles shared by the self-checks."""

from __future__ import annotations

import numpy as np

from gpo_macro.config import Region
from gpo_macro.platform import Rect, WindowInfo, WindowSystem


class FakeWindowSystem(WindowSystem):
    """A window at a known place on a known set of monitors, with a frame of
    pixels behind it. Records what the engine asked of it. Subclassing the
    real interface means a method added there breaks this fake loudly."""

    def __init__(self, window: WindowInfo | None = None,
                 monitors: list[Rect] | None = None,
                 frame: np.ndarray | None = None):
        self.window = window
        self.alive = window is not None
        self._monitors = list(monitors or [Rect(0, 0, 1920, 1080)])
        self.frame = frame
        self.grabs: list[tuple[int, tuple[int, int], Region]] = []
        self.screen_grabs: list[Rect] = []
        self.focus_calls: list[int] = []
        self.moves: list[tuple[int, int]] = []
        self.alerts: list[str] = []

    def find_game_window(self):
        return self.window if self.alive else None

    def window_alive(self, handle):
        return self.alive and self.window is not None and handle == self.window.hwnd

    def client_origin(self, handle):
        return (self.window.left, self.window.top)

    def client_size(self, handle):
        return (self.window.right - self.window.left, self.window.bottom - self.window.top)

    def is_foreground(self, handle):
        return bool(self.focus_calls)

    def focus_window(self, handle):
        self.focus_calls.append(handle)
        return True

    def grab(self, handle, origin, region):
        self.grabs.append((handle, origin, region))
        if self.frame is not None:
            return self.frame[region.y1:region.y2, region.x1:region.x2].copy()
        return np.zeros((region.height(), region.width(), 3), np.uint8)

    def grab_screen(self, rect):
        self.screen_grabs.append(rect)
        return np.zeros((rect.height, rect.width, 3), np.uint8)

    def monitors(self):
        return list(self._monitors)

    def move_mouse_relative(self, dx, dy):
        self.moves.append((dx, dy))

    def play_alert(self, wav_path):
        self.alerts.append(wav_path)

    def loopback_stream_kwargs(self):
        return None
