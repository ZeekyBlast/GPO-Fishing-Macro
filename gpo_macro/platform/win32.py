"""Windows: user32 through ctypes, mss for the pixels, winsound for the beep."""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes

import numpy as np

from . import Rect, WindowInfo, WindowSystem, _new_mss

log = logging.getLogger("gpo.platform")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Roblox's own top-level window class. A browser tab or a folder called
# Roblox carries the word in its title too; this is how the game outranks them.
GAME_CLASSES = {"windowsclient"}

SW_RESTORE = 9
MOUSEEVENTF_MOVE = 0x0001


class Win32WindowSystem(WindowSystem):
    def __init__(self) -> None:
        self._local = threading.local()

    # ------------------------------------------------------------- windows

    def find_game_window(self) -> WindowInfo | None:
        found: list[tuple[int, WindowInfo]] = []      # (rank, info): 0 beats 1

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def on_window(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            if "roblox" not in title.value.lower():
                return True
            klass = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, klass, 256)
            rect = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                info = WindowInfo(hwnd, title.value, rect.left, rect.top, rect.right, rect.bottom)
                found.append((0 if klass.value.lower() in GAME_CLASSES else 1, info))
            return True

        user32.EnumWindows(on_window, 0)
        if not found:
            return None
        rank, info = min(found, key=lambda f: f[0])
        if rank:
            log.info("no window of class WINDOWSCLIENT; using %r by title", info.title)
        return info

    def window_alive(self, handle: int) -> bool:
        return bool(user32.IsWindow(handle))

    def client_origin(self, handle: int) -> tuple[int, int]:
        rect = wintypes.RECT()
        if not user32.GetClientRect(handle, ctypes.byref(rect)):
            return (0, 0)
        point = wintypes.POINT(0, 0)
        user32.ClientToScreen(handle, ctypes.byref(point))
        return (point.x, point.y)

    def client_size(self, handle: int) -> tuple[int, int]:
        rect = wintypes.RECT()
        if not user32.GetClientRect(handle, ctypes.byref(rect)):
            return (0, 0)
        return (rect.right - rect.left, rect.bottom - rect.top)

    def is_foreground(self, handle: int) -> bool:
        return user32.GetForegroundWindow() == handle

    def focus_window(self, handle: int) -> bool:
        try:
            if user32.IsIconic(handle):
                user32.ShowWindow(handle, SW_RESTORE)
                time.sleep(0.15)
            # Attach thread input so SetForegroundWindow is permitted, then detach.
            foreground = user32.GetForegroundWindow()
            fore_tid = user32.GetWindowThreadProcessId(foreground, None)
            this_tid = kernel32.GetCurrentThreadId()
            if fore_tid != this_tid:
                user32.AttachThreadInput(this_tid, fore_tid, True)
            user32.SetForegroundWindow(handle)
            user32.BringWindowToTop(handle)
            if fore_tid != this_tid:
                user32.AttachThreadInput(this_tid, fore_tid, False)
            time.sleep(0.05)
            return self.is_foreground(handle)
        except Exception:
            return False

    # -------------------------------------------------------------- pixels

    def _sct(self):
        """mss is not thread-safe: one instance per thread."""
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._local.sct = _new_mss()
        return sct

    def grab(self, handle: int, origin: tuple[int, int], region) -> np.ndarray:
        return self._grab_box(origin[0] + region.x1, origin[1] + region.y1,
                              region.width(), region.height())

    def grab_screen(self, rect: Rect) -> np.ndarray:
        return self._grab_box(rect.left, rect.top, rect.width, rect.height)

    def _grab_box(self, left: int, top: int, width: int, height: int) -> np.ndarray:
        if width <= 0 or height <= 0:
            raise ValueError(f"invalid grab box: {width}x{height}")
        shot = self._sct().grab({"left": left, "top": top, "width": width, "height": height})
        return np.asarray(shot)[:, :, :3].copy()     # BGRA -> BGR

    # --------------------------------------------------------------- input

    def move_mouse_relative(self, dx: int, dy: int) -> None:
        user32.mouse_event(MOUSEEVENTF_MOVE, int(dx), int(dy), 0, 0)

    # --------------------------------------------------------------- sound

    def play_alert(self, wav_path: str) -> None:
        import winsound
        if wav_path:
            try:
                winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except Exception:
                log.warning("could not play %s - falling back to beeps", wav_path)
        for freq, duration in ((880, 250), (1100, 250), (880, 250), (1400, 400)):
            try:
                winsound.Beep(freq, duration)
            except Exception:
                break

    def loopback_stream_kwargs(self) -> dict | None:
        """WASAPI loopback: open the default *output* device as an input,
        under the WASAPI host API, with the loopback flag."""
        try:
            import sounddevice as sd
            wasapi = next(i for i, api in enumerate(sd.query_hostapis())
                          if "wasapi" in api["name"].lower())
            output = sd.query_devices(kind="output")
            for index, device in enumerate(sd.query_devices()):
                if (device["hostapi"] == wasapi and device["name"] == output["name"]
                        and device["max_output_channels"] > 0):
                    return {"device": index, "extra_settings": sd.WasapiSettings(loopback=True)}
        except Exception as exc:
            log.warning("no WASAPI loopback device: %s", exc)
        return None
