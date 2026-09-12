"""The settings form, described once.

Both the (legacy) tkinter UI and the C# shell render from this list, so a new
knob is added in exactly one place. Kinds are "str" | "int" | "float" | "bool".
"""

from __future__ import annotations

# (section, config section attribute, field attribute, label, kind)
FORM: list[tuple[str, str, str, str, str]] = [
    ("Hotkeys", "hotkeys", "start_stop", "Toggle macro", "str"),
    ("Hotkeys", "hotkeys", "panic", "Panic key", "str"),
    ("Detection", "detection", "color_tolerance", "Color tolerance (±)", "int"),
    ("Detection", "detection", "min_bar_pixels", "Min bar pixels", "int"),
    ("Detection", "detection", "black_screen_threshold", "Black-screen threshold", "float"),
    ("Detection", "detection", "scan_loop_delay", "Scan loop delay (s)", "float"),
    ("Controller", "controller", "bar_lead", "Bar lead / brake (s)", "float"),
    ("Controller", "controller", "fish_lead", "Fish lead (s)", "float"),
    ("Controller", "controller", "invert", "Invert raise/drop", "bool"),
    ("Fishing", "fishing", "equip_rod", "Equip rod before casting", "bool"),
    ("Fishing", "fishing", "rod_key", "Rod hotkey", "str"),
    ("Fishing", "fishing", "cast_hold_duration", "Cast hold (s)", "float"),
    ("Fishing", "fishing", "post_cast_delay", "Post-cast settle (s)", "float"),
    ("Fishing", "fishing", "recast_timeout", "Recast timeout (s)", "float"),
    ("Fishing", "fishing", "loot_delay", "Loot delay (s)", "float"),
    ("Fishing", "fishing", "reel_max_duration", "Max reel duration (s)", "float"),
    ("Fishing", "fishing", "pause_on_focus_lost", "Pause when Roblox unfocused", "bool"),
    ("Fishing", "fishing", "action_jitter", "Action jitter (0-1)", "float"),
    ("Bait", "bait", "auto_craft", "Auto-craft bait at Blacksmith Sen", "bool"),
    ("Bait", "bait", "auto_buy", "Auto-buy common bait at the barrel", "bool"),
    ("Bait", "bait", "every_n_catches", "Upkeep every N catches", "int"),
    ("Bait", "bait", "menu_delay", "Menu settle time (s)", "float"),
    ("Bait", "bait", "talk_key", "Sen's prompt key", "str"),
    ("Bait", "bait", "shop_key", "Barrel's prompt key", "str"),
    ("Bait", "bait", "shop_hold", "Hold the barrel key for (s)", "float"),
    ("Bait", "bait", "buy_amount", "Bait per purchase (max 300)", "int"),
    ("Bait", "bait", "walk_to_sen", "Walk to Sen and back", "bool"),
    ("Bait", "bait", "walk_keys", "Walk keys (e.g. d, or w+d)", "str"),
    ("Bait", "bait", "return_keys", "Return keys", "str"),
    ("Bait", "bait", "max_walk", "Give up walking after (s)", "float"),
    ("Bait", "bait", "return_scale", "Return leg scale", "float"),
    ("Fruit", "fruit", "auto_store", "Auto-store devil fruits", "bool"),
    ("Fruit", "fruit", "match_threshold", "Icon match threshold", "float"),
    ("Fruit", "fruit", "store_wait", "Wait for banner (s)", "float"),
    ("Fruit", "fruit", "store_search_x", "Prompt search width (+/- px)", "int"),
    ("Fruit", "fruit", "store_search_y", "Prompt search height (+/- px)", "int"),
    ("Fruit", "fruit", "drop_duplicates",
     "Drop fruits you already own (required for storing)", "bool"),
    ("Fruit", "fruit", "drop_key", "Drop key", "str"),
    ("Webhook", "webhook", "url", "Discord webhook URL", "secret"),
    ("Webhook", "webhook", "user_id", "Discord user ID (pings)", "str"),
    ("Webhook", "webhook", "ping_on_fruit", "Ping on fruit/sound", "bool"),
    ("Webhook", "webhook", "ping_on_error", "Ping on errors", "bool"),
    ("Webhook", "webhook", "log_milestones", "Milestone notifications", "bool"),
    ("Webhook", "webhook", "milestone_every", "Milestone every N fish", "int"),
    ("Webhook", "webhook", "periodic_stats_minutes", "Periodic stats (min, 0=off)", "int"),
    ("Sound", "sound", "enabled", "Rare-spawn sound alert (loopback)", "bool"),
    ("Sound", "sound", "sensitivity", "Sensitivity (spike ratio)", "float"),
    ("Sound", "sound", "trigger_hold", "Spike must persist (s)", "float"),
    ("Sound", "sound", "cooldown", "Alert cooldown (s)", "float"),
    ("Sound", "sound", "alert_wav", "Custom alert WAV (optional)", "str"),
    ("Interface", "ui", "always_on_top", "Window always on top", "bool"),
    ("Interface", "ui", "live_preview", "Live detection preview", "bool"),
    ("Interface", "ui", "preview_scale", "Preview scale", "float"),
    ("Interface", "ui", "check_for_updates", "Check for updates on launch", "bool"),
]

SECTION_NOTES: dict[str, str] = {
    "Hotkeys": "Work even while Roblox has focus. Panic releases the mouse and stops "
               "everything immediately.",
    "Detection": "Only touch these if Calibration's Test detection cannot find "
                 "the gauge.",
    "Controller": "Bar lead is the brake. Raise it if the bar sails past the fish, lower "
                  "it if it stalls short. Watch the on-the-fish percentage on the "
                  "dashboard while you change it.",
    "Fishing": "Timings around the cast. Jitter randomises every delay by this fraction.",
    "Bait": "Work in progress: auto-craft and auto-buy run but are not yet proven over a "
            "long session - watch the first few passes. "
            "Craft or buy, not both: switching one on switches the other off. Crafting "
            "is the only source of Rare and Legendary bait (two rare fish or one legendary "
            "fish each) and it crafts until the fish run out. Both need their points from "
            "the Calibration tab. Walking is only for a fishing spot outside Sen's prompt "
            "range. Before using it: turn GPO's Auto Run OFF (Menu > Settings), or the "
            "return leg sprints past the spot and into the sea, and set the camera to "
            "Classic, since Follow mode turns a straight walk into a curve.",
    "Fruit": "Needs the fruit icon, hotbar row, banner strip and store button from "
             "the Calibration tab. GPO only lets you own one of each fruit, so a "
             "refusal is normal and the duplicate is dropped, which destroys it. "
             "Turning dropping off turns storing off with it - storing alone "
             "would fill the hotbar with duplicates nothing can clear.",
    "Webhook": "Posts catches, faults and session summaries to Discord. The URL is a "
               "credential - anyone holding it can post to your channel.",
    "Sound": "Listens to system audio for the rare-spawn cue. Needs a loopback device.",
    "Interface": "The update check asks GitHub for the latest release and nothing else. It sends no information about you.",
}


# What a field does, in a few words, shown beside it. Empty is fine and
# common; only knobs whose consequence is not obvious from the label get one.
EFFECTS: dict[tuple[str, str], str] = {
    ("hotkeys", "start_stop"): "start and stop",
    ("hotkeys", "panic"): "releases the held mouse button",
    ("detection", "color_tolerance"): "per channel",
    ("controller", "bar_lead"): "the brake",
    ("controller", "fish_lead"): "0 = aim at where the fish is now",
    ("fishing", "recast_timeout"): "then it casts again",
    ("fishing", "action_jitter"): "randomises every delay by this fraction",
    ("bait", "every_n_catches"): "one upkeep pass",
    ("webhook", "milestone_every"): "one message per N fish",
    ("ui", "preview_scale"): "of the capture, in the reel panel",
}

# Sections folded away by default: knobs that only matter once something is
# already broken should not be the first thing a new user scrolls past.
ADVANCED: frozenset[str] = frozenset({"Detection"})


def schema() -> list[dict]:
    """The form as plain data, for the C# shell."""
    return [{"section": section, "obj": obj, "attr": attr, "label": label, "kind": kind,
             "effect": EFFECTS.get((obj, attr), ""), "advanced": section in ADVANCED}
            for section, obj, attr, label, kind in FORM]
