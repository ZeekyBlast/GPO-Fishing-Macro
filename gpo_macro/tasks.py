"""Optional automation routines: bait upkeep and devil-fruit storage.

Every routine here clicks menus, and every one of them reads the screen
before it clicks: a menu that did not open is a click into the world, which
with a rod in hand is a cast. So the pattern throughout is probe a small box
around the target, trigger, wait for that box to change, and only then click.
Missing calibration causes a single warning event, never a crash.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

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


def green_fraction(frame_bgr: np.ndarray) -> float:
    """Fraction of strongly green pixels - the craft counter once it is filled."""
    if frame_bgr.size == 0:
        return 0.0
    b, g, r = (frame_bgr[:, :, 0].astype(int), frame_bgr[:, :, 1].astype(int),
               frame_bgr[:, :, 2].astype(int))
    return float(((g > 120) & (g > r + 50) & (g > b + 50)).mean())


def white_fraction(frame_bgr: np.ndarray) -> float:
    """Fraction of near-white pixels - GUI text on a dark slab."""
    if frame_bgr.size == 0:
        return 0.0
    return float((frame_bgr.min(axis=2) > 200).mean())


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
    return wait_for_green(grabber, tracker, cfg.store_point,
                          cfg.store_search_x, cfg.store_search_y, timeout)


def wait_for_green(grabber: ScreenGrabber, tracker: WindowTracker, point: list[int],
                   search_x: int, search_y: int, timeout: float) -> Optional[list[int]]:
    """Where to click a green button near `point`, or None if none is up.

    Searched for rather than assumed: the Store Fruit prompt is a proximity
    prompt, so a hand-placed point a few pixels off its edge misses every time
    and reports it as absent when it is right there. The CRAFT slab is found
    the same way for the same reason.
    """
    search = Region(point[0] - search_x, point[1] - search_y,
                    point[0] + search_x, point[1] + search_y)
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


def box_around(point: list[int], half: int = 24) -> Region:
    return Region(point[0] - half, point[1] - half, point[0] + half, point[1] + half)


def probe(grabber: ScreenGrabber, tracker: WindowTracker, region: Region) -> np.ndarray:
    """A baseline frame of a region, taken before something is triggered."""
    try:
        return grabber.grab_region(tracker, region)
    except Exception:
        return np.zeros((0, 0, 3), np.uint8)


def wait_changed(grabber: ScreenGrabber, tracker: WindowTracker, region: Region,
                 baseline: np.ndarray, timeout: float, min_change: float = 0.15) -> bool:
    """True once `region` differs from `baseline` - the thing appeared (or left)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if changed_fraction(baseline, grabber.grab_region(tracker, region)) >= min_change:
                return True
        except Exception:
            return False
        if time.monotonic() >= deadline:
            return False
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


# ---------------------------------------------------------------- bait upkeep

CRAFT_CEILING = 300         # the bait stack cap; no loop needs more
PROMPT_MATCH = 0.85         # the white T badge is high-contrast; matches score high
TAG_MATCH = 0.80            # his nametag: white text on the world, same size at any range


class WalkError(RuntimeError):
    """The character is somewhere the bot cannot see its way back from.
    Fishing from there would cast into the dock all night, so the bot stops."""


def _missing(cfg: BaitConfig, names: list[str]) -> list[str]:
    out = []
    for name in names:
        value = getattr(cfg, name)
        if not (value.valid() if isinstance(value, Region) else bool(value)):
            out.append(name.replace("_", " "))
    return out


def upkeep_bait(input_ctl: InputController, grabber: ScreenGrabber, tracker: WindowTracker,
                cfg: BaitConfig, banner: Region, publish: Publish) -> None:
    """One upkeep pass: craft or buy, then put the bait back on the rod.

    `banner` is the fruit section's top-of-screen strip: the craft menu's
    "no eligible materials" and the barrel's refusals land on the same strip
    as "New Item", so it is calibrated once.
    """
    if cfg.auto_buy and cfg.auto_craft:
        _warn_once(publish, "auto-buy and auto-craft are both on - running neither. "
                            "Pick one in Settings.")
        return
    if cfg.auto_craft:
        with walked_to_sen(input_ctl, grabber, tracker, cfg, publish) as there:
            if there:
                craft_bait(input_ctl, grabber, tracker, cfg, banner, publish)
    elif cfg.auto_buy:
        buy_bait(input_ctl, grabber, tracker, cfg, banner, publish)
    else:
        return
    select_bait(input_ctl, cfg, publish)


def craft_bait(input_ctl: InputController, grabber: ScreenGrabber, tracker: WindowTracker,
               cfg: BaitConfig, banner: Region, publish: Publish) -> int:
    """Craft the calibrated recipe until the fish run out. Returns how many.

    The N/M counter under the + slot is the whole state machine: red means it
    wants fish, green means it is full, and red again after CRAFT means one
    bait was made. The top strip going red ("You dont have any eligible
    materials to add!") is the stop. Nothing is counted, nothing is read.
    """
    missing = _missing(cfg, ["dialog_yes", "menu_region", "craft_recipe", "craft_add",
                             "craft_pick", "craft_counter_region", "craft_button",
                             "craft_close", "dialog_end"])
    if missing:
        _warn_once(publish, "auto-craft is on but not calibrated: "
                            f"{', '.join(missing)} (Calibration tab)")
        return 0

    settle = max(0.1, cfg.menu_delay)
    yes_box = box_around(cfg.dialog_yes)
    before_yes = probe(grabber, tracker, yes_box)
    before_end = probe(grabber, tracker, box_around(cfg.dialog_end))
    before_menu = probe(grabber, tracker, cfg.menu_region)
    input_ctl.press_key(cfg.talk_key, delay_after=0.2)
    if not wait_changed(grabber, tracker, yes_box, before_yes, timeout=2.0):
        publish("warn", "Sen's dialogue did not appear - stand at Blacksmith Sen, "
                        f"or check the talk key ({cfg.talk_key})")
        return 0
    # The dialogue tweens in, so give it a beat; one retry before calling it
    # a failure.
    time.sleep(settle)
    for _ in range(2):
        input_ctl.click(cfg.dialog_yes, delay_after=settle)
        if wait_changed(grabber, tracker, cfg.menu_region, before_menu,
                        timeout=2.5, min_change=0.15):
            break
    else:
        _end_conversation(input_ctl, grabber, tracker, cfg, before_end, settle)
        publish("warn", "craft menu did not open after Yes")
        return 0

    # The menu answers refusals just above its own top edge, not on the
    # banner strip: "You do not have the requirements to craft this item!"
    strip = Region(cfg.menu_region.x1, cfg.menu_region.y1 - 70,
                   cfg.menu_region.x2, cfg.menu_region.y1)
    counter = cfg.craft_counter_region

    def counter_is(colour: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            frame = grabber.grab_region(tracker, counter)
            if (red_fraction(frame) if colour == "red" else green_fraction(frame)) > 0.03:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.08)

    for _ in range(2):                          # the first click can be eaten
        input_ctl.click(cfg.craft_recipe, delay_after=settle)
        if counter_is("red", 1.5) or counter_is("green", 0.2):
            break
    else:
        _close_menu(input_ctl, grabber, tracker, cfg, settle)
        _end_conversation(input_ctl, grabber, tracker, cfg, before_end, settle)
        publish("warn", "recipe row did not select - the N/M counter never appeared")
        return 0

    publish("info", "crafting bait at Sen")
    crafted = stacks = 0
    out_of_fish = stopped = False           # stopped: a warning already said why
    stack_ready = bool(cfg.craft_slider and cfg.craft_slider_end and cfg.craft_all)
    try:
        while crafted < CRAFT_CEILING:
            # Fill the slot. + opens a list of eligible fish beside the menu;
            # clicking its first row moves that fish in. The row is waited
            # for, not assumed: + is a toggle, so a click that opened nothing
            # gets one more try before the fish are declared gone.
            pick_box = box_around(cfg.craft_pick)
            for _ in range(4):                  # no recipe needs more fish than this
                if counter_is("green", 0.1):
                    break
                opened = False
                for _ in range(2):
                    before_pick = probe(grabber, tracker, pick_box)
                    input_ctl.click(cfg.craft_add, delay_after=0.1)
                    opened = wait_changed(grabber, tracker, pick_box, before_pick, timeout=1.5)
                    if opened or red_fraction(grabber.grab_region(tracker, strip)) > 0.02:
                        break
                if not opened:
                    out_of_fish = True
                    break
                input_ctl.click(cfg.craft_pick, delay_after=settle)
                if not counter_is("green", 1.5) and not counter_is("red", 0.1):
                    break
                # Two-fish recipes come back red with 1/2: loop and add another.
            if out_of_fish or not counter_is("green", 0.1):
                break
            craft_at = wait_for_green(grabber, tracker, cfg.craft_button, 120, 50, timeout=0.6)
            if craft_at is None:
                stopped = True
                publish("warn", "CRAFT button not found near its anchor")
                break
            input_ctl.click(craft_at, delay_after=0.1)
            # Two or more of one fish and CRAFT asks how many instead of
            # crafting: a slider with Craft Selected beside it. Drag the knob
            # to the end and take the whole stack in one go. A CRAFT that did
            # nothing for a second was eaten and gets one more click.
            done = False
            began = time.monotonic()
            deadline = began + 3.5
            retried = False
            while time.monotonic() < deadline:
                if counter_is("red", 0.05):
                    done = True
                    break
                if not retried and time.monotonic() - began > 1.2:
                    retried = True
                    input_ctl.click(craft_at, delay_after=0.1)
                if stack_ready:
                    ask = wait_for_green(grabber, tracker, cfg.craft_all, 80, 30, timeout=0.05)
                    if ask is not None:
                        input_ctl.drag(cfg.craft_slider, cfg.craft_slider_end, delay_after=0.2)
                        input_ctl.click(ask, delay_after=0.1)
                        done = counter_is("red", 2.5)
                        stacks += 1
                        break
            if not done:
                stopped = True
                publish("warn", "CRAFT click had no effect - stopping"
                                + ("" if stack_ready else " (a stack of one fish needs the "
                                   "quantity dialog calibrated)"))
                break
            crafted += 1
            time.sleep(settle)
    finally:
        _close_menu(input_ctl, grabber, tracker, cfg, settle)
        _end_conversation(input_ctl, grabber, tracker, cfg, before_end, settle)

    if crafted:
        what = (f"crafted {crafted} bait" if not stacks
                else f"crafted {crafted - stacks} bait and {stacks} whole stack(s)")
        publish("info", what + (", fish used up" if out_of_fish else ""))
    elif out_of_fish:
        publish("info", "no fish to craft bait from")
    elif not stopped:
        publish("warn", "crafted nothing - the material counter never turned green")
    return crafted


def _close_menu(input_ctl: InputController, grabber: ScreenGrabber, tracker: WindowTracker,
                cfg: BaitConfig, settle: float) -> None:
    # The first click after the menu changed state (a craft, a dialog
    # closing) is eaten; a second one takes. Escape is never the fallback:
    # it is Roblox's own menu key and opens the CoreGui over everything.
    while_open = probe(grabber, tracker, cfg.menu_region)
    for _ in range(2):
        input_ctl.click(cfg.craft_close, delay_after=settle)
        if wait_changed(grabber, tracker, cfg.menu_region, while_open,
                        timeout=1.5, min_change=0.3):
            return


def _end_conversation(input_ctl: InputController, grabber: ScreenGrabber,
                      tracker: WindowTracker, cfg: BaitConfig, before: np.ndarray,
                      settle: float) -> None:
    """Click the "..." bubble Sen leaves behind, but only while it is there:
    with nothing under the cursor that click is a cast at the dock.

    The bubble is a dark slab on dark planks, so "did the region change" is
    blind to it; its three white dots are not. Presence is white pixels in
    the box, and each click is checked the same way before another.
    """
    end_box = box_around(cfg.dialog_end)
    for _ in range(3):
        try:
            if white_fraction(grabber.grab_region(tracker, end_box)) < 0.01:
                return
        except Exception:
            return
        input_ctl.click(cfg.dialog_end, delay_after=settle)
        time.sleep(0.4)


def buy_bait(input_ctl: InputController, grabber: ScreenGrabber, tracker: WindowTracker,
             cfg: BaitConfig, banner: Region, publish: Publish) -> bool:
    """Hold the barrel's key, type an amount, confirm. Returns True if bought.

    Typing is the dangerous step - with no text box focused it goes to the
    game - so it is gated behind proof that the quantity box appeared where it
    was calibrated, and checked again afterwards.
    """
    missing = _missing(cfg, ["shop_quantity", "shop_confirm"])
    if missing:
        _warn_once(publish, "auto-buy is on but not calibrated: "
                            f"{', '.join(missing)} (Calibration tab)")
        return False

    settle = max(0.1, cfg.menu_delay)
    qty_box = box_around(cfg.shop_quantity)
    before = probe(grabber, tracker, qty_box)
    input_ctl.hold_key(cfg.shop_key, cfg.shop_hold, delay_after=0.2)
    if not wait_changed(grabber, tracker, qty_box, before, timeout=2.0):
        publish("warn", "the barrel's dialog did not appear - stand at the bait barrel, "
                        f"or check the shop key ({cfg.shop_key})")
        return False

    untyped = probe(grabber, tracker, qty_box)
    input_ctl.click(cfg.shop_quantity, delay_after=0.2)
    input_ctl.type_text(str(max(1, cfg.buy_amount)), delay_after=settle)
    if not wait_changed(grabber, tracker, qty_box, untyped, timeout=1.0, min_change=0.02):
        if cfg.shop_cancel:
            input_ctl.click(cfg.shop_cancel, delay_after=settle)
        publish("warn", "the amount did not show up in the quantity box - nothing bought")
        return False

    open_dialog = probe(grabber, tracker, qty_box)
    input_ctl.click(cfg.shop_confirm, delay_after=settle)
    refused = banner.valid() and red_fraction(grabber.grab_region(tracker, banner)) > 0.02
    if refused:
        publish("warn", "purchase refused - not enough Peli, or the stack is full")
    else:
        publish("info", f"bought {cfg.buy_amount} bait")

    # Only click Cancel on a dialog that is still there; on the dock it is a cast.
    if not wait_changed(grabber, tracker, qty_box, open_dialog, timeout=1.0) and cfg.shop_cancel:
        input_ctl.click(cfg.shop_cancel, delay_after=settle)
    return not refused


def select_bait(input_ctl: InputController, cfg: BaitConfig, publish: Publish) -> None:
    """Click the tier's row in the rod's bait panel. GPO drops the selection."""
    if not cfg.bait_select:
        _warn_once(publish, "bait row not calibrated - the rod may fish on the wrong bait "
                            "(Calibration tab)")
        return
    input_ctl.click(cfg.bait_select, delay_after=0.2)


def prompt_visible(grabber: ScreenGrabber, tracker: WindowTracker,
                   template: np.ndarray) -> bool:
    frame = grabber.grab_client(tracker)
    th, tw = template.shape[:2]
    if frame.shape[0] < th or frame.shape[1] < tw:
        return False
    score, _ = match_template(frame, template)
    return score >= PROMPT_MATCH


def tag_position(grabber: ScreenGrabber, tracker: WindowTracker,
                 template: np.ndarray) -> Optional[tuple[int, int]]:
    """Centre of Sen's nametag on screen, window-relative, or None.

    The label floats over his head, always faces the camera and draws at one
    size whatever the range, and the camera does not turn on WASD. So its
    place on screen is a readout of where the character stands relative to
    him - the position sensor the walk home steers by.
    """
    frame = grabber.grab_client(tracker)
    th, tw = template.shape[:2]
    if frame.shape[0] < th or frame.shape[1] < tw:
        return None
    score, (x, y) = match_template(frame, template)
    if score < TAG_MATCH:
        return None
    return x + tw // 2, y + th // 2


def _progress(start: tuple[int, int], home: list[int], pos: tuple[int, int]) -> float:
    """How far along the start->home line `pos` is: 0 at start, 1 at home, >1 past it."""
    dx, dy = home[0] - start[0], home[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return 1.0
    return ((pos[0] - start[0]) * dx + (pos[1] - start[1]) * dy) / length_sq


def _dist(a, b) -> float:
    return float(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5)


@contextmanager
def walked_to_sen(input_ctl: InputController, grabber: ScreenGrabber, tracker: WindowTracker,
                  cfg: BaitConfig, publish: Publish) -> Iterator[bool]:
    """Walk until Sen's T badge is on screen; steer back by his nametag.

    Both legs stop on a measurement, never a timer. In: the prompt appears.
    Back: his nametag is where it sits when you stand at the fishing spot,
    then any overshoot is trimmed with taps whose length comes from the speed
    just measured. A timer only lands right if both legs run at one speed,
    and a sprint, a bump from another player or a laggy frame all put a
    timed leg in the sea. Losing sight of the label is a WalkError: the bot
    stops on the dock rather than walk blind.
    """
    if not cfg.walk_to_sen:
        yield True
        return
    badge = _load_template(cfg.prompt_template)
    tag = _load_template(cfg.tag_template)
    missing = [name for name, ok in (("prompt badge", badge is not None),
                                     ("nametag", tag is not None),
                                     ("home position", bool(cfg.home_tag))) if not ok]
    if missing:
        _warn_once(publish, "walk to Sen is on but not calibrated: "
                            f"{', '.join(missing)} (Calibration tab)")
        yield False
        return

    arrived = False
    started = time.monotonic()
    input_ctl.press_keys(cfg.walk_keys)
    try:
        while time.monotonic() - started < max(0.5, cfg.max_walk):
            if prompt_visible(grabber, tracker, badge):
                arrived = True
                break
            time.sleep(0.1)
    finally:
        input_ctl.release_keys(cfg.walk_keys)
    took = time.monotonic() - started
    if arrived:
        publish("info", f"walked to Sen in {took:.1f}s")
    else:
        publish("warn", f"walked {took:.1f}s and Sen's prompt never showed - walking back")
    try:
        yield arrived
    finally:
        _walk_home(input_ctl, grabber, tracker, cfg, tag, publish)


def _walk_home(input_ctl: InputController, grabber: ScreenGrabber, tracker: WindowTracker,
               cfg: BaitConfig, tag: np.ndarray, publish: Publish) -> None:
    home = cfg.home_tag
    tol = max(2, cfg.home_tolerance)
    start = tag_position(grabber, tracker, tag)
    if start is None:
        raise WalkError("cannot see Sen's nametag from here - not walking back blind")
    if _dist(start, home) <= tol:
        return

    # The leg: hold until the label reaches home or passes it.
    began = time.monotonic()
    last_seen = began
    pos = start
    input_ctl.press_keys(cfg.return_keys)
    try:
        while True:
            now = time.monotonic()
            if now - began > max(0.5, cfg.max_walk):
                raise WalkError(f"walked back for {cfg.max_walk:.0f}s and never reached the spot")
            seen = tag_position(grabber, tracker, tag)
            if seen is None:
                if now - last_seen > 1.5:
                    raise WalkError("lost sight of Sen's nametag on the way back")
                time.sleep(0.05)
                continue
            pos, last_seen = seen, now
            if _dist(pos, home) <= tol or _progress(start, home, pos) >= 1.0:
                break
            time.sleep(0.05)
    finally:
        input_ctl.release_keys(cfg.return_keys)
    speed = max(1.0, _dist(start, pos) / max(0.05, time.monotonic() - began))   # px/s, measured

    # Trim: momentum and frame timing leave it a little past or short. Tap
    # toward home, sized by the speed just measured, and look again.
    for _ in range(3):
        time.sleep(0.3)                                    # let momentum die
        seen = tag_position(grabber, tracker, tag)
        if seen is None:
            raise WalkError("lost sight of Sen's nametag while trimming the stop")
        error = _dist(seen, home)
        if error <= tol:
            break
        overshot = _progress(start, home, seen) > 1.0
        input_ctl.hold_key(cfg.walk_keys if overshot else cfg.return_keys,
                           min(0.8, error / speed), delay_after=0.0)
    else:
        publish("warn", f"stopped {error:.0f}px from the fishing spot after trimming")


def reset_template_cache() -> None:
    _TEMPLATE_CACHE.clear()
