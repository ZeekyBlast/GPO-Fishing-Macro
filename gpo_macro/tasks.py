"""Optional automation routines: bait buying/crafting and devil-fruit storage.

These walk calibrated click sequences (window-relative points from the config).
Missing calibration points cause a single warning event, never a crash.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

from .capture import ScreenGrabber, WindowTracker
from .config import BaitConfig, FruitConfig, Region
from .input import InputController

log = logging.getLogger("gpo.tasks")

Publish = Callable[..., None]   # publish(kind, message, **data)


def _warn_once(publish: Publish, message: str, _seen: set[str] = set()) -> None:
    if message not in _seen:
        _seen.add(message)
        publish("warn", message)


def buy_bait(input_ctl: InputController, cfg: BaitConfig, publish: Publish) -> None:
    """Open the bait shop, buy common bait, confirm, close."""
    required = ["shop_open", "shop_buy_common", "shop_close"]
    missing = [p for p in required if not getattr(cfg, p)]
    if missing:
        _warn_once(publish, f"bait buy skipped - not calibrated: {', '.join(missing)}")
        return
    publish("info", "buying bait")
    input_ctl.click(cfg.shop_open, delay_after=0.85)          # menu animation
    input_ctl.click(cfg.shop_buy_common, delay_after=0.3)
    if cfg.shop_confirm:
        input_ctl.click(cfg.shop_confirm, delay_after=0.3)
    input_ctl.click(cfg.shop_close, delay_after=0.3)
    publish("info", "bait purchased")


def craft_bait(input_ctl: InputController, cfg: BaitConfig, publish: Publish) -> None:
    """Open the crafting menu and batch-craft the selected recipe."""
    required = ["craft_open", "craft_select_recipe", "craft_button", "craft_close"]
    missing = [p for p in required if not getattr(cfg, p)]
    if missing:
        _warn_once(publish, f"bait craft skipped - not calibrated: {', '.join(missing)}")
        return
    publish("info", f"crafting bait x{cfg.crafts_per_cycle}")
    input_ctl.click(cfg.craft_open, delay_after=0.85)
    input_ctl.click(cfg.craft_select_recipe, delay_after=0.25)
    if cfg.craft_select_amount:
        input_ctl.click(cfg.craft_select_amount, delay_after=0.2)
    for _ in range(max(1, cfg.crafts_per_cycle)):
        input_ctl.click(cfg.craft_button, delay_after=0.05)
    if cfg.craft_confirm:
        input_ctl.click(cfg.craft_confirm, delay_after=0.3)
    input_ctl.click(cfg.craft_close, delay_after=0.3)
    publish("info", "bait crafted")


_TEMPLATE_CACHE: dict[str, np.ndarray] = {}

def _load_template(path: str) -> Optional[np.ndarray]:
    resolved = Path(path)
    if not resolved.exists():
        return None
    cached = _TEMPLATE_CACHE.get(str(resolved))
    if cached is not None:
        return cached
    image = cv2.imread(str(resolved), cv2.IMREAD_COLOR)
    if image is None:
        return None
    _TEMPLATE_CACHE[str(resolved)] = image
    return image


def match_template(frame_bgr: np.ndarray, template_bgr: np.ndarray) -> tuple[float, tuple[int, int]]:
    """Best normalized-correlation match. Returns (score, top_left)."""
    result = cv2.matchTemplate(frame_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
    _, score, _, top_left = cv2.minMaxLoc(result)
    return float(score), (int(top_left[0]), int(top_left[1]))


def fruit_positions(hotbar_bgr: np.ndarray, template_bgr: np.ndarray,
                    threshold: float) -> list[tuple[int, int, float]]:
    """Where the fruit icons are: [(x, y, score), ...] centred, left to right.

    Positions, not slot numbers. GPO renders only the slots you actually have
    while every item keeps its original key binding, so a hotbar reading
    1 2 3 4 5 6 7 0 puts the eighth icon on the `0` key - the position tells
    you nothing about which key equips it. Clicking the icon sidesteps the
    whole question, and a pixel position is all a click needs.
    """
    th, tw = template_bgr.shape[:2]
    if hotbar_bgr.size == 0 or hotbar_bgr.shape[0] < th or hotbar_bgr.shape[1] < tw:
        return []
    scores = cv2.matchTemplate(hotbar_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
    found: list[tuple[int, int, float]] = []
    while len(found) < 20:
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        if score < threshold:
            break
        found.append((x + tw // 2, y + th // 2, float(score)))
        # Blank a template-sized neighbourhood so one icon is not found twice.
        x0, x1 = max(0, x - tw // 2), min(scores.shape[1], x + tw // 2 + 1)
        y0, y1 = max(0, y - th // 2), min(scores.shape[0], y + th // 2 + 1)
        scores[y0:y1, x0:x1] = -1.0
    return sorted(found)


def find_green_prompt(frame_bgr: np.ndarray,
                      min_area: int = 400) -> Optional[tuple[int, int]]:
    """Centre of the largest green slab in this frame, or None.

    The Store Fruit prompt is found rather than assumed: it is a proximity
    prompt, so a hand-placed click point that is a few pixels off its edge
    misses every time, and reports the prompt as absent when it is right
    there. Searching also means the click lands on the button even if it
    moves.
    """
    if frame_bgr.size == 0:
        return None
    b, g, r = (frame_bgr[:, :, 0].astype(int), frame_bgr[:, :, 1].astype(int),
               frame_bgr[:, :, 2].astype(int))
    mask = ((g > 90) & (g > r + 25) & (g > b + 25)).astype(np.uint8)
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count < 2:
        return None
    best = max(range(1, count), key=lambda i: stats[i, cv2.CC_STAT_AREA])
    if stats[best, cv2.CC_STAT_AREA] < min_area:
        return None
    return int(centroids[best][0]), int(centroids[best][1])


def red_fraction(frame_bgr: np.ndarray) -> float:
    """Fraction of strongly red pixels - GPO writes refusals in red."""
    if frame_bgr.size == 0:
        return 0.0
    b, g, r = (frame_bgr[:, :, 0].astype(int), frame_bgr[:, :, 1].astype(int),
               frame_bgr[:, :, 2].astype(int))
    return float(((r > 140) & (r > g + 60) & (r > b + 60)).mean())


def changed_fraction(before_bgr: np.ndarray, after_bgr: np.ndarray) -> float:
    """Fraction of pixels that moved between two frames of the same region."""
    if before_bgr.shape != after_bgr.shape or before_bgr.size == 0:
        return 1.0
    delta = np.abs(after_bgr.astype(int) - before_bgr.astype(int)).max(axis=2)
    return float((delta > 28).mean())


def classify_banner(before_bgr: np.ndarray, after_bgr: np.ndarray) -> str:
    """'none' | 'refused' | 'stored', from the top-of-screen banner strip.

    Both outcomes draw a banner, so presence alone decides nothing. The
    refusal ("You can only store one of each fruit!") is written in red and
    the success banner ("New Item <name>") is not, which separates them
    without reading a single character.
    """
    if changed_fraction(before_bgr, after_bgr) < 0.03:
        return "none"
    return "refused" if red_fraction(after_bgr) > 0.02 else "stored"


HOTBAR_KEYS = "1234567890"     # every key a hotbar slot can answer to


def store_fruits(input_ctl: InputController, grabber: ScreenGrabber,
                 tracker: WindowTracker, cfg: FruitConfig, rod_key: str,
                 publish: Publish, shot_dir: str = "captures/fruit") -> tuple[int, int]:
    """Clear devil fruits out of the hotbar. Returns (stored, dropped).

    Two things about GPO shape this, and neither is guessable from the hotbar
    picture:

    - Slots are equipped with their number key. Clicking a slot does not equip.
    - Which key a slot answers to cannot be read from where it sits, because
      only owned slots are drawn while every item keeps its original binding -
      a hotbar reading 1 2 3 4 5 6 7 0 has its eighth icon on the `0` key.

    So the keys are simply tried in turn, and the game answers: the Store Fruit
    prompt only appears while a devil fruit is held, which makes it both the
    "is this slot a fruit" test and the button to press. Every fruit looks
    identical and GPO refuses one you already own, so the banner decides what
    happens next - stored, or refused and therefore droppable.
    """
    if not cfg.drop_duplicates:
        # Storing without dropping leaves every duplicate sitting in the hotbar
        # with nothing able to clear it, so the two run together or not at all.
        _warn_once(publish, "fruit auto-store does nothing while \"Drop fruits you "
                            "already own\" is off - duplicates would pile up in the "
                            "hotbar with no way to clear them")
        return 0, 0

    missing = [name for name, ok in (
        ("fruit icon", _load_template(cfg.template_path) is not None),
        ("hotbar region", cfg.hotbar_region.valid()),
        ("banner region", cfg.banner_region.valid()),
        ("store button", bool(cfg.store_point))) if not ok]
    if missing:
        _warn_once(publish, "fruit auto-store is on but not calibrated: "
                            f"{', '.join(missing)} (Calibration tab)")
        return 0, 0

    template = _load_template(cfg.template_path)

    def count_fruit() -> int:
        try:
            hotbar = grabber.grab_region(tracker, cfg.hotbar_region)
        except Exception:
            return -1
        return len(fruit_positions(hotbar, template, cfg.match_threshold))

    remaining = count_fruit()
    if remaining <= 0:
        return 0, 0        # nothing to do, and no keys pressed on a normal catch

    publish("info", f"{remaining} devil fruit(s) in the hotbar")
    stored = dropped = 0
    held_one = False

    for key in HOTBAR_KEYS:
        if remaining <= 0:
            break
        input_ctl.press_key(key, delay_after=0.45)      # equipping animates
        prompt = _wait_for_prompt(grabber, tracker, cfg)
        if prompt is None:
            continue                                    # not a fruit, or not in range
        held_one = True

        outcome, shot = _attempt_store(input_ctl, grabber, tracker, cfg, prompt, shot_dir)
        if outcome == "stored":
            stored += 1
            publish("fruit", "stored a devil fruit", image=shot)
        elif outcome == "refused":
            # Still holding it from the store attempt, which is the only state
            # the drop key works in.
            input_ctl.press_key(cfg.drop_key, delay_after=0.5)
            if count_fruit() >= remaining:
                publish("warn", "already own that fruit, but the drop did not take - "
                                f"check the drop key ({cfg.drop_key})")
                break
            dropped += 1
            publish("info", "already own that fruit, dropped it")
        else:
            publish("warn", f"no response to the store click - saved {shot}")
            break
        remaining = count_fruit()

    if not held_one:
        publish("warn", "found a devil fruit in the hotbar but the Store Fruit prompt "
                        "never appeared - stand at the storage point, or widen the "
                        "store button search area")
    if rod_key:
        input_ctl.press_key(rod_key, delay_after=0.3)   # back to the rod to cast
    return stored, dropped


def _wait_for_prompt(grabber: ScreenGrabber, tracker: WindowTracker,
                     cfg: FruitConfig, timeout: float = 0.7) -> Optional[list[int]]:
    """Where to click Store Fruit, or None if it is not up.

    The prompt is searched for rather than assumed: it is a proximity prompt,
    so a hand-placed point a few pixels off its edge misses every time and
    reports it as absent when it is right there.
    """
    search = Region(cfg.store_point[0] - cfg.store_search_x,
                    cfg.store_point[1] - cfg.store_search_y,
                    cfg.store_point[0] + cfg.store_search_x,
                    cfg.store_point[1] + cfg.store_search_y)
    deadline = time.monotonic() + timeout
    while True:
        try:
            found = find_green_prompt(grabber.grab_region(tracker, search))
        except Exception:
            return None
        if found is not None:
            return [search.x1 + found[0], search.y1 + found[1]]
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.08)


def _attempt_store(input_ctl: InputController, grabber: ScreenGrabber,
                   tracker: WindowTracker, cfg: FruitConfig, store_at: list[int],
                   shot_dir: str) -> tuple[str, str]:
    """Click Store Fruit and read the banner. Returns (outcome, screenshot path)."""
    before = grabber.grab_region(tracker, cfg.banner_region)
    input_ctl.click(store_at, delay_after=0.15)

    deadline = time.monotonic() + max(0.5, cfg.store_wait)
    after = before
    outcome = "none"
    while time.monotonic() < deadline:
        after = grabber.grab_region(tracker, cfg.banner_region)
        outcome = classify_banner(before, after)
        if outcome != "none":
            break
        time.sleep(0.08)

    # Keep the banner either way: a success is what gets posted to Discord, and
    # a non-response is the only evidence of why nothing happened.
    path = ""
    if outcome != "refused":
        directory = Path(shot_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = str(directory / f"{time.strftime('%Y%m%d-%H%M%S')}-{outcome}.png")
        cv2.imwrite(path, after)
    return outcome, path


def reset_template_cache() -> None:
    _TEMPLATE_CACHE.clear()
