"""Self-check for the window seam: tracker, grabber and auto-calibration
against a fake window system, with the game on a second monitor.

Run: python tests/test_capture.py
"""

import numpy as np
import paths  # noqa: F401  - puts the project root on sys.path
from fakes import FakeWindowSystem

from gpo_macro import vision
from gpo_macro.capture import ScreenGrabber, WindowTracker
from gpo_macro.config import DetectionConfig, Region
from gpo_macro.platform import Rect, WindowInfo, WindowSystem, default, monitor_containing

MONITORS = [Rect(0, 0, 2560, 1440), Rect(2560, 0, 1920, 1080)]
# 1600x900, sitting on the second monitor.
WINDOW = WindowInfo(hwnd=7, title="Roblox", left=2600, top=50, right=4200, bottom=950)


def main() -> int:
    # The backend for this OS is chosen once and built without touching the OS.
    assert isinstance(default(), WindowSystem) and default() is default()

    assert monitor_containing(MONITORS, 3000, 100) == MONITORS[1]
    assert monitor_containing(MONITORS, 100, 100) == MONITORS[0]
    assert monitor_containing(MONITORS, 9999, 9999) is None

    system = FakeWindowSystem(WINDOW, MONITORS)
    tracker = WindowTracker(system)
    assert tracker.system is system
    assert tracker.refresh() is WINDOW
    assert tracker.origin == (2600, 50) and tracker.handle == 7
    assert tracker.to_screen(10, 20) == (2610, 70)

    # A grab is window-relative and reaches the window system whole:
    # handle, origin and region. Which backend needs which is its business.
    grabber = ScreenGrabber(system)
    frame = grabber.grab_region(tracker, Region(10, 20, 110, 220))
    assert frame.shape == (200, 100, 3), frame.shape
    assert system.grabs == [(7, (2600, 50), Region(10, 20, 110, 220))], system.grabs

    # A full grab is the monitor the window is on, not the primary one.
    grabber.grab_full(tracker)
    assert system.screen_grabs == [MONITORS[1]], system.screen_grabs

    # Auto-calibration reads the client area and stores the region relative
    # to it: the same numbers whichever monitor the window is on.
    scene = np.zeros((900, 1600, 3), np.uint8)
    scene[:] = (218, 130, 49)                              # water
    scene[300:640, 800:812] = (255, 170, 85)               # the gauge's blue interior, 12x340
    system.frame = scene
    result = vision.auto_calibrate(grabber, tracker, DetectionConfig())
    # pad_x = max(140, 8 * 12), pad_y = max(90, 340 // 2), clamped to the client.
    assert result.region == Region(660, 130, 952, 810), result
    assert result.confidence >= 0.5, result.confidence
    assert "relative" in " ".join(result.notes), result.notes

    # No window: auto-calibrate says so rather than storing screen coordinates,
    # and a grab is a clear refusal rather than a grab at (0, 0).
    system.alive = False
    tracker = WindowTracker(system)
    assert tracker.refresh() is None and tracker.handle is None
    result = vision.auto_calibrate(grabber, tracker, DetectionConfig())
    assert result.region is None and "window" in result.message.lower(), result
    try:
        grabber.grab_region(tracker, Region(0, 0, 10, 10))
    except ValueError as exc:
        assert "window" in str(exc), exc
    else:
        raise AssertionError("grabbed with no window")

    # A window that dies is found gone, not reused from the cache.
    system = FakeWindowSystem(WINDOW, MONITORS)
    tracker = WindowTracker(system)
    assert tracker.refresh() is WINDOW
    system.alive = False
    assert tracker.refresh() is None and tracker.handle is None

    print("all capture checks passed")
    return 0


def test_capture() -> None:
    """pytest entry: the window seam. `python tests/test_capture.py` runs the same."""
    main()


if __name__ == "__main__":
    raise SystemExit(main())
