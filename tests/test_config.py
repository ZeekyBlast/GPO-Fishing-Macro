"""Self-check for settings merging and persistence.

A patch from the shell lands while the bot thread is reading the same
config object. Nothing the bot reads may ever be half-updated: a point is
two numbers, a Region is four, and a reader holding either must see the old
value whole or the new value whole.

Run: python tests/test_config.py
"""

import paths  # noqa: F401  - puts the project root on sys.path

import dataclasses
import json

from gpo_macro.config import AppConfig, Region, dict_into_dataclass


def main() -> int:
    cfg = AppConfig()

    # A point is swapped, never emptied and refilled: a reader that took the
    # list a moment ago keeps a whole point, and cast_point[0] cannot raise.
    cfg.fishing.cast_point = [100, 200]
    held = cfg.fishing.cast_point
    dict_into_dataclass(cfg, {"fishing": {"cast_point": [300, 400]}})
    assert cfg.fishing.cast_point == [300, 400], cfg.fishing.cast_point
    assert held == [100, 200], f"reader's point was mutated underneath it: {held}"

    # A skipped pick is stored as empty, by the same swap.
    dict_into_dataclass(cfg, {"fishing": {"cast_point": []}})
    assert cfg.fishing.cast_point == []

    # A Region is a value: four numbers that only mean something together.
    cfg.scan_region = Region(10, 10, 200, 300)
    held_region = cfg.scan_region
    dict_into_dataclass(cfg, {"scan_region": {"x1": 500, "y1": 500, "x2": 900, "y2": 900}})
    assert cfg.scan_region == Region(500, 500, 900, 900), cfg.scan_region
    assert held_region == Region(10, 10, 200, 300), (
        f"reader's region was mutated underneath it: {held_region}")
    try:
        cfg.scan_region.x1 = 1
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("a Region must be immutable - it is read as a whole")

    # A partial patch keeps the other three numbers, and a nested Region
    # inside a section gets the same treatment.
    dict_into_dataclass(cfg, {"scan_region": {"x2": 950}})
    assert cfg.scan_region == Region(500, 500, 950, 900), cfg.scan_region
    dict_into_dataclass(cfg, {"bait": {"menu_region": {"x1": 1, "y1": 2, "x2": 30, "y2": 40}}})
    assert cfg.bait.menu_region == Region(1, 2, 30, 40), cfg.bait.menu_region

    # Scalars land, neighbours stay, unknown keys are dropped rather than
    # crashing an older build reading a newer file.
    dict_into_dataclass(cfg, {"controller": {"bar_lead": 0.3, "no_such_knob": 1},
                              "no_such_section": {"x": 1}})
    assert cfg.controller.bar_lead == 0.3
    assert cfg.controller.fish_lead == AppConfig().controller.fish_lead

    # Everything survives the file.
    path = paths.scratch() / "settings.json"
    cfg.save(path)
    back = AppConfig.load(path)
    assert back.scan_region == cfg.scan_region
    assert back.bait.menu_region == cfg.bait.menu_region
    assert back.fishing.cast_point == cfg.fishing.cast_point
    assert back.controller.bar_lead == 0.3
    assert json.loads(path.read_text(encoding="utf-8"))["scan_region"] == {
        "x1": 500, "y1": 500, "x2": 950, "y2": 900}

    print("all config checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
