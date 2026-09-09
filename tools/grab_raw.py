"""Capture RAW frames of the fishing gauge and report what find_bar reads.

  python tools/grab_raw.py            grab one frame now
  python tools/grab_raw.py --wait     poll until the gauge appears, then burst-capture

Frames land in captures/ as raw_NN.png (no annotation drawn on them). Paths are
resolved against the project root, so it works from any working directory.
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from gpo_macro.capture import ScreenGrabber, WindowTracker
from gpo_macro.config import AppConfig
from gpo_macro import vision

OUT = ROOT / "captures"


def gauge_blue(frame, cfg) -> int:
    """Exact-match count of the gauge's interior blue - trigger only, not detection."""
    b, g, r = cfg.bar_blue[2], cfg.bar_blue[1], cfg.bar_blue[0]
    return int(np.count_nonzero((frame[:, :, 0] == b) & (frame[:, :, 1] == g)
                                & (frame[:, :, 2] == r)))


def report(frame, cfg, label: str) -> None:
    reading = vision.find_bar(frame, cfg.detection)
    if reading is None:
        print(f"{label}  exact_blue={gauge_blue(frame, cfg.detection)}  find_bar=NOT FOUND")
    else:
        print(f"{label}  bar_y={reading.marker_y:6.1f} size={reading.marker_size:3.0f} "
              f"fish_y={reading.seg_center_y:6.1f} overlap={int(reading.overlap)} "
              f"pill={reading.track_top:3d}-{reading.track_bottom:3d} "
              f"cols={reading.band_x1}-{reading.band_x2}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", action="store_true", help="poll until the gauge shows up")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--interval", type=float, default=0.25)
    ap.add_argument("--pad", type=int, default=0, help="grow the region by N px on every side")
    args = ap.parse_args()

    cfg = AppConfig.load(ROOT / "settings.json")
    tracker = WindowTracker()
    info = tracker.refresh()
    print("roblox window:", info.title if info else "NOT FOUND")
    if not cfg.scan_region.valid():
        raise SystemExit("scan region not calibrated")
    if args.pad:
        r = cfg.scan_region
        r.x1 -= args.pad; r.y1 -= args.pad; r.x2 += args.pad; r.y2 += args.pad
        print(f"padded region: ({r.x1},{r.y1})-({r.x2},{r.y2})  {r.width()}x{r.height()}")
    grabber = ScreenGrabber()
    OUT.mkdir(exist_ok=True)

    if args.wait:
        deadline = time.monotonic() + args.timeout
        print(f"waiting up to {args.timeout:.0f}s for the gauge - cast now")
        while time.monotonic() < deadline:
            if gauge_blue(grabber.grab_region(tracker, cfg.scan_region), cfg.detection) >= 200:
                break
            time.sleep(0.1)
        else:
            raise SystemExit("gauge never appeared")
        print("gauge up - capturing")

    for i in range(args.frames if args.wait else 1):
        frame = grabber.grab_region(tracker, cfg.scan_region)
        path = OUT / f"raw_{i:02d}.png"
        cv2.imwrite(str(path), frame)
        report(frame, cfg, f"{path}")
        if args.wait:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
