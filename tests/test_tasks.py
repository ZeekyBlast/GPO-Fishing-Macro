"""Self-check for devil-fruit storage and bait upkeep. No game, no clicks.

Run: python tests/test_tasks.py
"""
import paths  # noqa: F401  - puts the project root on sys.path

import numpy as np

from gpo_macro.config import BaitConfig, FruitConfig, Region
from gpo_macro.tasks import (WalkError, box_around, buy_bait, changed_fraction,
                             classify_banner, craft_bait, find_green_prompt,
                             fruit_positions, green_fraction, red_fraction, store_fruits,
                             upkeep_bait, walked_to_sen)

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

    def click(self, point, delay_after=0.0, double=False):
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


# =========================================================== bait upkeep

PLANKS = (120, 60, 30)
RED_TEXT = (60, 70, 235)
GREEN_TEXT = (70, 220, 80)
OUT_OF_FISH = banner(RED_TEXT)              # "You dont have any eligible materials to add!"

assert green_fraction(banner(GREEN_TEXT)) > 0.03
assert green_fraction(banner(RED_TEXT)) == 0.0
assert green_fraction(banner()) == 0.0


def bait_config(**kw):
    base = dict(auto_craft=True, menu_delay=0.0,
                dialog_yes=[400, 300], menu_region=Region(50, 50, 650, 750),
                craft_recipe=[120, 400], craft_add=[500, 200], craft_pick=[900, 120],
                craft_counter_region=Region(480, 230, 540, 250),
                craft_button=[500, 700], craft_close=[640, 60], dialog_end=[350, 900],
                craft_slider=[300, 560], craft_slider_end=[560, 560], craft_all=[300, 640],
                bait_select=[900, 500],
                shop_quantity=[400, 320], shop_confirm=[300, 420], shop_cancel=[500, 420],
                buy_amount=100, shop_hold=0.0, walk_to_sen=False, max_walk=0.5)
    base.update(kw)
    return BaitConfig(**base)


BANNER = Region(0, 0, 480, 36)


class CraftInput(FakeInput):
    """Everything the upkeep routines can do to the keyboard, recorded."""

    def __init__(self, screen):
        super().__init__(screen)
        self.typed, self.holds, self.down = [], [], []

    def tap_escape(self, delay_after=0.0):
        self.keys.append("esc")
        self.screen.on_key("esc")

    def hold_key(self, names, seconds, delay_after=0.0):
        self.holds.append((names, round(seconds, 2)))
        self.screen.on_hold(names, seconds)

    def press_keys(self, names):
        self.down.append(("down", names))
        self.screen.on_key(names)

    def release_keys(self, names=None):
        self.down.append(("up", names))
        self.screen.on_release(names)

    def type_text(self, text, delay_after=0.0):
        self.typed.append(text)
        self.screen.on_type(text)

    def drag(self, start, end, delay_after=0.0):
        self.holds.append(("drag", tuple(start), tuple(end)))
        self.screen.on_drag(tuple(start), tuple(end))


class SenScreen:
    """Blacksmith Sen, as the routine sees him.

    T opens the dialogue, Yes opens the menu, + toggles a list of eligible
    fish beside the menu, clicking a row moves that fish into the slot, CRAFT
    consumes a full slot, the red X closes. The material counter is red until
    the slot is full and green after, and a red line appears just above the
    menu when + is pressed with no fish left - all read, never counted.
    """

    def __init__(self, cfg, fish, need=1, prompt=True, walk_steps=0, stacked=False):
        self.cfg, self.fish, self.need, self.prompt = cfg, fish, need, prompt
        self.stacked = stacked              # all the fish are one kind: CRAFT asks how many
        self.dialog = self.menu = self.selected = self.talking = False
        self.picker = self.error = self.asking = False
        self.slider_at_end = False
        self.slot = self.crafted = 0
        self.banner = EMPTY
        self.steps_to_sen = walk_steps     # grab_full calls before the badge shows
        self.badge = None

    def on_key(self, name):
        if name == self.cfg.talk_key and self.prompt and not self.talking:
            self.dialog = self.talking = True
        elif name == "esc":
            self.dialog = self.menu = False

    def on_hold(self, names, seconds):
        pass

    def on_release(self, names):
        pass

    def on_drag(self, start, end):
        if self.asking and start == tuple(self.cfg.craft_slider)                 and end == tuple(self.cfg.craft_slider_end):
            self.slider_at_end = True

    def on_type(self, text):
        pass

    def on_click(self, point):
        cfg = self.cfg
        if self.dialog and point == tuple(cfg.dialog_yes):
            self.dialog, self.menu = False, True
        elif self.talking and not self.menu and point == tuple(cfg.dialog_end):
            self.talking = False                  # the "..." bubble; HUD comes back
        elif not self.menu:
            return
        elif point == tuple(cfg.craft_recipe):
            self.selected = True
        elif point == tuple(cfg.craft_add) and self.selected:
            if self.picker:
                self.picker = False
            elif self.fish > 0:
                self.picker = True
            else:
                self.error = True
        elif self.picker and abs(point[0] - cfg.craft_pick[0]) < 40 \
                and abs(point[1] - cfg.craft_pick[1]) < 40:
            if self.fish > 0 and self.slot < self.need:
                self.fish -= 1
                self.slot += 1
        elif self.asking and cfg.craft_all and abs(point[0] - cfg.craft_all[0]) < 90                 and abs(point[1] - cfg.craft_all[1]) < 40:
            made = self.fish + 1 if self.slider_at_end else 1     # the slot's fish plus the rest
            self.crafted += made
            self.fish -= made - 1
            self.slot = 0
            self.asking = self.picker = self.slider_at_end = False
        elif abs(point[0] - cfg.craft_button[0]) < 130 and abs(point[1] - cfg.craft_button[1]) < 60:
            if self.slot == self.need and self.stacked and self.fish > 0:
                self.asking = True                  # "1 Legendary Fish Bait", slider, Craft Selected
            elif self.slot == self.need:
                self.crafted += 1
                self.slot = 0
                self.picker = False
        elif point == tuple(cfg.craft_close):
            self.menu = False

    def grab_region(self, tracker, region):
        cfg = self.cfg
        if region == BANNER:
            return self.banner
        if region == box_around(cfg.dialog_yes):
            return np.full((48, 48, 3), (30, 30, 30) if self.dialog else PLANKS, np.uint8)
        if region == box_around(cfg.dialog_end):
            return np.full((48, 48, 3), (30, 30, 30) if self.talking else PLANKS, np.uint8)
        if region == cfg.menu_region:
            return np.full((region.height(), region.width(), 3),
                           (25, 25, 25) if self.menu else PLANKS, np.uint8)
        if region == cfg.craft_counter_region:
            frame = np.full((region.height(), region.width(), 3), (25, 25, 25), np.uint8)
            if self.menu and self.selected:
                frame[6:14, 10:50] = GREEN_TEXT if self.slot == self.need else RED_TEXT
            return frame
        strip = Region(cfg.menu_region.x1, cfg.menu_region.y1 - 70,
                       cfg.menu_region.x2, cfg.menu_region.y1)
        if region == strip:
            frame = np.full((region.height(), region.width(), 3), PLANKS, np.uint8)
            if self.error:
                frame[20:40, 100:600] = RED_TEXT
            return frame
        frame = np.full((region.height(), region.width(), 3), PLANKS, np.uint8)
        h, w = frame.shape[:2]
        centre_x = (region.x1 + region.x2) // 2
        if self.menu and abs(centre_x - cfg.craft_button[0]) < 5:
            frame[h // 2 - 15:h // 2 + 15, w // 2 - 60:w // 2 + 60] = (60, 190, 70)  # CRAFT
        if self.menu and self.asking and cfg.craft_all and abs(centre_x - cfg.craft_all[0]) < 5                 and abs((region.y1 + region.y2) // 2 - cfg.craft_all[1]) < 5:
            frame[h // 2 - 12:h // 2 + 12, w // 2 - 50:w // 2 + 50] = (60, 190, 70)  # Craft Selected
        if region == box_around(cfg.craft_pick):
            return np.full((48, 48, 3), (30, 30, 30) if self.menu and self.picker else PLANKS,
                           np.uint8)
        return frame

    def grab_full(self):
        frame = np.full((300, 400, 3), PLANKS, np.uint8)
        if self.steps_to_sen <= 0 and self.badge is not None:
            frame[100:100 + self.badge.shape[0], 200:200 + self.badge.shape[1]] = self.badge
        self.steps_to_sen -= 1
        return frame


def craft(fish, need=1, prompt=True, stacked=False, **kw):
    cfg = bait_config(**kw)
    screen = SenScreen(cfg, fish, need, prompt=prompt, stacked=stacked)
    input_ctl = CraftInput(screen)
    events = []
    made = craft_bait(input_ctl, screen, None, cfg, BANNER,
                      lambda kind, message, **data: events.append((kind, message)))
    return made, input_ctl, screen, events


# Three legendary fish, one each: crafts three, stops on the red banner, closes.
made, input_ctl, screen, events = craft(fish=3)
assert made == 3 and screen.crafted == 3, (made, screen.crafted, events)
assert screen.fish == 0 and not screen.menu, (screen.fish, screen.menu)
assert not screen.talking, "the ... bubble was left up - T would do nothing next pass"
# 3 opens, then the one the red line answers - no toggle retry after a refusal.
assert input_ctl.clicks.count(tuple(bait_config().craft_add)) == 4, input_ctl.clicks
assert screen.error, "never asked + with no fish left"
assert "esc" not in input_ctl.keys, input_ctl.keys                # closed by the X
assert any("crafted 3" in m for _k, m in events), events

# Five of one fish: CRAFT asks how many. The slider goes to the end and the
# whole stack is taken in one pass - one CRAFT, one drag, one Craft Selected.
made, input_ctl, screen, events = craft(fish=5, stacked=True)
assert screen.crafted == 5 and screen.fish == 0, (screen.crafted, screen.fish, events)
assert ("drag", (300, 560), (560, 560)) in input_ctl.holds, input_ctl.holds
assert any("1 whole stack" in m for _k, m in events), events

# The stack dialog not calibrated: the pass stops and says which button is missing.
made, input_ctl, screen, events = craft(fish=5, stacked=True, craft_all=[])
assert screen.crafted == 0 and screen.fish == 4, (screen.crafted, screen.fish)
assert any("quantity dialog calibrated" in m for _k, m in events), events

# Rare bait needs two fish per craft: five fish make two, the odd one stays.
made, input_ctl, screen, events = craft(fish=5, need=2)
assert made == 2 and screen.fish + screen.slot == 1, (made, screen.fish, screen.slot, events)

# No fish at all: nothing crafted, said plainly, menu still closed.
made, input_ctl, screen, events = craft(fish=0)
assert made == 0 and not screen.menu
assert any("no fish" in m for _k, m in events), events

# Sen not in range: T does nothing, so Yes is never clicked - that click would
# be a cast into the dock.
made, input_ctl, screen, events = craft(fish=3, prompt=False)
assert made == 0 and input_ctl.clicks == [], input_ctl.clicks
assert any("did not appear" in m for _k, m in events), events

# Not calibrated: one warning, no input at all.
made, input_ctl, screen, events = craft(fish=3, craft_add=[])
assert made == 0 and input_ctl.keys == [] and input_ctl.clicks == []
assert any("not calibrated" in m and "craft add" in m for _k, m in events), events


# ------------------------------------------------------------- the walk

BADGE = np.full((30, 30, 3), (235, 235, 235), np.uint8)
BADGE[8:22, 13:17] = (40, 40, 40)                         # the T's stem
BADGE[8:12, 8:22] = (40, 40, 40)                          # its bar
TAG = np.full((20, 80, 3), (20, 20, 20), np.uint8)
TAG[4:16, 6:74] = (240, 240, 240)                         # "Blacksmith Sen", roughly
TAG[8:12, 20:60] = (20, 20, 20)
PROMPT = str(paths.scratch() / "prompt_t.png")
TAGFILE = str(paths.scratch() / "sen_tag.png")
cv2.imwrite(PROMPT, BADGE)
cv2.imwrite(TAGFILE, TAG)
reset_template_cache()

import time as _time  # noqa: E402


class Dock:
    """A one-dimensional dock, seen through the camera.

    p is the character's place along it: 0 at the fishing spot, 100 at Sen.
    Held keys move it at a rate per second - the way back sprints, which is
    exactly what put the timed leg in the sea. Sen's nametag draws at
    x = 600 - 4p, so it sits at 600 from the spot and 200 from Sen; the T
    badge shows within his prompt range. Nothing is counted: the walk only
    ever sees the frame.
    """

    IN, BACK, TAP = 100.0, 260.0, 100.0     # p per second: leg in, leg back, trim taps

    def __init__(self, cfg, tag_visible=True, prompt=True):
        self.cfg, self.tag_visible, self.prompt = cfg, tag_visible, prompt
        self.p = 0.0
        self.vel = 0.0
        self.since = _time.monotonic()

    def _settle(self):
        now = _time.monotonic()
        self.p += self.vel * (now - self.since)
        self.since = now

    def on_key(self, names):
        self._settle()
        if names == self.cfg.walk_keys:
            self.vel = self.IN
        elif names == self.cfg.return_keys:
            self.vel = -self.BACK

    def on_release(self, names):
        self._settle()
        self.vel = 0.0

    def on_hold(self, names, seconds):
        self._settle()
        self.p += (self.TAP if names == self.cfg.walk_keys else -self.TAP) * seconds

    def on_click(self, point):
        pass

    def grab_region(self, tracker, region):
        return np.full((region.height(), region.width(), 3), PLANKS, np.uint8)

    def grab_client(self, tracker):
        self._settle()
        frame = np.full((300, 1000, 3), PLANKS, np.uint8)
        if self.tag_visible:
            x = int(round(600 - 4 * self.p))
            if 0 <= x - 40 and x + 40 <= 1000:
                frame[90:110, x - 40:x + 40] = TAG
        if self.prompt and self.p >= 90:
            frame[200:230, 200:230] = BADGE
        return frame


def walk(body_raises=False, tag_visible=True, prompt=True, **kw):
    kw = dict(walk_to_sen=True, prompt_template=PROMPT, tag_template=TAGFILE,
              home_tag=[600, 100], home_tolerance=12, walk_keys="w+d",
              return_keys="s+a", max_walk=3.0) | kw
    cfg = bait_config(**kw)
    dock = Dock(cfg, tag_visible, prompt)
    input_ctl = CraftInput(dock)
    events = []
    arrived = None
    error = None
    try:
        with walked_to_sen(input_ctl, dock, None, cfg,
                           lambda kind, message, **data: events.append((kind, message))) as there:
            arrived = there
            if body_raises:
                raise RuntimeError("craft blew up")
    except RuntimeError as exc:
        error = exc
    return arrived, dock, input_ctl, events, error


# In until the badge, back until the nametag is home - and home is a
# measurement, so the sprint on the way back does not matter.
arrived, dock, input_ctl, events, error = walk()
assert arrived is True and error is None, (events, error)
assert input_ctl.down[:2] == [("down", "w+d"), ("up", "w+d")], input_ctl.down
assert ("down", "s+a") in input_ctl.down, input_ctl.down
assert abs(dock.p) <= 12 / 4 + 1, f"ended at p={dock.p:.1f}, not the fishing spot"
assert any("walked to Sen" in m for _k, m in events), events

# The prompt never shows: gives up at max_walk, still steers back home.
arrived, dock, input_ctl, events, error = walk(prompt=False, max_walk=0.6)
assert arrived is False and error is None, (events, error)
assert abs(dock.p) <= 6, dock.p          # 24 px on the fake dock; the loop runs on wall time
assert any("never showed" in m for _k, m in events), events

# The craft raising must not leave the character standing at Sen.
arrived, dock, input_ctl, events, error = walk(body_raises=True)
assert arrived is True and isinstance(error, RuntimeError) and not isinstance(error, WalkError)
assert abs(dock.p) <= 6, dock.p          # 24 px on the fake dock; the loop runs on wall time

# No nametag in sight: never walks blind - keys released, WalkError raised.
arrived, dock, input_ctl, events, error = walk(tag_visible=False)
assert isinstance(error, WalkError), error
assert input_ctl.down[-1][0] == "up" or ("down", "s+a") not in input_ctl.down, input_ctl.down
assert dock.p >= 85, f"walked back blind to p={dock.p:.1f}"

# Not calibrated: one warning, nothing pressed.
arrived, dock, input_ctl, events, error = walk(home_tag=[])
assert arrived is False and input_ctl.down == [] and input_ctl.holds == []
assert any("not calibrated" in m and "home position" in m for _k, m in events), events

# Walking off means no leg at all.
arrived, dock, input_ctl, events, error = walk(walk_to_sen=False)
assert arrived is True and input_ctl.down == [] and input_ctl.holds == []


# --------------------------------------------------------------- the buy

class BarrelScreen(SenScreen):
    """The bait barrel: hold the key and a dialog with a quantity box appears."""

    def __init__(self, cfg, opens=True, refuses=False):
        super().__init__(cfg, fish=0)
        self.opens, self.refuses = opens, refuses
        self.open = False
        self.text = ""

    def on_hold(self, names, seconds):
        if names == self.cfg.shop_key and self.opens:
            self.open = True

    def on_type(self, text):
        if self.open:
            self.text += text

    def on_click(self, point):
        if self.open and point == tuple(self.cfg.shop_confirm):
            self.banner = banner(RED_TEXT) if self.refuses else EMPTY
            self.open = False                # the dialog closes itself on Confirm

    def grab_region(self, tracker, region):
        if region == BANNER:
            return self.banner
        if region == box_around(self.cfg.shop_quantity):
            frame = np.full((48, 48, 3), (30, 30, 30) if self.open else PLANKS, np.uint8)
            if self.text:
                frame[20:28, 8:8 + 4 * len(self.text)] = (235, 235, 235)
            return frame
        return np.full((region.height(), region.width(), 3), PLANKS, np.uint8)


def buy(opens=True, refuses=False, **kw):
    cfg = bait_config(auto_craft=False, auto_buy=True, **kw)
    screen = BarrelScreen(cfg, opens, refuses)
    input_ctl = CraftInput(screen)
    events = []
    ok = buy_bait(input_ctl, screen, None, cfg, BANNER,
                  lambda kind, message, **data: events.append((kind, message)))
    return ok, input_ctl, screen, events


ok, input_ctl, screen, events = buy()
assert ok and screen.text == "100", (ok, screen.text, events)
# The dialog closed itself on Confirm, so Cancel is not clicked - on the dock that is a cast.
assert tuple(bait_config().shop_cancel) not in input_ctl.clicks, input_ctl.clicks
assert "esc" not in input_ctl.keys

# Not at the barrel: nothing is typed. Typing with no box focused goes to the game.
ok, input_ctl, screen, events = buy(opens=False)
assert not ok and input_ctl.typed == [] and input_ctl.clicks == [], (input_ctl.typed, input_ctl.clicks)

ok, input_ctl, screen, events = buy(refuses=True)
assert not ok and any("refused" in m for _k, m in events), events


# -------------------------------------------------------- one mode at a time

cfg = bait_config(auto_buy=True, auto_craft=True)
screen = SenScreen(cfg, fish=3)
input_ctl = CraftInput(screen)
events = []
upkeep_bait(input_ctl, screen, None, cfg, BANNER,
            lambda kind, message, **data: events.append((kind, message)))
assert input_ctl.keys == [] and input_ctl.clicks == [], (input_ctl.keys, input_ctl.clicks)
assert any("both on" in m for _k, m in events), events

# Craft alone ends by putting the bait back on the rod.
cfg = bait_config()
screen = SenScreen(cfg, fish=1)
input_ctl = CraftInput(screen)
upkeep_bait(input_ctl, screen, None, cfg, BANNER, lambda *a, **k: None)
assert input_ctl.clicks[-1] == tuple(cfg.bait_select), input_ctl.clicks

print("test_tasks: ok")
