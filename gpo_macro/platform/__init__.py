"""The window manager, the screen, the pointer and the speaker, as this OS does them.

Everything the engine does that is not pixels-in, clicks-out is the same on
every platform: find the game's window, read where its client area sits,
grab a window-relative box of it, nudge the mouse, make a noise. Those are
the methods of WindowSystem, and there is one implementation per OS:

    win32.py   Win32 through ctypes, mss for the pixels
    x11.py     X11 through python-xlib (XWayland included), XGetImage for the pixels

The rest of the engine never imports either. It asks a WindowTracker for
its `system`, and a WindowTracker takes one at construction, defaulting to
`default()`; the tests hand it a fake instead. Anything that is not
thread-safe - an mss instance, an X Display - is kept thread-local inside
the backend, so one WindowSystem serves the bot thread and the shell's
command thread alike.

Under Wayland there is no window enumeration, no way to read another
window's pixels and no synthetic input, so the Linux backend needs the game
in an X11 window: XWayland counts, a native Wayland surface does not.
"""

from __future__ import annotations

import sys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ..config import Region


@dataclass
class WindowInfo:
    """The game's top-level window: its handle, title and outer rectangle in
    screen pixels. On X11 the rectangle is the client area itself, since the
    frame belongs to the window manager."""

    hwnd: int
    title: str
    left: int
    top: int
    right: int
    bottom: int

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


@dataclass(frozen=True)
class Rect:
    """A monitor, in screen pixels."""

    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


def monitor_containing(monitors: list[Rect], x: int, y: int) -> Rect | None:
    """The monitor a point is on, or None if it is off every screen."""
    for rect in monitors:
        if rect.contains(x, y):
            return rect
    return None


class WindowSystem(ABC):
    """What the engine needs from the OS. One instance per process."""

    # ------------------------------------------------------------- windows

    @abstractmethod
    def find_game_window(self) -> WindowInfo | None:
        """The Roblox window, or None. A title match is required; a window
        class the game is known to use wins over one that merely mentions
        Roblox in its title, so a browser tab does not get clicked."""

    @abstractmethod
    def window_alive(self, handle: int) -> bool:
        """Still exists? A closed game is a stop, not a pause."""

    @abstractmethod
    def client_origin(self, handle: int) -> tuple[int, int]:
        """Screen position of the client area's top-left: what every stored
        coordinate is relative to."""

    @abstractmethod
    def client_size(self, handle: int) -> tuple[int, int]:
        """Width and height of the client area."""

    @abstractmethod
    def is_foreground(self, handle: int) -> bool:
        """Has keyboard focus? When the OS cannot say, the answer is True: a
        wrong "no" pauses the bot for the night, and the panic key stands."""

    @abstractmethod
    def focus_window(self, handle: int) -> bool:
        """Bring it to the front. True if it is in front afterwards."""

    # -------------------------------------------------------------- pixels

    @abstractmethod
    def grab(self, handle: int, origin: tuple[int, int], region: Region) -> np.ndarray:
        """A window-relative region as a BGR array of shape (h, w, 3).
        `origin` is the client origin for backends that grab the screen;
        `handle` is the window for backends that grab the window."""

    @abstractmethod
    def grab_screen(self, rect: Rect) -> np.ndarray:
        """A screen rectangle as BGR. Diagnostics only - under XWayland the
        screen has no pixels to give, and the bot never needs it."""

    def monitors(self) -> list[Rect]:
        """Every monitor's rectangle. mss knows them on both platforms;
        an empty list means it could not ask."""
        try:
            with _new_mss() as sct:
                return [Rect(m["left"], m["top"], m["width"], m["height"])
                        for m in sct.monitors[1:]]
        except Exception:
            return []

    # --------------------------------------------------------------- input

    @abstractmethod
    def move_mouse_relative(self, dx: int, dy: int) -> None:
        """Nudge the pointer by a real input event, the kind a game reading
        raw input notices. An absolute warp is not that on Windows."""

    # --------------------------------------------------------------- sound

    @abstractmethod
    def play_alert(self, wav_path: str) -> None:
        """A custom WAV if given and playable, otherwise a built-in beep."""

    @abstractmethod
    def loopback_stream_kwargs(self) -> dict | None:
        """Keyword arguments that make sounddevice.InputStream capture what
        the speakers play, or None when this machine has no such device."""


def _new_mss():
    """mss 10 renamed the entry point; both spellings are in the wild."""
    import mss
    factory = getattr(mss, "MSS", None) or mss.mss
    return factory()


_default: WindowSystem | None = None
_default_lock = threading.Lock()


def default() -> WindowSystem:
    """The backend for this OS, made once. Constructing it touches nothing;
    a display or a DLL is only opened on the first call that needs it."""
    global _default
    with _default_lock:
        if _default is None:
            if sys.platform == "win32":
                from .win32 import Win32WindowSystem
                _default = Win32WindowSystem()
            else:
                from .x11 import X11WindowSystem
                _default = X11WindowSystem()
        return _default
