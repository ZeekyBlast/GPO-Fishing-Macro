"""Design tokens. See DESIGN.md for the reasoning.

The window itself is C# (ui-csharp/Theme.xaml holds the matching chrome), but
what a colour *means* is decided here and shipped to the shell in the `hello`
frame - so state and event colours have exactly one definition. The Discord
embeds read the same tokens.
"""

from __future__ import annotations

# --------------------------------------------------------------------- colour
# Near-black surfaces, one committed accent, two fault colours. GREEN is the
# accent: it means the hook is on the fish, which is the only thing this
# program is for. It fills bars and marks live state - it is never decoration.

BG = "#0C0E10"          # window ground
SURFACE = "#141719"     # raised panel
SURFACE_2 = "#1B1F22"   # input, inset well, meter track
LINE = "#252A2E"        # hairline
LINE_STRONG = "#3A4247"  # hover, focus rim

TEXT = "#E9ECEE"        # primary
TEXT_2 = "#BFC6C9"      # secondary prose
MUTED = "#8A9296"       # labels
FAINT = "#646E72"       # metadata, disabled. Raised from #5B6569, which read
# fine on BG but fell to 2.77:1 on SURFACE_2 - the audit below is what caught it

GREEN = "#4EC98A"       # THE accent: engaged, on the fish, caught
GREEN_DIM = "#1E3A2C"   # accent at rest (meter track fill)
AMBER = "#E0A33A"       # attention, not yet a fault: waiting, paused, timeout
RED = "#C2453F"         # fault: error, escape, panic

# State colour is never the only signal - every use is paired with a word.
STATE_COLORS = {
    "idle": FAINT, "focus": MUTED, "prep": MUTED, "cast": MUTED,
    "wait": AMBER, "reel": GREEN, "loot": GREEN,
    "maintenance": MUTED, "recover": AMBER, "paused": AMBER,
}

# Event kind -> (icon, colour). Shared by the log and the Discord embeds.
EVENT_STYLES = {
    "fish": ("+", GREEN), "milestone": ("*", GREEN), "fruit": ("!", AMBER),
    "sound": ("~", AMBER), "warn": ("!", AMBER), "timeout": ("!", AMBER),
    "error": ("x", RED), "state": (">", MUTED), "debug": (".", FAINT),
    "info": ("-", MUTED),
}

# ----------------------------------------------------------------- typography
# Mono for anything that is a number, an identifier, a label or a status.
# Sans for sentences a human wrote. The split is what makes this read as an
# instrument rather than an app.

MONO = "Cascadia Mono"   # ships with Windows Terminal; Consolas is the fallback
SANS = "Segoe UI"

# Fixed scale, not fluid: metadata / label / body / heading / readout / hero.
SIZE_META, SIZE_LABEL, SIZE_BODY, SIZE_HEAD, SIZE_READOUT, SIZE_HERO = 10, 11, 12, 14, 15, 30


def mono(size: int = SIZE_BODY, bold: bool = False) -> tuple:
    return (MONO, size, "bold") if bold else (MONO, size)


def sans(size: int = SIZE_BODY, bold: bool = False) -> tuple:
    return (SANS, size, "bold") if bold else (SANS, size)


# ---------------------------------------------------------------------- space
PAD = 12          # panel padding and the gutter between panels
GAP = 6           # gap between related controls
ROW_MIN = 26      # no interactive row is shorter than this


# ------------------------------------------------------------------- contrast
def _luminance(hex_color: str) -> float:
    channels = []
    raw = hex_color.lstrip("#")
    for i in (0, 2, 4):
        c = int(raw[i:i + 2], 16) / 255.0
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two hex colours, 1.0 to 21.0."""
    a, b = _luminance(fg), _luminance(bg)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def audit() -> list[tuple[str, str, str, float, float]]:
    """Every foreground/background pair the app actually renders, with its
    required ratio. Returns (name, fg, bg, ratio, required)."""
    checks: list[tuple[str, str, str, float]] = []
    for surface_name, surface in (("bg", BG), ("surface", SURFACE), ("surface2", SURFACE_2)):
        for name, colour, required in (
                ("text", TEXT, 4.5), ("text2", TEXT_2, 4.5), ("muted", MUTED, 4.5),
                ("faint", FAINT, 3.0),   # metadata only, never a sentence
                ("green", GREEN, 4.5), ("amber", AMBER, 4.5), ("red", RED, 3.0)):
            checks.append((f"{name} on {surface_name}", colour, surface, required))
    return [(name, fg, bg, contrast(fg, bg), required) for name, fg, bg, required in checks]


if __name__ == "__main__":
    worst = 99.0
    for name, fg, bg, ratio, required in audit():
        flag = "ok " if ratio >= required else "FAIL"
        worst = min(worst, ratio - required)
        print(f"{flag} {name:20s} {fg} on {bg}  {ratio:5.2f}:1  (need {required})")
    print(f"\nsmallest margin over requirement: {worst:+.2f}")
