"""The X11 backend against a real window: this machine's X server or XWayland.

Opens a tkinter window titled like Roblox, then asks the backend where it
is, what it looks like, whether the pointer moves, and whether the window
is still there after it closes. Tk's own answers are the oracle. Skips
itself where there is no display, so the rest of the suite runs anywhere.

Run: python tests/test_x11.py
"""

import paths  # noqa: F401  - puts the project root on sys.path

import os
import sys
import time

if sys.platform == "win32" or not os.environ.get("DISPLAY"):
    print("no X display - x11 checks skipped")
    raise SystemExit(0)

import tkinter as tk

import numpy as np

from gpo_macro import vision
from gpo_macro.capture import ScreenGrabber, WindowTracker
from gpo_macro.config import DetectionConfig, Region
from gpo_macro.platform.x11 import X11WindowSystem


def draw_gauge(canvas: tk.Canvas, w: int, h: int) -> tuple[int, int]:
    """The minigame as test_vision renders it, at the canvas's centre: a
    black pill with a blue interior, the player's bar, a white fish line.
    Returns the pill's top-left in window coordinates."""
    px, py = w // 2 - 10, h // 2 - 170
    canvas.create_rectangle(px, py, px + 20, py + 342, fill="#191919", outline="")   # pill
    canvas.create_rectangle(px + 5, py + 2, px + 15, py + 341, fill="#55aaff", outline="")   # interior
    canvas.create_rectangle(px + 6, py + 200, px + 14, py + 284, fill="#191919", outline="")  # bar
    canvas.create_rectangle(px + 5, py + 100, px + 15, py + 103, fill="#ffffff", outline="")  # fish
    return px, py


def main() -> int:
    system = X11WindowSystem()

    app = tk.Tk()
    app.title("Roblox (self-check window)")
    canvas = tk.Canvas(app, bg="#ff8000", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    for _ in range(6):                                   # let the window manager place it
        app.update()
        time.sleep(0.1)
    try:
        info = system.find_game_window()
        assert info is not None and "roblox" in info.title.lower(), info
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        assert system.client_origin(info.hwnd) == (x, y), (system.client_origin(info.hwnd), (x, y))
        assert system.client_size(info.hwnd) == (w, h), (system.client_size(info.hwnd), (w, h))
        assert (info.left, info.top, info.right, info.bottom) == (x, y, x + w, y + h), info
        assert system.window_alive(info.hwnd)

        # Pixels come from the window itself, so this holds under XWayland too.
        frame = system.grab(info.hwnd, (x, y), Region(0, 0, w, h))
        assert frame.shape == (h, w, 3) and frame.dtype == np.uint8, frame.shape
        centre = tuple(int(v) for v in frame[h // 2, w // 2])
        assert centre == (0, 128, 255), f"centre pixel is BGR {centre}, wanted (0, 128, 255)"
        try:
            system.grab(info.hwnd, (x, y), Region(0, 0, w + 50, h + 50))
        except ValueError:
            pass
        else:
            raise AssertionError("a box running off the window was not refused")

        # The whole chain on real pixels: draw the minigame, let auto-calibrate
        # box it through the tracker and grabber, then read the bar from the
        # region it chose - all window-relative, wherever the window sits.
        px, py = draw_gauge(canvas, w, h)
        for _ in range(3):
            app.update()
            time.sleep(0.05)
        tracker = WindowTracker(system)
        grabber = ScreenGrabber(system)
        calib = vision.auto_calibrate(grabber, tracker, DetectionConfig())
        assert calib.region is not None, calib.message
        assert calib.region.x1 <= px and calib.region.x2 >= px + 20, (calib.region, px)
        assert calib.region.y1 <= py and calib.region.y2 >= py + 342, (calib.region, py)
        reading = vision.find_bar(grabber.grab_region(tracker, calib.region), DetectionConfig())
        assert reading is not None, "gauge drawn but not read back"
        assert abs(reading.marker_y - (py - calib.region.y1 + 242)) < 4, reading.marker_y
        assert abs(reading.seg_center_y - (py - calib.region.y1 + 101)) < 4, reading.seg_center_y
        print(f"auto-calibrated {calib.region.width()}x{calib.region.height()}, "
              f"bar at {reading.marker_y:.0f}, fish at {reading.seg_center_y:.0f}")

        # The window manager decides focus; the backend must at least answer.
        print(f"foreground={system.is_foreground(info.hwnd)} focus_window={system.focus_window(info.hwnd)}")

        # A relative nudge moves the pointer by exactly that much: it is how
        # the last stretch of every click is walked.
        from Xlib import display as xdisplay
        root = xdisplay.Display().screen().root
        before = root.query_pointer()
        system.move_mouse_relative(7, 3)
        deadline = time.monotonic() + 0.5              # the compositor echoes it back
        while time.monotonic() < deadline:
            after = root.query_pointer()
            if (after.root_x, after.root_y) != (before.root_x, before.root_y):
                break
            time.sleep(0.005)
        moved = (after.root_x - before.root_x, after.root_y - before.root_y)
        system.move_mouse_relative(-7, -3)
        assert moved == (7, 3), f"asked for (7, 3), pointer moved {moved}"
    finally:
        app.destroy()

    for _ in range(20):
        if not system.window_alive(info.hwnd):
            break
        time.sleep(0.05)
    assert not system.window_alive(info.hwnd), "closed window still reported alive"
    assert system.monitors(), "no monitors listed"

    print("all x11 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
