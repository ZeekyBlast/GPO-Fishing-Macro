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

# Aliases for pynput's own key names, which parse_key also accepts directly.
_NAMED_KEYS = {
    "control": Key.ctrl, "return": Key.enter, "escape": Key.esc,
}


class FailsafeError(RuntimeError):
    """Raised when the mouse is parked in a screen corner - the user's panic move."""


def parse_key(name: str) -> Key | KeyCode:
    """Map a config string like 'f6', '1', 'shift', 'page_up' to a pynput key."""
    name = name.strip().lower()
    if name in _NAMED_KEYS:
        return _NAMED_KEYS[name]
    if len(name) > 1 and hasattr(Key, name):
        return Key[name]
    if len(name) == 1:
        return KeyCode.from_char(name)
    raise ValueError(f"cannot interpret key: {name!r}")


def to_pynput_hotkey(name: str) -> str:
    """Convert 'ctrl+shift+f9' to pynput GlobalHotKeys syntax '<ctrl>+<shift>+<f9>'.

    Each part goes through parse_key, so a hotkey and a keystroke given the
    same name are the same key. Special keys are bracketed; a single
    character stays bare, because pynput reads '<a>' as an error and '<1>'
    as virtual key 1 - the left mouse button.
    """
    parts = [p.strip() for p in name.split("+") if p.strip()]
    if not parts:
        raise ValueError("no key named")
    out = []
    for part in parts:
        key = parse_key(part)
        out.append(f"<{key.name}>" if isinstance(key, Key) else key.char)
    return "+".join(out)


class InputController:
    def __init__(self, tracker: WindowTracker, jitter: float = 0.10):
        self.tracker = tracker
        self.system = tracker.system
        self.jitter = max(0.0, jitter)
        self.mouse = MouseController()
        self.keyboard = KbController()
        self._mouse_held = False
        self._held_keys: set[str] = set()
        self._monitors = self.system.monitors()      # screens do not move mid-session

    # ------------------------------------------------------------- helpers

    def _jittered_delay(self, seconds: float) -> None:
        delta = seconds * self.jitter
        time.sleep(max(0.0, seconds + random.uniform(-delta, delta)))

    def failsafe_check(self) -> None:
        """Any corner of any monitor: the panic move has to work on whichever
        screen the hand happens to be over."""
        x, y = self.mouse.position
        near = 6
        for m in self._monitors:
            for cx, cy in ((m.left, m.top), (m.right - 1, m.top),
                           (m.left, m.bottom - 1), (m.right - 1, m.bottom - 1)):
                if abs(x - cx) < near and abs(y - cy) < near:
                    raise FailsafeError(f"mouse parked in screen corner ({x}, {y})")

    # -------------------------------------------------------------- mouse

    def move_to(self, x: int, y: int) -> None:
        self.failsafe_check()
        sx, sy = self.tracker.to_screen(x, y)
        self.mouse.position = (float(sx), float(sy))

    def click(self, point: Sequence[int], delay_after: float = 0.1, double: bool = False) -> None:
        self._approach(int(point[0]) + random.randint(-2, 2), int(point[1]) + random.randint(-2, 2))
        self._jittered_delay(0.4)
        for n in range(2 if double else 1):
            if n:
                time.sleep(0.1)         # a real double-click has a gap in it
            self.mouse.press(Button.left)
            time.sleep(0.12)
            self.mouse.release(Button.left)
        self._jittered_delay(delay_after)

    def _approach(self, x: int, y: int) -> None:
        """Arrive at a window-relative point the way a hand would.

        Roblox reads the mouse through raw input. SetCursorPos - the absolute
        move every other method here uses - parks the cursor without an
        input event, so the GUI never sees it arrive: no hover, no pointer
        hand, and the first click is spent waking the button up instead of
        pressing it. So the cursor is parked short of the target and walked
        the last stretch with relative moves, which the window system sends
        as real events. Windows scales those by pointer acceleration, so the
        walk is closed-loop on the position read back rather than a fixed
        count.
        """
        sx, sy = self.tracker.to_screen(x, y)
        self.failsafe_check()
        self.mouse.position = (float(sx - 48), float(sy))
        time.sleep(0.05)
        for _ in range(40):
            cx, cy = self.mouse.position
            dx, dy = sx - cx, sy - cy
            if abs(dx) <= 1 and abs(dy) <= 1:
                break
            self.system.move_mouse_relative(int(max(-6, min(6, dx))), int(max(-6, min(6, dy))))
            time.sleep(0.015)

    def drag(self, start: Sequence[int], end: Sequence[int], delay_after: float = 0.1) -> None:
        """Press on `start`, glide to `end`, release - a slider knob."""
        self._approach(int(start[0]), int(start[1]))
        self._jittered_delay(0.3)
        self.mouse.press(Button.left)
        time.sleep(0.1)
        for step in range(1, 13):
            self.move_to(int(start[0] + (end[0] - start[0]) * step / 12),
                         int(start[1] + (end[1] - start[1]) * step / 12))
            time.sleep(0.02)
        time.sleep(0.1)
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
