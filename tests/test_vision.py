"""Self-check for gauge detection: synthetic gauges + the real capture.

Run: python tests/test_vision.py
"""
import paths  # noqa: F401  - puts the project root on sys.path

import cv2
import numpy as np

from gpo_macro.config import DetectionConfig
from gpo_macro.vision import find_bar

BLUE = (255, 170, 85)     # BGR of RGB(85,170,255)
BLACK = (25, 25, 25)
WHITE = (255, 255, 255)
GREEN = (0, 255, 0)


def gauge(bar_top, bar_bottom, fish_y, fish_bgr=WHITE, h=376, w=185):
    """Render a gauge that matches the measured in-game layout."""
    img = np.full((h, w, 3), (218, 130, 49), np.uint8)      # water
    img[24:366, 54:74] = BLACK                               # pill + borders
    img[26:365, 59:69] = BLUE                                # interior
    img[bar_top:bar_bottom + 1, 60:68] = BLACK               # player bar
    img[fish_y:fish_y + 3, 59:69] = fish_bgr                 # fish line
    return img


def main():
    cfg = DetectionConfig()

    r = find_bar(gauge(224, 307, 193), cfg)
    assert r is not None, "clean gauge not detected"
    assert abs(r.marker_y - 265.5) < 3, r.marker_y
    assert abs(r.marker_size - 84) < 4, r.marker_size
    assert abs(r.seg_center_y - 194) < 3, r.seg_center_y
    assert not r.overlap
    assert r.track_top < 30 and r.track_bottom > 360, (r.track_top, r.track_bottom)

    # Fish sitting on the bar, recoloured green: still found, overlap flagged.
    r = find_bar(gauge(224, 307, 265, GREEN), cfg)
    assert r is not None, "green-on-bar gauge not detected"
    assert abs(r.seg_center_y - 266) < 3, r.seg_center_y
    assert r.overlap
    assert abs(r.marker_y - 265.5) < 6, r.marker_y

    # Bar parked against each end: the old blue-gap search guessed a 60 px bar here.
    r = find_bar(gauge(26, 120, 250), cfg)
    assert r is not None and abs(r.marker_y - 73) < 4, r
    r = find_bar(gauge(280, 364, 100), cfg)
    assert r is not None and abs(r.marker_y - 322) < 4, r

    # Fish line sitting between the blue and the bar: the pill span has to
    # bridge it, or the bar falls outside the pill and nothing is found.
    r = find_bar(gauge(310, 364, 306), cfg)
    assert r is not None, "fish line split the pill from the bar"
    assert abs(r.marker_y - 337) < 4, r.marker_y
    assert abs(r.seg_center_y - 307) < 3, r.seg_center_y

    # No gauge on screen. The real frame matters more than the flat one: it
    # has hundreds of scattered gauge-blue and near-black scenery pixels, which
    # is exactly what a pixel-count presence check mistook for a bite.
    assert find_bar(np.full((376, 185, 3), (218, 130, 49), np.uint8), cfg) is None
    idle = cv2.imread(paths.fixture("no_gauge.png"))
    assert idle is not None, "missing tests/fixtures/no_gauge.png"
    assert find_bar(idle, cfg) is None, "scenery read as a gauge"""

    # Real captures. test_detection.png is rewritten by the app's Test
    # Detection button, so the fixtures are frozen copies with known values.
    for name, bar_y, fish_y, over in (("gauge_overlap", 302.0, 298.0, True),
                                      ("gauge_apart", 426.5, 298.0, False)):
        frame = cv2.imread(paths.fixture(f"{name}.png"))
        assert frame is not None, f"missing tests/fixtures/{name}.png"
        r = find_bar(frame, cfg)
        assert r is not None, f"{name}: not detected"
        assert abs(r.marker_y - bar_y) < 4, (name, r.marker_y)
        assert abs(r.seg_center_y - fish_y) < 4, (name, r.seg_center_y)
        assert r.overlap is over, (name, r.overlap)
        # The pill's antialiased end cap moves a few rows with colour tolerance,
        # so check the span, not exact bounds. It must not be clipped again.
        assert r.track_bottom - r.track_top >= 330, (name, r.track_top, r.track_bottom)
        assert r.track_top <= r.marker_y <= r.track_bottom, (name, r.marker_y)
        assert abs(r.marker_size - 86) < 5, (name, r.marker_size)
        print(f"{name}: bar_y={r.marker_y:.1f} size={r.marker_size:.0f} "
              f"fish_y={r.seg_center_y:.1f} overlap={r.overlap} "
              f"pill={r.track_top}-{r.track_bottom}")

    # A frame with the fish line painted out still reads, but says so: the
    # returned fish is the previous one, and the caller decides how long that
    # is allowed to go on. Fed back in unbounded it was a fish frozen at one y
    # for a whole fight.
    frame = cv2.imread(paths.fixture("gauge_apart.png"))
    real = find_bar(frame, cfg)
    assert real is not None and not real.fish_coasted
    x1, x2 = real.band_x1, real.band_x2 + 1
    blanked = frame.copy()
    blanked[real.seg_top - 3:real.seg_bottom + 4, x1:x2] = blanked[real.seg_top - 8, x1:x2]
    assert find_bar(blanked, cfg) is None, "lost line with no history must be a miss"
    ghost = find_bar(blanked, cfg, prev_fish_y=real.seg_center_y)
    assert ghost is not None and ghost.fish_coasted, ghost
    assert abs(ghost.seg_center_y - real.seg_center_y) < 1, ghost.seg_center_y

    print("all detection checks passed")


if __name__ == "__main__":
    main()
