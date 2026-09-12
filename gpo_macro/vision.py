"""Color-based detection of GPO's fishing minigame + auto-calibration.

Verified against in-game screenshots (2026-09, including night-time water):
- The gauge is a dark pill holding the light-blue HOOK segment (your bar).
- The fish is drawn as a thin WHITE marker ON the hook pill (same scale - no
  cross-bar normalization involved). A side pill holds the green PROGRESS
  meter, which is not used for control.
- Holding the button SINKS the hook bar; releasing lets it rise. The gauge
  sits over water that is bright blue by day and dark navy at night, so the
  hook pill is anchored by its blue segment, never by the background.

Colors live in DetectionConfig as RGB; frames from mss/OpenCV are BGR.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from .capture import ScreenGrabber, WindowTracker
from .config import DetectionConfig, Region


@dataclass
class BarReading:
    """Detected minigame geometry, in frame-local pixel coordinates.

    marker_y     - center of the BLACK hook segment (the thing you control)
    seg_center_y - center of the fish marker line (the control target)
    overlap      - True when the fish marker is green (hook on fish => progress)
    track_top/bottom - black hook segment extent
    """

    x_center: float
    band_x1: int
    band_x2: int
    marker_y: float
    marker_size: float
    seg_center_y: float
    seg_top: int
    seg_bottom: int
    track_top: int
    track_bottom: int
    target_y: float = 0.0
    overlap: bool = False
    fish_coasted: bool = False   # the line was not seen; seg_center_y is prev_fish_y

    def __post_init__(self) -> None:
        if not self.target_y:
            self.target_y = self.seg_center_y


@dataclass
class AutoCalibResult:
    region: Optional[Region] = None       # window-relative if tracker provided, else screen
    confidence: float = 0.0
    message: str = ""
    notes: list[str] = field(default_factory=list)


def mask_color(frame_bgr: np.ndarray, rgb: list[int], tolerance: int) -> np.ndarray:
    """Binary mask of pixels within per-channel tolerance of an RGB color."""
    b, g, r = int(rgb[2]), int(rgb[1]), int(rgb[0])
    lower = np.array([max(0, b - tolerance), max(0, g - tolerance), max(0, r - tolerance)],
                     dtype=np.uint8)
    upper = np.array([min(255, b + tolerance), min(255, g + tolerance), min(255, r + tolerance)],
                     dtype=np.uint8)
    return cv2.inRange(frame_bgr, lower, upper) > 0


def black_ratio(frame_bgr: np.ndarray) -> float:
    """Fraction of near-black pixels (loading/black screens in Roblox)."""
    if frame_bgr.size == 0:
        return 0.0
    dark = np.all(frame_bgr < 12, axis=2)
    return float(dark.mean())


def _group_runs(idx: np.ndarray, max_gap: int = 2) -> list[tuple[int, int]]:
    """Group sorted row indices into (start, end_inclusive) runs split at gaps."""
    if idx.size == 0:
        return []
    runs: list[tuple[int, int]] = []
    start = prev = int(idx[0])
    for y in idx[1:]:
        y = int(y)
        if y - prev > max_gap:
            runs.append((start, prev))
            start = y
        prev = y
    runs.append((start, prev))
    return runs


def _column_runs(mask: np.ndarray, min_count: int, min_width: int = 4) -> list[tuple[int, int]]:
    """Contiguous column ranges where the mask has at least min_count pixels."""
    counts = mask.sum(axis=0)
    runs: list[tuple[int, int]] = []
    start = -1
    gap = 0
    for x in range(mask.shape[1]):
        if counts[x] >= min_count:
            if start < 0:
                start = x
            gap = 0
        elif start >= 0:
            gap += 1
            if gap > 2:  # tolerate thin separators inside a pill
                end = x - gap
                if end - start + 1 >= min_width:
                    runs.append((start, end))
                start, gap = -1, 0
    if start >= 0:
        end = mask.shape[1] - 1 - gap
        if end - start + 1 >= min_width:
            runs.append((start, end))
    return runs


def find_bar(frame_bgr: np.ndarray, cfg: DetectionConfig,
             prev_fish_y: Optional[float] = None) -> Optional[BarReading]:
    """Locate the black player bar and the fish line inside the blue play area.

    Measured from a real capture (185x376 scan region, gauge interior 10 px
    wide): the pill has dark side borders, a blue interior, and the player's
    bar is drawn as a solid black block spanning the interior. The fish is a
    thin full-width line on the interior that is neither blue nor black.

    Row classification runs on the interior columns only, so the pill's own
    borders never enter the measurement.
    """
    tol = cfg.color_tolerance
    height = frame_bgr.shape[0]

    blue = mask_color(frame_bgr, cfg.bar_blue, tol)      # play-area background
    dark = mask_color(frame_bgr, cfg.track_gray, tol)    # pill border + player bar

    # Interior columns of the gauge: the tallest run of blue-carrying columns.
    bands = _column_runs(blue, min_count=max(4, height // 40), min_width=4)
    if not bands:
        return None
    x1, x2 = max(bands, key=lambda b: int(blue[:, b[0]:b[1] + 1].sum()))

    blue_frac = blue[:, x1:x2 + 1].mean(axis=1)
    dark_frac = dark[:, x1:x2 + 1].mean(axis=1)
    is_blue = blue_frac >= 0.4
    is_dark = dark_frac >= 0.4
    inside = is_blue | is_dark

    # Pill extent: the blue-or-black span, bridging the fish line where it
    # splits blue from the bar (a gap of a few rows). Picking the span with
    # the most blue in it keeps dark scenery above/below the gauge out.
    if int(is_blue.sum()) < 5:
        return None
    spans = _group_runs(np.where(inside)[0], max_gap=8)
    pill_top, pill_bottom = max(spans, key=lambda r: int(is_blue[r[0]:r[1] + 1].sum()))
    if pill_bottom - pill_top < 20:
        return None

    # Player bar = longest black run inside the pill. max_gap bridges the fish
    # line where it sits on the bar; the min length drops the pill's end caps.
    dark_rows = np.where(is_dark[pill_top:pill_bottom + 1])[0] + pill_top
    bar_runs = [r for r in _group_runs(dark_rows, max_gap=8) if r[1] - r[0] + 1 >= 6]
    if not bar_runs:
        return None
    bar_top, bar_bottom = max(bar_runs, key=lambda r: r[1] - r[0])
    marker_y = (bar_top + bar_bottom) / 2.0
    marker_size = float(bar_bottom - bar_top + 1)

    # Fish line: a thin interior run that is neither blue nor bar-black. Matching
    # by "not the background" instead of by colour keeps it found when the game
    # recolours the marker on contact (white off the bar, green on it).
    other = np.where(~inside[pill_top:pill_bottom + 1])[0] + pill_top
    max_tick = max(6, (pill_bottom - pill_top) // 12)
    fish_runs = [r for r in _group_runs(other, max_gap=2) if r[1] - r[0] + 1 <= max_tick]

    coasted = False
    if fish_runs:
        reference = prev_fish_y if prev_fish_y is not None else marker_y
        seg_top_s, seg_bottom_s = min(
            fish_runs, key=lambda r: abs((r[0] + r[1]) / 2.0 - reference))
    elif prev_fish_y is not None:
        # Marker briefly unreadable (flash, overlap animation): coast on the
        # last sighting rather than reporting the whole gauge as gone. The
        # caller bounds how long - fed back in unbounded, a lost line becomes
        # a fish frozen at one y for the rest of the fight.
        seg_top_s = seg_bottom_s = int(prev_fish_y)
        coasted = True
    else:
        return None
    seg_center = (seg_top_s + seg_bottom_s) / 2.0

    return BarReading(
        x_center=(x1 + x2) / 2.0,
        band_x1=int(x1), band_x2=int(x2),
        marker_y=marker_y, marker_size=marker_size,
        seg_center_y=seg_center,
        seg_top=int(seg_top_s), seg_bottom=int(seg_bottom_s),
        track_top=int(pill_top), track_bottom=int(pill_bottom),
        target_y=seg_center,
        overlap=bool(bar_top <= seg_center <= bar_bottom),
        fish_coasted=coasted,
    )


def annotate(frame_bgr: np.ndarray, reading: Optional[BarReading]) -> np.ndarray:
    """Draw detection geometry on a copy of the frame (for previews/debug)."""
    out = frame_bgr.copy()
    if reading is None:
        cv2.putText(out, "no bar", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        return out
    cv2.rectangle(out, (reading.band_x1, reading.track_top),
                  (reading.band_x2, reading.track_bottom), (255, 170, 85), 1)   # hook segment
    cv2.line(out, (reading.band_x1, int(reading.marker_y)),
             (reading.band_x2, int(reading.marker_y)), (0, 255, 0), 2)          # segment center
    ty = int(reading.target_y)
    cv2.line(out, (max(0, reading.band_x1 - 8), ty),
             (reading.band_x2 + 8, ty), (0, 255, 255), 2)                       # fish marker
    return out


def auto_calibrate(grabber: ScreenGrabber, tracker: Optional[WindowTracker],
                   cfg: DetectionConfig) -> AutoCalibResult:
    """Scan the whole screen for the blue player segment (and green fish segment)
    and propose a scan region covering both pills.

    Should be run while the fishing minigame is visible on screen.
    """
    result = AutoCalibResult()
    try:
        frame = grabber.grab_full()
    except Exception as exc:  # pragma: no cover - depends on display
        result.message = f"screen capture failed: {exc}"
        return result

    frame_h, frame_w = frame.shape[:2]
    tol = cfg.color_tolerance
    blue = mask_color(frame, cfg.bar_blue, tol)

    def largest_blob(mask: np.ndarray) -> Optional[tuple[int, int, int, int, int]]:
        count = int(mask.sum())
        if count < cfg.min_bar_pixels:
            return None
        n, _labels, stats, _c = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
        if n < 2:
            return None
        best = max(range(1, n), key=lambda i: stats[i, cv2.CC_STAT_AREA])
        return (int(stats[best, cv2.CC_STAT_LEFT]), int(stats[best, cv2.CC_STAT_TOP]),
                int(stats[best, cv2.CC_STAT_WIDTH]), int(stats[best, cv2.CC_STAT_HEIGHT]),
                int(stats[best, cv2.CC_STAT_AREA]))

    blue_blob = largest_blob(blue)
    if blue_blob is None:
        result.message = ("blue hook segment not found - start a fishing minigame, then "
                          "retry, or drag-select the region manually")
        return result
    bx, by, bw, bh, area = blue_blob

    # Padding has to cover more than the gauge as it stands right now. The
    # gauge is anchored to the bobber in world space, so it slides with the
    # camera during a fight - measured at ~30 px in 3 s - and a box fitted to
    # the current position clips the bar mid-fight, which reads as the gauge
    # vanishing. Half the pill's height above and below, and eight bar widths
    # (floor 140 px) to each side, absorbs that drift.
    x1, y1, x2, y2 = bx, by, bx + bw, by + bh
    pad_x = max(140, bw * 8)
    pad_y = max(90, bh // 2)
    x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
    x2, y2 = min(frame_w, x2 + pad_x), min(frame_h, y2 + pad_y)

    if tracker is not None and tracker.refresh() is not None:
        ox, oy = tracker.origin
        x1, y1, x2, y2 = x1 - ox, y1 - oy, x2 - ox, y2 - oy
        result.notes.append("region stored relative to the Roblox window")
    else:
        result.notes.append("Roblox window not found - region stored as absolute screen coords")

    result.region = Region(int(x1), int(y1), int(x2), int(y2))
    confidence = 0.6
    confidence += 0.2 if 1.0 <= bh / max(1, bw) <= 30 else 0.0
    confidence += 0.2 if area >= cfg.min_bar_pixels * 4 else 0.0
    result.confidence = min(1.0, confidence)
    if result.confidence < 0.5:
        result.message = ("found a candidate region but confidence is low - verify with "
                          "'Test detection'")
    else:
        result.message = (f"auto-calibrated: gauge {bw}x{bh} px, region "
                          f"{int(x2 - x1)}x{int(y2 - y1)} with room for drift")
    return result


def sample_color_at(frame_bgr: np.ndarray, x: int, y: int, radius: int = 2) -> list[int]:
    """Median RGB color in a small patch around (x, y) - used by the GUI color picker."""
    h, w = frame_bgr.shape[:2]
    x0, x1 = max(0, x - radius), min(w, x + radius + 1)
    y0, y1 = max(0, y - radius), min(h, y + radius + 1)
    patch = frame_bgr[y0:y1, x0:x1].reshape(-1, 3)
    med = np.median(patch, axis=0).astype(int)  # BGR
    return [int(med[2]), int(med[1]), int(med[0])]  # RGB


def _preview() -> None:  # pragma: no cover - interactive
    """Live detection preview: python -m gpo_macro.vision [--region x1,y1,x2,y2]"""
    from .config import AppConfig

    parser = argparse.ArgumentParser(description="live minigame detection preview")
    parser.add_argument("--region", default="", help="x1,y1,x2,y2 (window-relative)")
    args = parser.parse_args()

    cfg = AppConfig()
    tracker = WindowTracker()
    info = tracker.refresh()
    if info is None:
        print("Roblox window not found - capturing full screen instead")
    grabber = ScreenGrabber()

    if args.region:
        vals = [int(v) for v in args.region.split(",")]
        region = Region(*vals)
    else:
        region = cfg.scan_region if cfg.scan_region.valid() else None

    cv2.namedWindow("gpo-vision", cv2.WINDOW_NORMAL)
    print("preview running - press 'q' in the window to quit")
    while True:
        try:
            if region is not None and region.valid():
                frame = grabber.grab_region(tracker, region)
            else:
                frame = grabber.grab_full()
        except Exception as exc:
            print(f"grab failed: {exc}")
            break
        reading = find_bar(frame, cfg.detection)
        shown = annotate(frame, reading)
        if reading is not None:
            cv2.setWindowTitle(
                "gpo-vision",
                f"marker_y={reading.marker_y:.0f} target_y={reading.target_y:.0f}")
        cv2.imshow("gpo-vision", shown)
        if cv2.waitKey(30) & 0xFF == ord("q"):
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    _preview()
