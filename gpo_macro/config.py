"""Typed, JSON-persisted configuration.

All screen coordinates are stored RELATIVE to the Roblox window's client area,
so the macro keeps working if the window is moved. They are translated to
absolute screen coordinates at grab/click time (see capture.py / input.py).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class Region:
    """Rectangle relative to the Roblox client area: (x1, y1) top-left, (x2, y2) bottom-right."""

    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0

    def valid(self) -> bool:
        return self.x2 > self.x1 and self.y2 > self.y1

    def width(self) -> int:
        return max(0, self.x2 - self.x1)

    def height(self) -> int:
        return max(0, self.y2 - self.y1)


@dataclass
class HotkeysConfig:
    start_stop: str = "f6"       # toggle the bot (F1 is Roblox's menu on some setups)
    panic: str = "f8"            # hard-stop everything instantly


@dataclass
class DetectionConfig:
    # The only two colours detection needs, in RGB, measured from captures:
    # - bar_blue is the gauge interior's background,
    # - track_gray is both the pill border and the player's bar.
    # The fish marker has no colour entry on purpose - it is found as the thin
    # run that is neither of those, so it stays found when the game recolours
    # it on contact with the bar.
    bar_blue: list[int] = field(default_factory=lambda: [85, 170, 255])
    track_gray: list[int] = field(default_factory=lambda: [25, 25, 25])
    color_tolerance: int = 20             # per-channel tolerance for matching
    min_bar_pixels: int = 80               # blue pixels auto-calibration needs to
    # accept a blob as the gauge. Bite detection does not use a pixel count -
    # it asks find_bar for a full reading, so scenery cannot fake a gauge.
    black_screen_threshold: float = 0.5    # fraction of near-black pixels => loading screen
    scan_loop_delay: float = 0.02          # sleep between detection frames (seconds)


@dataclass
class ControllerConfig:
    """Knobs for the bang-bang hold/release law in controller.py.

    Holding raises the bar, releasing drops it. The controller compares where
    the bar will be in bar_lead seconds against where the fish will be in
    fish_lead seconds, and holds whenever the bar is the lower of the two.
    """

    invert: bool = False       # flip raise/drop if the bar moves the wrong way
    bar_lead: float = 0.24     # seconds of bar momentum to anticipate; this is
    # the brake - raise it if the bar overshoots the fish, lower it if it
    # stalls short
    fish_lead: float = 0.0     # seconds of fish motion to anticipate. Off by
    # default: leading a target that reverses direction costs more than it
    # gains. Raise it only if the fish in question moves predictably


@dataclass
class FishingConfig:
    equip_rod: bool = True
    rod_key: str = "1"
    cast_hold_duration: float = 0.05       # seconds to hold LMB for a cast
    post_cast_delay: float = 0.7           # settle time after releasing the cast
    recast_timeout: float = 25.0           # no bite within this window => recast
    loot_delay: float = 0.15               # pause after the minigame ends
    reel_max_duration: float = 60.0        # failsafe: reel longer than this => recast
    pause_on_focus_lost: bool = True       # auto-pause when Roblox is not foreground
    action_jitter: float = 0.10            # +/- fraction randomized onto delays


@dataclass
class PointMap:
    """Named click points (window-relative [x, y]). Empty list = not calibrated."""

    def missing(self) -> list[str]:
        return [name for name in self.__dict__ if not getattr(self, name)]


@dataclass
class BaitConfig(PointMap):
    auto_buy: bool = False
    loops_per_purchase: int = 100          # buy bait every N catches
    auto_craft: bool = False
    loops_per_craft: int = 5               # craft bait every N catches
    crafts_per_cycle: int = 40
    # Click points: shop menu
    shop_open: list[int] = field(default_factory=list)
    shop_buy_common: list[int] = field(default_factory=list)
    shop_confirm: list[int] = field(default_factory=list)
    shop_close: list[int] = field(default_factory=list)
    # Click points: crafting menu
    craft_open: list[int] = field(default_factory=list)
    craft_select_recipe: list[int] = field(default_factory=list)
    craft_select_amount: list[int] = field(default_factory=list)
    craft_button: list[int] = field(default_factory=list)
    craft_confirm: list[int] = field(default_factory=list)
    craft_close: list[int] = field(default_factory=list)


@dataclass
class FruitConfig(PointMap):
    """Devil-fruit storage.

    Every fruit shares one hotbar icon, so a single template finds any of them
    and the macro cannot know which fruit it holds until the game says so. The
    sequence is: find the fruit icons in the hotbar, click one to equip it,
    click the storage prompt, and read the top-of-screen banner for the answer.

    Fruits are located by pixel position rather than slot number. GPO renders
    only the slots you own while each item keeps its original key binding, so
    a hotbar reading 1 2 3 4 5 6 7 0 has its eighth icon on the `0` key -
    position cannot tell you which key equips a slot, and clicking it makes
    the question moot.
    """

    auto_store: bool = False
    template_path: str = "templates/fruit.png"   # snipped from a hotbar slot
    match_threshold: float = 0.80
    hotbar_region: Region = field(default_factory=Region)   # the full row of slots
    banner_region: Region = field(default_factory=Region)   # "New Item <name>" strip
    store_point: list[int] = field(default_factory=list)    # roughly on the prompt
    store_search_x: int = 220              # the prompt is searched for around that
    store_search_y: int = 90               # point, not assumed to sit exactly on it
    store_wait: float = 3.0                # seconds to wait for the banner
    drop_duplicates: bool = True           # DESTRUCTIVE, and it gates the whole
    # feature: turning it off turns storing off too. Storing without dropping fills
    # the hotbar with duplicates it can never clear, which is worse than not storing
    # at all. A fruit is only ever dropped after GPO itself refused the store.
    drop_key: str = "backspace"            # GPO drops the held item on this key


@dataclass
class WebhookConfig:
    url: str = ""
    user_id: str = ""                       # Discord ID to ping (optional)
    ping_on_fruit: bool = True
    ping_on_error: bool = True
    log_milestones: bool = True
    milestone_every: int = 100              # catches per milestone notification
    periodic_stats_minutes: int = 30        # 0 disables periodic stat posts


@dataclass
class SoundConfig:
    enabled: bool = False                   # listen for the rare-fish/sea-event cue
    sensitivity: float = 3.0                # RMS spike ratio over the rolling baseline
    trigger_hold: float = 0.4               # seconds the spike must persist
    cooldown: float = 60.0                  # minimum seconds between alerts
    alert_wav: str = ""                     # optional custom WAV; empty => built-in beeps


@dataclass
class UIConfig:
    always_on_top: bool = False
    live_preview: bool = False              # show annotated detection frames on the dashboard
    preview_scale: float = 0.35


@dataclass
class AppConfig:
    hotkeys: HotkeysConfig = field(default_factory=HotkeysConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    controller: ControllerConfig = field(default_factory=ControllerConfig)
    fishing: FishingConfig = field(default_factory=FishingConfig)
    scan_region: Region = field(default_factory=Region)   # minigame watch area (window-relative)
    bait: BaitConfig = field(default_factory=BaitConfig)
    fruit: FruitConfig = field(default_factory=FruitConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    sound: SoundConfig = field(default_factory=SoundConfig)
    ui: UIConfig = field(default_factory=UIConfig)

    # ------------------------------------------------------------------ I/O

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        path = Path(path)
        cfg = cls()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            dict_into_dataclass(cfg, data)
        return cfg


def dataclass_to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: dataclass_to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [dataclass_to_dict(v) for v in obj]
    return obj


def dict_into_dataclass(obj: Any, data: dict[str, Any]) -> None:
    """Merge a (possibly partial) dict into a dataclass instance, ignoring unknown keys."""
    if not (is_dataclass(obj) and isinstance(data, dict)):
        return
    by_name = {f.name: f for f in fields(obj)}
    for key, value in data.items():
        if key not in by_name:
            continue
        current = getattr(obj, key)
        if is_dataclass(current) and isinstance(value, dict):
            dict_into_dataclass(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            current.clear()
            current.extend(value)
        else:
            setattr(obj, key, value)


class ConfigStore:
    """Thread-safe holder that auto-saves on every mutation."""

    def __init__(self, path: str | Path, config: AppConfig):
        self.path = Path(path)
        self._config = config
        self._lock = threading.RLock()

    @property
    def config(self) -> AppConfig:
        with self._lock:
            return self._config

    def update(self) -> AppConfig:
        """Call after mutating nested fields; persists and returns the config."""
        with self._lock:
            self._config.save(self.path)
            return self._config

    def replace(self, new: AppConfig) -> AppConfig:
        with self._lock:
            self._config = new
            self._config.save(self.path)
            return self._config
