"""Screen capture and Roblox window management (Windows, stdlib ctypes + mss).

Every grab takes a window-relative Region and translates it using the Roblox
client-area origin, so the user can move/resize the window without breaking
calibration.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import time
from dataclasses import dataclass
from typing import Optional

import mss
import numpy as np

from .config import Region

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    left: int
    top: int
    right: int
    bottom: int

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


def find_roblox_window() -> Optional[WindowInfo]:
    """Return the first visible top-level window whose title contains 'roblox'."""
    result: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def on_window(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if "roblox" in buf.value.lower():
            rect = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                result.append(
                    WindowInfo(hwnd, buf.value, rect.left, rect.top, rect.right, rect.bottom)
                )
                return False  # stop enumeration
        return True

    user32.EnumWindows(on_window, 0)
    return result[0] if result else None


def client_origin(hwnd: int) -> tuple[int, int]:
    """Absolute screen coordinates of the window's client-area top-left."""
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return (0, 0)
    point = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    return (point.x, point.y)


def client_size(hwnd: int) -> tuple[int, int]:
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return (0, 0)
    return (rect.right - rect.left, rect.bottom - rect.top)


def is_foreground(hwnd: int) -> bool:
    return user32.GetForegroundWindow() == hwnd


def focus_window(hwnd: int) -> bool:
    """Bring the window to the foreground. Returns True on success."""
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.15)
        # Attach thread input so SetForegroundWindow is permitted, then detach.
        foreground = user32.GetForegroundWindow()
        fore_tid = user32.GetWindowThreadProcessId(foreground, None)
        this_tid = kernel32.GetCurrentThreadId()
        if fore_tid != this_tid:
            user32.AttachThreadInput(this_tid, fore_tid, True)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        if fore_tid != this_tid:
            user32.AttachThreadInput(this_tid, fore_tid, False)
        time.sleep(0.05)
        return is_foreground(hwnd)
    except Exception:
        return False


class WindowTracker:
    """Caches the Roblox window handle; re-resolves if it disappears or moves."""

    def __init__(self) -> None:
        self._info: Optional[WindowInfo] = None
        self._origin: tuple[int, int] = (0, 0)

    def refresh(self) -> Optional[WindowInfo]:
        if self._info is None or not user32.IsWindow(self._info.hwnd):
            self._info = find_roblox_window()
        if self._info is not None:
            self._origin = client_origin(self._info.hwnd)
        return self._info

    @property
    def origin(self) -> tuple[int, int]:
        return self._origin

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        """Translate a window-relative point to absolute screen coordinates."""
        return (self._origin[0] + x, self._origin[1] + y)

    def region_to_screen(self, region: Region) -> dict[str, int]:
        ox, oy = self._origin
        return {"left": ox + region.x1, "top": oy + region.y1,
                "width": region.width(), "height": region.height()}


class ScreenGrabber:
    """mss wrapper. Create one instance per thread (mss is not thread-safe)."""

    def __init__(self) -> None:
        self._sct = mss.mss()

    def grab_region(self, tracker: WindowTracker, region: Region) -> np.ndarray:
        """Grab a window-relative Region. Returns BGR ndarray of shape (h, w, 3)."""
        box = tracker.region_to_screen(region)
        if box["width"] <= 0 or box["height"] <= 0:
            raise ValueError(f"invalid grab region: {region}")
        shot = self._sct.grab(box)
        frame = np.asarray(shot)[:, :, :3]  # BGRA -> BGR
        return frame.copy()

    def grab_client(self, tracker: WindowTracker) -> np.ndarray:
        """The whole Roblox client area, so matches come back window-relative."""
        info = tracker.refresh()
        if info is None:
            raise ValueError("no Roblox window")
        width, height = client_size(info.hwnd)
        return self.grab_region(tracker, Region(0, 0, width, height))

    def grab_full(self) -> np.ndarray:
        monitor = self._sct.monitors[1]  # primary monitor
        shot = self._sct.grab(monitor)
        return np.asarray(shot)[:, :, :3].copy()
