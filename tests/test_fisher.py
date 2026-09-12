"""Self-check for the bot's maintenance step. No game, no real input.

Run: python test_fisher.py
"""

import paths  # noqa: F401  - puts the project root on sys.path
import numpy as np

from gpo_macro.config import AppConfig, Region
from gpo_macro.fisher import FishingBot, State
from gpo_macro.stats import EventBus, Stats

WATER = (1180.0, 430.0)      # where the player aimed the cast, over the water
STORE = [1283, 1152]         # the storage prompt, which sits over the dock


class FakeMouse:
    def __init__(self, position):
        self.position = position


class FakeInput:
    """Enough InputController to run maintenance, plus the real position guard."""

    from gpo_macro.input import InputController
    preserved_position = InputController.preserved_position

    def __init__(self):
        self.mouse = FakeMouse(WATER)
        self.clicks, self.keys = [], []
        self.mouse_held = False

    def click(self, point, delay_after=0.0, double=False):
        self.mouse.position = (float(point[0]), float(point[1]))   # clicking moves the cursor
        self.clicks.append(tuple(point))

    def shift_click(self, point, delay_after=0.0):
        self.click(point)

    def press_key(self, name, delay_after=0.0):
        self.keys.append(name)


class FakeGrabber:
    """A hotbar holding one fruit, a banner that answers, a green prompt."""

    def __init__(self, cfg, icon):
        self.cfg, self.icon = cfg, icon
        self.fruit = True
        self.stored = False

    def grab_region(self, tracker, region):
        f = self.cfg.fruit
        if region == f.hotbar_region:
            strip = np.full((64, 800, 3), 45, np.uint8)
            if self.fruit:
                strip[8:8 + self.icon.shape[0], 300:300 + self.icon.shape[1]] = self.icon
            return strip
        if region == f.banner_region:
            band = np.full((36, 480, 3), (60, 25, 12), np.uint8)
            if self.stored:
                band[10:26, 20:460] = (235, 235, 235)      # "New Item <name>"
            return band
        scene = np.full((region.height(), region.width(), 3), (120, 60, 30), np.uint8)
        h, w = scene.shape[:2]
        scene[h // 2 - 20:h // 2 + 20, w // 2 - 90:w // 2 + 90] = (60, 190, 70)
        return scene


def main():
    import cv2

    icon = np.zeros((48, 48, 3), np.uint8)
    icon[:, :] = (120, 40, 20)
    icon[14:34, 14:34] = (200, 160, 60)
    template = str(paths.scratch() / "fruit_icon.png")
    cv2.imwrite(template, icon)
    from gpo_macro.tasks import reset_template_cache
    reset_template_cache()

    cfg = AppConfig()
    cfg.fruit.auto_store = True
    cfg.fruit.drop_duplicates = True
    cfg.fruit.template_path = template
    cfg.fruit.hotbar_region = Region(200, 900, 1000, 964)
    cfg.fruit.banner_region = Region(0, 0, 480, 36)
    cfg.fruit.store_point = STORE
    cfg.fruit.store_wait = 0.4

    bot = FishingBot(cfg, Stats(), EventBus())
    bot._input = FakeInput()
    grabber = FakeGrabber(cfg, icon)

    # Storing clicks the hotbar and the storage prompt, both far from the water.
    grabber.stored = True
    bot._do_maintenance(grabber)

    assert bot._input.clicks, "maintenance never clicked anything"
    assert any(abs(x - STORE[0]) < 250 for x, _y in bot._input.clicks), bot._input.clicks
    # The cast aims wherever the cursor is, so it has to come back to the water.
    assert bot._input.mouse.position == WATER, (
        f"cursor left at {bot._input.mouse.position} instead of {WATER} - the next cast "
        f"would click there, which is the dock, or the bait list under the prompt")
    assert bot.state is State.CAST, bot.state

    # The guard holds even when a task blows up mid-way.
    bot._input = FakeInput()

    class Exploding(FakeGrabber):
        def grab_region(self, tracker, region):
            if region == self.cfg.fruit.hotbar_region:
                raise RuntimeError("capture died")
            return super().grab_region(tracker, region)

    bot._do_maintenance(Exploding(cfg, icon))
    assert bot._input.mouse.position == WATER, bot._input.mouse.position

    # Nothing enabled: maintenance is a no-op and still leaves the aim alone.
    quiet = AppConfig()
    bot = FishingBot(quiet, Stats(), EventBus())
    bot._input = FakeInput()
    bot._do_maintenance(FakeGrabber(quiet, icon))
    assert bot._input.clicks == [] and bot._input.mouse.position == WATER

    # The window vanishing mid-fight is a stop, not a pause: mouse released,
    # reason recorded for the shell, and the pause-on-focus setting is not
    # consulted (there is nothing to resume into).
    class Gone:
        def refresh(self):
            return None

    bot = FishingBot(AppConfig(), Stats(), EventBus())
    bot.cfg.fishing.pause_on_focus_lost = False
    bot._input = FakeInput()
    bot._input.mouse_held = True
    bot._input.release_mouse = lambda: setattr(bot._input, "mouse_held", False)
    bot._input.release_keys = lambda names=None: None
    bot._tracker = Gone()
    bot.state = State.REEL
    bot._check_focus()
    assert bot.stop_reason == "window_lost" and bot._stop_event.is_set(), bot.stop_reason
    assert not bot._input.mouse_held, "mouse still held after the window vanished"

    # Before the button goes down: optional re-equip, optional bait row, then
    # the aim - in that order, because the bait row is a click and the cast
    # fires wherever the cursor is when the hold starts.
    from gpo_macro.fisher import prepare_cast

    class CastInput(FakeInput):
        def __init__(self):
            super().__init__()
            self.moves = []

        def move_to(self, x, y):
            self.moves.append((x, y))
            self.mouse.position = (float(x), float(y))

    aimed = AppConfig()
    aimed.fishing.cast_point = [1500, 700]
    aimed.fishing.equip_every_cast = True
    aimed.fishing.alt_slot_key = "3"
    aimed.bait.select_every_cast = True
    aimed.bait.bait_select = [90, 500]
    ctl = CastInput()
    prepare_cast(ctl, aimed, lambda *a, **k: None)
    assert ctl.keys == ["3", "1"], ctl.keys
    assert ctl.clicks == [(90, 500)], ctl.clicks
    assert ctl.moves == [(1500, 700)] and ctl.mouse.position == (1500.0, 700.0), ctl.moves

    # Everything off: nothing happens, the cursor stays where it was.
    ctl = CastInput()
    prepare_cast(ctl, AppConfig(), lambda *a, **k: None)
    assert ctl.keys == [] and ctl.clicks == [] and ctl.moves == [] and ctl.mouse.position == WATER

    print("all fisher checks passed")


if __name__ == "__main__":
    main()
