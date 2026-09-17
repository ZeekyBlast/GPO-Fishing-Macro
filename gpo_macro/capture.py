"""Window tracking and screen capture, on top of the platform's WindowSystem.

Every grab takes a window-relative Region and hands it to the window system
together with the client origin and the window handle. The user can move
the window without breaking calibration, and the X11 backend can read the
window itself where the screen has nothing to give.

Which OS this is never appears here: the WindowTracker is given a
WindowSystem at construction (the tests hand it a fake) and defaults to the
one for this platform.
"""

from __future__ import annotations

import numpy as np

from .config import Region
from .platform import WindowInfo, WindowSystem, default, monitor_containing

__all__ = ["WindowInfo", "WindowTracker", "ScreenGrabber"]


class WindowTracker:
    """Caches the Roblox window handle; re-resolves if it disappears or moves."""

    def __init__(self, system: WindowSystem | None = None) -> None:
        self.system = system or default()
        self._info: WindowInfo | None = None
        self._origin: tuple[int, int] = (0, 0)

    def refresh(self) -> WindowInfo | None:
        if self._info is None or not self.system.window_alive(self._info.hwnd):
            self._info = self.system.find_game_window()
        if self._info is not None:
            self._origin = self.system.client_origin(self._info.hwnd)
        return self._info

    @property
    def info(self) -> WindowInfo | None:
        """The window as of the last refresh, or None."""
        return self._info

    @property
    def handle(self) -> int | None:
        return None if self._info is None else self._info.hwnd

    @property
    def origin(self) -> tuple[int, int]:
        return self._origin

    def client_size(self) -> tuple[int, int]:
        return (0, 0) if self._info is None else self.system.client_size(self._info.hwnd)

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        """Translate a window-relative point to absolute screen coordinates."""
        return (self._origin[0] + x, self._origin[1] + y)


class ScreenGrabber:
    """Grabs window-relative regions through the window system. Safe to share
    between threads: whatever is not is kept thread-local inside the backend."""

    def __init__(self, system: WindowSystem | None = None) -> None:
        self.system = system or default()

    def grab_region(self, tracker: WindowTracker, region: Region) -> np.ndarray:
        """Grab a window-relative Region. Returns BGR ndarray of shape (h, w, 3)."""
        if not region.valid():
            raise ValueError(f"invalid grab region: {region}")
        handle = tracker.handle
        if handle is None:
            raise ValueError("no Roblox window")
        return self.system.grab(handle, tracker.origin, region)

    def grab_client(self, tracker: WindowTracker) -> np.ndarray:
        """The whole Roblox client area, so matches come back window-relative."""
        if tracker.refresh() is None:
            raise ValueError("no Roblox window")
        width, height = tracker.client_size()
        return self.grab_region(tracker, Region(0, 0, width, height))

    def grab_full(self, tracker: WindowTracker | None = None) -> np.ndarray:
        """The monitor the window is on, or the first one. Diagnostics only:
        the bot itself never needs more than the window."""
        monitors = self.system.monitors()
        if not monitors:
            raise ValueError("no monitors found")
        rect = monitors[0]
        info = tracker.info if tracker is not None else None
        if info is not None:
            rect = monitor_containing(monitors, (info.left + info.right) // 2,
                                      (info.top + info.bottom) // 2) or rect
        return self.system.grab_screen(rect)
