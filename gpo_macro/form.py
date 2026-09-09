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
    ("Bait", "bait", "auto_buy", "Auto-buy common bait", "bool"),
    ("Bait", "bait", "loops_per_purchase", "Buy every N catches", "int"),
    ("Bait", "bait", "auto_craft", "Auto-craft bait", "bool"),
    ("Bait", "bait", "loops_per_craft", "Craft every N catches", "int"),
    ("Bait", "bait", "crafts_per_cycle", "Crafts per cycle", "int"),
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
]

SECTION_NOTES: dict[str, str] = {
    "Hotkeys": "Work even while Roblox has focus. Panic releases the mouse and stops "
               "everything immediately.",
    "Detection": "Only touch these if the Calibration tab's Test Detection cannot find "
                 "the gauge.",
    "Controller": "Bar lead is the brake. Raise it if the bar sails past the fish, lower "
                  "it if it stalls short. Watch the on-the-fish percentage on the "
                  "dashboard while you change it.",
    "Fishing": "Timings around the cast. Jitter randomises every delay by this fraction.",
    "Bait": "Needs the bait click points from the Calibration tab before it will run.",
    "Fruit": "Needs the fruit icon, hotbar row, banner strip and store button from "
             "the Calibration tab. GPO only lets you own one of each fruit, so a "
             "refusal is normal and the duplicate is dropped, which destroys it. "
             "Turning dropping off turns storing off with it - storing alone "
             "would fill the hotbar with duplicates nothing can clear.",
    "Webhook": "Posts catches, faults and session summaries to Discord. The URL is a "
               "credential - anyone holding it can post to your channel.",
    "Sound": "Listens to system audio for the rare-spawn cue. Needs a loopback device.",
    "Interface": "",
}


def schema() -> list[dict]:
    """The form as plain data, for the C# shell."""
    return [{"section": section, "obj": obj, "attr": attr, "label": label, "kind": kind}
            for section, obj, attr, label, kind in FORM]
