"""Simulated input (pynput) with a corner failsafe and human-ish jitter.

All points are window-relative and translated through WindowTracker, so
calibration survives moving the Roblox window.
"""

from __future__ import annotations

import random
import time
from contextlib import contextmanager
from typing import Optional, Sequence

from pynput.keyboard import Controller as KbController, Key, KeyCode
from pynput.mouse import Button, Controller as MouseController

from .capture import WindowTracker

_NAMED_KEYS = {
    "shift": Key.shift, "ctrl": Key.ctrl, "control": Key.ctrl, "alt": Key.alt,
    "tab": Key.tab, "enter": Key.enter, "return": Key.enter, "esc": Key.esc,
    "escape": Key.esc, "space": Key.space, "backspace": Key.backspace,
    "e": KeyCode.from_char("e"), "q": KeyCode.from_char("q"), "r": KeyCode.from_char("r"),
}


class FailsafeError(RuntimeError):
    """Raised when the mouse is parked in a screen corner - the user's panic move."""


def parse_key(name: str) -> Key | KeyCode:
    """Map a config string like 'f6', '1', 'shift' to a pynput key."""
    name = name.strip().lower()
    if name.startswith("f") and name[1:].isdigit():
        return getattr(Key, name)
    if name in _NAMED_KEYS:
        return _NAMED_KEYS[name]
    if len(name) == 1:
        return KeyCode.from_char(name)
    raise ValueError(f"cannot interpret key: {name!r}")


def to_pynput_hotkey(name: str) -> str:
    """Convert 'ctrl+shift+f9' to pynput GlobalHotKeys syntax '<ctrl>+<shift>+<f9>'."""
    parts = [p.strip().lower() for p in name.split("+") if p.strip()]
    out = []
    for part in parts:
        if len(part) == 1 and part.isalnum():
            out.append(f"<{part}>")
        elif part.startswith("<") and part.endswith(">"):
            out.append(part)
        else:
            out.append(f"<{part}>")
    return "+".join(out)


class InputController:
    def __init__(self, tracker: WindowTracker, jitter: float = 0.10):
        self.tracker = tracker
        self.jitter = max(0.0, jitter)
        self.mouse = MouseController()
        self.keyboard = KbController()
        self._mouse_held = False
        self._held_keys: set[str] = set()

    # ------------------------------------------------------------- helpers

    def _jittered_delay(self, seconds: float) -> None:
        delta = seconds * self.jitter
        time.sleep(max(0.0, seconds + random.uniform(-delta, delta)))

    def failsafe_check(self) -> None:
        import ctypes
        x, y = self.mouse.position
        user32 = ctypes.windll.user32
        screen_w, screen_h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        near = 6
        if (x < near and y < near) or (x > screen_w - near and y < near) \
                or (x < near and y > screen_h - near) or (x > screen_w - near and y > screen_h - near):
            raise FailsafeError(f"mouse parked in screen corner ({x}, {y})")

    # -------------------------------------------------------------- mouse

    def move_to(self, x: int, y: int) -> None:
        self.failsafe_check()
        sx, sy = self.tracker.to_screen(x, y)
        self.mouse.position = (float(sx), float(sy))

    def click(self, point: Sequence[int], delay_after: float = 0.1, double: bool = False) -> None:
        px, py = int(point[0]) + random.randint(-2, 2), int(point[1]) + random.randint(-2, 2)
        # Roblox GUI buttons want to see the cursor arrive, then hover a few
        # frames, then a press that lasts longer than a frame. Measured against
        # Sen's menu: a teleport with a 50 ms hover and 30 ms press was ignored,
        # a short glide in with 400/120 took every time. Menus are not on the
        # hot path, so the extra half second costs nothing.
        sx, sy = self.mouse.position
        ox, oy = self.tracker.origin
        for step in range(1, 7):
            self.move_to(int((sx - ox) + (px - (sx - ox)) * step / 6),
                         int((sy - oy) + (py - (sy - oy)) * step / 6))
            time.sleep(0.015)
        self._jittered_delay(0.4)
        for _ in range(2 if double else 1):
            self.mouse.press(Button.left)
            time.sleep(0.12)
            self.mouse.release(Button.left)
        self._jittered_delay(delay_after)

    def shift_click(self, point: Sequence[int], delay_after: float = 0.15) -> None:
        px, py = int(point[0]), int(point[1])
        self.move_to(px + random.randint(-2, 2), py + random.randint(-2, 2))
        self._jittered_delay(0.05)
        with self.keyboard.pressed(Key.shift):
            self.mouse.press(Button.left)
            time.sleep(0.03)
            self.mouse.release(Button.left)
        self._jittered_delay(delay_after)

    @contextmanager
    def preserved_position(self):
        """Put the cursor back where it was found.

        Casting aims at wherever the cursor already is, so any routine that
        clicks a menu has to hand the aim back. Without this the cursor is left
        on the last button pressed and the next cast goes wherever that was -
        into the dock, or into the bait list that sits under the storage
        prompt's position once you are back at the water.
        """
        origin = self.mouse.position
        try:
            yield
        finally:
            try:
                self.mouse.position = origin
            except Exception:
                pass

    def press_mouse(self) -> None:
        self.failsafe_check()
        self.mouse.press(Button.left)
        self._mouse_held = True

    def release_mouse(self) -> None:
        self.mouse.release(Button.left)
        self._mouse_held = False

    @property
    def mouse_held(self) -> bool:
        return self._mouse_held

    def cast(self, hold_duration: float) -> None:
        """Press, hold for the cast duration (+jitter), release."""
        self.press_mouse()
        self._jittered_delay(hold_duration)
        self.release_mouse()

    def tap(self, duration: float) -> None:
        """One short press+release (the wiki-recommended way to nudge the bar)."""
        self.failsafe_check()
        self.mouse.press(Button.left)
        time.sleep(max(0.02, duration))
        self.mouse.release(Button.left)
        self._mouse_held = False

    # ------------------------------------------------------------ keyboard

    def press_key(self, name: str, delay_after: float = 0.1) -> None:
        key = parse_key(name)
        self.keyboard.press(key)
        time.sleep(0.03)
        self.keyboard.release(key)
        self._jittered_delay(delay_after)

    def tap_escape(self, delay_after: float = 0.2) -> None:
        self.press_key("esc", delay_after)

    # Held keys are how the character walks. A "w+d" string holds both.
    # Anything still down when the bot stops or panics is released by
    # release_keys() with no argument - a stuck D walks the character off the
    # dock and into the sea for as long as nobody is looking.

    def press_keys(self, names: str) -> None:
        for name in names.split("+"):
            name = name.strip()
            if name:
                self.keyboard.press(parse_key(name))
                self._held_keys.add(name)

    def release_keys(self, names: str | None = None) -> None:
        targets = [n.strip() for n in names.split("+")] if names else list(self._held_keys)
        for name in targets:
            try:
                self.keyboard.release(parse_key(name))
            except Exception:
                pass
            self._held_keys.discard(name)

    def hold_key(self, names: str, seconds: float, delay_after: float = 0.1) -> None:
        """Hold for a fixed time - proximity prompts and walking legs."""
        self.press_keys(names)
        try:
            time.sleep(max(0.0, seconds))
        finally:
            self.release_keys(names)
        self._jittered_delay(delay_after)

    def type_text(self, text: str, delay_after: float = 0.1) -> None:
        """Type into a focused text box. Callers must have proven the box is
        there first: with nothing focused this goes straight into Roblox chat."""
        self.keyboard.type(text)
        self._jittered_delay(delay_after)
