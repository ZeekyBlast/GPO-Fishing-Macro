"""Self-check for devil-fruit storage. No game, no network, no clicks.

Run: python tests/test_tasks.py
"""
import paths  # noqa: F401  - puts the project root on sys.path

import numpy as np

from gpo_macro.config import FruitConfig, Region
from gpo_macro.tasks import (changed_fraction, classify_banner, find_green_prompt,
                             fruit_positions, red_fraction, store_fruits)

# ---------------------------------------------------------------- fixtures

TEMPLATE = str(paths.scratch() / "fruit_icon.png")

ICON = np.zeros((40, 40, 3), np.uint8)
ICON[:, :] = (120, 40, 20)                 # the dark blue fruit square, BGR
ICON[12:28, 12:28] = (200, 160, 60)        # its lighter question-mark blob


PITCH, SLOT = 100, ICON.shape[0]   # slot pitch and icon size in the fake hotbar


def hotbar(fruit_at):
    """A hotbar strip. `fruit_at` are 0-based POSITIONS, not slot numbers -
    GPO renders only the slots you own, so the eighth icon on screen can be
    the one bound to the 0 key."""
    strip = np.full((64, PITCH * 8, 3), 45, np.uint8)
    for index in fruit_at:
        x = index * PITCH + (PITCH - SLOT) // 2
        strip[8:8 + SLOT, x:x + SLOT] = ICON
    return strip


def banner(text_bgr=None):
    """The top-of-screen strip: dark navy, optionally with coloured text on it."""
    strip = np.full((36, 480, 3), (60, 25, 12), np.uint8)
    if text_bgr is not None:
        for x in range(20, 460, 18):        # scattered glyph-ish blocks
            strip[10:26, x:x + 11] = text_bgr
    return strip


EMPTY = banner()
REFUSED = banner((60, 70, 235))             # "You can only store one of each fruit!" in red
STORED = banner((235, 235, 235))            # "New Item <Zushi>" in white


# ------------------------------------------------------------ hotbar search

# Fruits are located by position, never by slot number.
found = fruit_positions(hotbar([6]), ICON, 0.8)
assert len(found) == 1, found
assert abs(found[0][0] - (6 * PITCH + PITCH // 2)) <= 2, found

# Adjacent icons must come back as two, not one merged peak.
found = fruit_positions(hotbar([2, 3, 7]), ICON, 0.8)
assert len(found) == 3, found
assert [x for x, _y, _s in found] == sorted(x for x, _y, _s in found), found

assert fruit_positions(hotbar([]), ICON, 0.8) == []
assert fruit_positions(np.zeros((4, 4, 3), np.uint8), ICON, 0.8) == []


# ------------------------------------------------------------ prompt search

# The prompt is found, not assumed to sit under a hand-placed point.
scene = np.full((180, 500, 3), (120, 60, 30), np.uint8)
scene[40:90, 150:380] = (60, 190, 70)                     # the Store Fruit slab
spot = find_green_prompt(scene)
assert spot is not None and abs(spot[0] - 265) < 6 and abs(spot[1] - 65) < 6, spot
assert find_green_prompt(np.full((180, 500, 3), (120, 60, 30), np.uint8)) is None
# A few stray green pixels are not a button.
speck = np.full((180, 500, 3), (120, 60, 30), np.uint8)
speck[10:14, 10:14] = (60, 190, 70)
assert find_green_prompt(speck) is None


# --------------------------------------------------------- banner outcomes

assert red_fraction(REFUSED) > 0.02, red_fraction(REFUSED)
assert red_fraction(STORED) < 0.02, red_fraction(STORED)
assert red_fraction(EMPTY) < 0.02, red_fraction(EMPTY)

assert classify_banner(EMPTY, EMPTY) == "none"
assert classify_banner(EMPTY, REFUSED) == "refused"
assert classify_banner(EMPTY, STORED) == "stored"
# A banner already on screen when we start must not be read as this store's result.
assert classify_banner(STORED, STORED) == "none"
assert changed_fraction(EMPTY, EMPTY) == 0.0


# ------------------------------------------------------- the storage walk

class FakeInput:
    """Records keys and clicks. Pressing a slot key equips that slot - clicking
    a slot does not, which is what GPO actually does."""

    def __init__(self, screen=None):
        self.keys, self.clicks = [], []
        self.screen = screen

    def press_key(self, name, delay_after=0.0):
        self.keys.append(name)
        if self.screen is not None:
            self.screen.on_key(name)

    def click(self, point, delay_after=0.0):
        self.clicks.append(tuple(point))
        if self.screen is not None:
            self.screen.on_click(tuple(point))


class FakeScreen:
    """A hotbar whose slots answer to arbitrary keys, a proximity prompt that
    only shows while a fruit is held, and a scripted banner.

    `fruit_keys` are the KEYS holding fruits, deliberately not contiguous and
    not matching screen order - GPO draws only owned slots while each item
    keeps its binding, so the eighth icon on screen can be the `0` key.
    """

    def __init__(self, cfg, fruit_keys, outcomes, in_range=True, drop_works=True):
        self.cfg = cfg
        self.fruit = list(fruit_keys)
        self.outcomes = list(outcomes)
        self.in_range = in_range
        self.drop_works = drop_works
        self.held = None
        self.banner = EMPTY

    def on_key(self, name):
        if name == self.cfg.drop_key:
            if self.drop_works and self.held in self.fruit:
                self.fruit.remove(self.held)          # dropped item despawns
                self.held = None
            return
        self.held = name

    def on_click(self, point):
        self.banner = self.outcomes.pop(0) if self.outcomes else EMPTY
        if self.banner is STORED and self.held in self.fruit:
            self.fruit.remove(self.held)              # stored item leaves the hotbar

    @property
    def holding_fruit(self):
        return self.held in self.fruit

    def grab_region(self, tracker, region):
        if region == self.cfg.hotbar_region:
            return hotbar(range(len(self.fruit)))
        if region == self.cfg.banner_region:
            return self.banner
        scene = np.full((region.height(), region.width(), 3), (120, 60, 30), np.uint8)
        if self.in_range and self.holding_fruit:      # the prompt needs both
            h, w = scene.shape[:2]
            scene[h // 2 - 20:h // 2 + 20, w // 2 - 90:w // 2 + 90] = (60, 190, 70)
        return scene


def config(**kw):
    base = dict(auto_store=True, template_path=TEMPLATE,
                hotbar_region=Region(200, 900, 200 + PITCH * 8, 964),
                banner_region=Region(0, 0, 480, 36),
                store_point=[600, 500], store_wait=0.4)
    base.update(kw)
    return FruitConfig(**base)


def run(fruit_keys, outcomes, in_range=True, drop_works=True, **cfg_kw):
    cfg = config(**cfg_kw)
    screen = FakeScreen(cfg, fruit_keys, outcomes, in_range, drop_works)
    input_ctl = FakeInput(screen)
    events = []
    stored, dropped = store_fruits(
        input_ctl, screen, None, cfg, "1",
        lambda kind, message, **data: events.append((kind, message)),
        shot_dir=str(paths.scratch() / "fruit-shots"))
    return stored, dropped, input_ctl, screen, events


import cv2  # noqa: E402  - only needed to lay down the template fixture
cv2.imwrite(TEMPLATE, ICON)
from gpo_macro.tasks import reset_template_cache  # noqa: E402
reset_template_cache()

# A fruit on the "0" key - the eighth slot on screen. Reaching it means trying
# keys, not deriving one from a screen position.
stored, dropped, input_ctl, screen, events = run(["0"], [STORED])
assert (stored, dropped) == (1, 0), (stored, dropped, events)
assert "0" in input_ctl.keys, input_ctl.keys
assert input_ctl.keys[-1] == "1", f"rod not re-equipped: {input_ctl.keys}"
assert screen.fruit == [], screen.fruit

# Slots are equipped by key, never by clicking them: the only click is the prompt.
assert len(input_ctl.clicks) == 1, input_ctl.clicks
click_x, click_y = input_ctl.clicks[0]
assert abs(click_x - 600) < 3 and abs(click_y - 500) < 3, input_ctl.clicks

# A duplicate is dropped - and only after the game itself refused it, while
# the fruit is still held from the store attempt.
stored, dropped, input_ctl, screen, events = run(["3"], [REFUSED])
assert (stored, dropped) == (0, 1), (stored, dropped, events)
assert input_ctl.keys.index("3") < input_ctl.keys.index("backspace"), input_ctl.keys
assert screen.fruit == [], "duplicate still in the hotbar"
assert any("dropped it" in m for _k, m in events), events

# Out of range: the prompt never shows, so nothing is clicked and nothing is
# destroyed - and it says so rather than blaming the slot.
stored, dropped, input_ctl, screen, events = run(["4"], [STORED], in_range=False)
assert (stored, dropped) == (0, 0)
assert input_ctl.clicks == [] and "backspace" not in input_ctl.keys, input_ctl.keys
assert screen.fruit == ["4"], "fruit destroyed without the game refusing it"
assert any("never appeared" in m for _k, m in events), events

# Silence is not proof of a duplicate either.
stored, dropped, input_ctl, screen, events = run(["4"], [EMPTY])
assert (stored, dropped) == (0, 0)
assert "backspace" not in input_ctl.keys, input_ctl.keys
assert screen.fruit == ["4"], screen.fruit

# Drop turned off disables storing too, before a single key is pressed.
stored, dropped, input_ctl, screen, events = run(["2", "5"], [STORED, REFUSED],
                                                 drop_duplicates=False)
assert (stored, dropped) == (0, 0), (stored, dropped)
assert input_ctl.keys == [] and input_ctl.clicks == [], (input_ctl.keys, input_ctl.clicks)
assert screen.fruit == ["2", "5"], screen.fruit
assert any("does nothing while" in m for _k, m in events), events

# Three fruits on scattered keys, mixed outcomes.
stored, dropped, input_ctl, screen, events = run(["2", "6", "0"],
                                                 [REFUSED, STORED, REFUSED])
assert (stored, dropped) == (1, 2), (stored, dropped, events)
assert screen.fruit == [], screen.fruit

# A drop key that does nothing is noticed once, not retried forever.
stored, dropped, input_ctl, screen, events = run(["3"], [REFUSED] * 4, drop_works=False)
assert (stored, dropped) == (0, 0), (stored, dropped)
assert input_ctl.keys.count("backspace") == 1, input_ctl.keys
assert any("drop did not take" in m for _k, m in events), events

# Empty hotbar: no keys pressed at all. This runs after every single catch, so
# the common case must not cost ten key presses.
stored, dropped, input_ctl, screen, events = run([], [])
assert (stored, dropped, input_ctl.keys, input_ctl.clicks, events) == (0, 0, [], [], [])

# Uncalibrated: one warning, nothing pressed, nothing clicked.
cfg = FruitConfig(auto_store=True, template_path=TEMPLATE)
input_ctl = FakeInput()
events = []
store_fruits(input_ctl, FakeScreen(cfg, ["3"], []), None, cfg, "1",
             lambda kind, message, **d: events.append((kind, message)))
assert input_ctl.clicks == [] and input_ctl.keys == [], input_ctl.keys

print("all fruit-storage checks passed")
