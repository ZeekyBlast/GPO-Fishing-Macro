"""Linux: X11 through python-xlib, which pynput already brings along.

Works against a real X server and against XWayland, with one difference
that decides how pixels are read: under XWayland the root window has no
contents (a screen grab comes back black), but every X window still has
its own, so frames are taken from the game's window with XGetImage. That
is what the engine wants anyway - every region it stores is relative to
the window, so the window is the natural thing to read.

A native Wayland surface is invisible to all of this. Sober and the Wine
clients can be run as X11 windows; `xwininfo -root -tree | grep -i roblox`
is the test.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import wave

import numpy as np

from . import Rect, WindowInfo, WindowSystem

log = logging.getLogger("gpo.platform")

# WM_CLASS values the Linux Roblox clients are known to use, lower-cased.
# A title match is still required; the class only breaks ties against a
# browser tab that happens to be titled "Roblox".
GAME_CLASSES = {"sober", "org.vinegarhq.sober", "robloxplayerbeta.exe", "roblox"}

ATOMS = ("_NET_CLIENT_LIST", "_NET_WM_NAME", "_NET_ACTIVE_WINDOW")


class X11WindowSystem(WindowSystem):
    def __init__(self) -> None:
        self._local = threading.local()

    # ------------------------------------------------------------ plumbing

    def _display(self):
        """This thread's connection, opened on first use. None when there is
        no X server to talk to, which every method treats as "no window"."""
        if hasattr(self._local, "display"):
            return self._local.display
        self._local.display = None
        if not os.environ.get("DISPLAY"):
            return None
        try:
            from Xlib import display
            self._local.display = display.Display()
            self._local.atoms = {name: self._local.display.intern_atom(name) for name in ATOMS}
        except Exception as exc:
            log.warning("cannot open the X display: %s", exc)
        return self._local.display

    def _atom(self, name: str) -> int:
        return self._local.atoms[name]

    def _window(self, handle: int):
        return self._display().create_resource_object("window", handle)

    def _name(self, win) -> str:
        prop = win.get_full_property(self._atom("_NET_WM_NAME"), 0)
        if prop is not None and prop.value:
            raw = prop.value
            return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        name = win.get_wm_name()
        return name.decode("latin-1", "replace") if isinstance(name, bytes) else (name or "")

    # ------------------------------------------------------------- windows

    def find_game_window(self) -> WindowInfo | None:
        d = self._display()
        if d is None:
            return None
        from Xlib import Xatom, error
        root = d.screen().root
        try:
            listing = root.get_full_property(self._atom("_NET_CLIENT_LIST"), Xatom.WINDOW)
        except error.XError:
            return None
        found: list[tuple[int, int, str]] = []          # (rank, handle, title)
        for handle in (listing.value if listing is not None else []):
            win = self._window(handle)
            try:
                title = self._name(win)
                if "roblox" not in title.lower():
                    continue
                klass = win.get_wm_class() or ("", "")
            except error.XError:
                continue
            known = any(part.lower() in GAME_CLASSES for part in klass)
            found.append((0 if known else 1, int(handle), title))
        if not found:
            return None
        rank, handle, title = min(found, key=lambda f: f[0])
        if rank:
            log.info("no window of a known Roblox class; using %r by title", title)
        try:
            (x, y), (w, h) = self.client_origin(handle), self.client_size(handle)
        except error.XError:
            return None
        return WindowInfo(handle, title, x, y, x + w, y + h)

    def window_alive(self, handle: int) -> bool:
        if self._display() is None:
            return False
        from Xlib import error
        try:
            self._window(handle).get_geometry()
            return True
        except error.XError:
            return False

    def client_origin(self, handle: int) -> tuple[int, int]:
        d = self._display()
        if d is None:
            return (0, 0)
        # The frame is the window manager's; the client's own origin in root
        # coordinates is exactly what stored coordinates are relative to.
        at = d.screen().root.translate_coords(self._window(handle), 0, 0)
        return (int(at.x), int(at.y))

    def client_size(self, handle: int) -> tuple[int, int]:
        if self._display() is None:
            return (0, 0)
        geometry = self._window(handle).get_geometry()
        return (int(geometry.width), int(geometry.height))

    def is_foreground(self, handle: int) -> bool:
        d = self._display()
        if d is None:
            return True
        from Xlib import Xatom, error
        try:
            active = d.screen().root.get_full_property(self._atom("_NET_ACTIVE_WINDOW"), Xatom.WINDOW)
        except error.XError:
            return True
        if active is None or not len(active.value):
            return True                              # unknown: do not pause on a guess
        return int(active.value[0]) == handle

    def focus_window(self, handle: int) -> bool:
        d = self._display()
        if d is None:
            return False
        from Xlib import X, error
        from Xlib.protocol import event
        win = self._window(handle)
        root = d.screen().root
        try:
            # The EWMH way: ask the window manager. Source 2 = a pager, which
            # managers honour more readily than an application's own request.
            message = event.ClientMessage(window=win, client_type=self._atom("_NET_ACTIVE_WINDOW"),
                                          data=(32, [2, X.CurrentTime, 0, 0, 0]))
            root.send_event(message, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            d.flush()
            time.sleep(0.05)
            if self.is_foreground(handle):
                return True
            win.set_input_focus(X.RevertToParent, X.CurrentTime)
            d.sync()
        except error.XError:
            return False
        time.sleep(0.05)
        return self.is_foreground(handle)

    # -------------------------------------------------------------- pixels

    def grab(self, handle: int, origin: tuple[int, int], region) -> np.ndarray:
        if self._display() is None:
            raise ValueError("no X display")
        return self._image(self._window(handle), region.x1, region.y1,
                           region.width(), region.height())

    def grab_screen(self, rect: Rect) -> np.ndarray:
        d = self._display()
        if d is None:
            raise ValueError("no X display")
        return self._image(d.screen().root, rect.left, rect.top, rect.width, rect.height)

    @staticmethod
    def _image(drawable, x: int, y: int, width: int, height: int) -> np.ndarray:
        from Xlib import X, error
        if width <= 0 or height <= 0:
            raise ValueError(f"invalid grab box: {width}x{height}")
        try:
            image = drawable.get_image(x, y, width, height, X.ZPixmap, 0xFFFFFFFF)
        except error.XError as exc:
            # BadMatch when the box runs off the window, BadWindow once it is gone.
            raise ValueError(f"grab failed: {exc.__class__.__name__}") from None
        if image.depth not in (24, 32):
            raise ValueError(f"unsupported display depth {image.depth}")
        data = np.frombuffer(image.data, dtype=np.uint8)
        return data.reshape(height, width, 4)[:, :, :3].copy()      # BGRA -> BGR, like mss

    # --------------------------------------------------------------- input

    def move_mouse_relative(self, dx: int, dy: int) -> None:
        d = self._display()
        if d is None:
            return
        from Xlib import X
        from Xlib.ext import xtest
        # XTest motion is real input as far as every client is concerned;
        # detail=1 makes it relative.
        xtest.fake_input(d, X.MotionNotify, detail=1, x=int(dx), y=int(dy))
        d.sync()

    # --------------------------------------------------------------- sound

    def play_alert(self, wav_path: str) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:
            log.warning("sound alert unavailable (sounddevice import failed): %s", exc)
            return
        if wav_path:
            try:
                rate, samples = _read_wav(wav_path)
                sd.play(samples, rate)
                return
            except Exception as exc:
                log.warning("could not play %s (%s) - falling back to beeps", wav_path, exc)
        try:
            sd.play(_beeps(), 44100)
        except Exception as exc:
            log.warning("could not play the alert: %s", exc)

    def loopback_stream_kwargs(self) -> dict | None:
        """PulseAudio and PipeWire expose what the speakers play as a
        "Monitor of ..." input; PortAudio lists it like any other device."""
        try:
            import sounddevice as sd
            for index, device in enumerate(sd.query_devices()):
                if device["max_input_channels"] > 0 and "monitor" in device["name"].lower():
                    return {"device": index, "channels": min(2, int(device["max_input_channels"]))}
        except Exception as exc:
            log.warning("cannot list audio devices: %s", exc)
        return None


def _read_wav(path: str) -> tuple[int, np.ndarray]:
    """PCM WAV to float32 samples in [-1, 1], shape (frames, channels)."""
    with wave.open(path, "rb") as handle:
        rate, channels, width = handle.getframerate(), handle.getnchannels(), handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width == 1:
        samples = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        samples = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        samples = np.frombuffer(raw, np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width {width}")
    return rate, samples.reshape(-1, channels)


def _beeps(rate: int = 44100) -> np.ndarray:
    """The same four-note alert winsound plays on Windows."""
    parts = []
    for freq, ms in ((880, 250), (1100, 250), (880, 250), (1400, 400)):
        t = np.arange(int(rate * ms / 1000)) / rate
        tone = 0.3 * np.sin(2 * np.pi * freq * t)
        fade = min(len(tone) // 2, rate // 100)                     # 10 ms in and out
        tone[:fade] *= np.linspace(0, 1, fade)
        tone[-fade:] *= np.linspace(1, 0, fade)
        parts.append(tone)
    return np.concatenate(parts).astype(np.float32)
