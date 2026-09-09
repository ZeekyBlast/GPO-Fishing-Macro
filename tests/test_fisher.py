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

    print("all fisher checks passed")


if __name__ == "__main__":
    main()
