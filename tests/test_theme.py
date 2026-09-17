"""Contrast audit for the design tokens.

Colour is checked, not eyeballed: every foreground-on-surface pair the app
actually renders has to clear WCAG AA. This is what caught FAINT at #5B6569 -
fine on BG, 2.77:1 on SURFACE_2.

The window is C# now, so this also guards the copy of the palette in
ui-csharp/Theme.xaml: the two must not drift apart.

Run: python tests/test_theme.py
"""


from __future__ import annotations

import re

import paths  # noqa: F401  - puts the project root on sys.path

from gpo_macro import theme

THEME_XAML = paths.ROOT / "ui-csharp" / "Theme.xaml"

# token name in theme.py -> resource key in Theme.xaml
SHARED = {
    "BG": "BgColor", "SURFACE": "SurfaceColor", "SURFACE_2": "Surface2Color",
    "LINE": "LineColor", "LINE_STRONG": "LineStrongColor",
    "TEXT": "TextColor", "TEXT_2": "Text2Color", "MUTED": "MutedColor",
    "FAINT": "FaintColor", "GREEN": "GreenColor", "GREEN_DIM": "GreenDimColor",
    "AMBER": "AmberColor", "RED": "RedColor",
}


def check_contrast() -> None:
    failures = [(name, f"{ratio:.2f}:1 needs {need}")
                for name, _fg, _bg, ratio, need in theme.audit() if ratio < need]
    assert not failures, f"contrast failures: {failures}"
    worst = min(ratio - need for _n, _f, _b, ratio, need in theme.audit())
    print(f"contrast ok - smallest margin over requirement: {worst:+.2f}")


def check_shell_palette() -> None:
    """The C# window cannot import theme.py, so the two copies are compared."""
    if not THEME_XAML.exists():
        print("Theme.xaml not found - skipping shell palette check")
        return
    xaml = THEME_XAML.read_text(encoding="utf-8")
    found = dict(re.findall(r'<Color x:Key="(\w+)">#FF([0-9A-Fa-f]{6})</Color>', xaml))

    drift = []
    for token, key in SHARED.items():
        want = getattr(theme, token).lstrip("#").upper()
        got = found.get(key, "").upper()
        if got != want:
            drift.append(f"{token}={want} but Theme.xaml {key}={got or 'missing'}")
    assert not drift, "palette drift between theme.py and Theme.xaml:\n  " + "\n  ".join(drift)
    print(f"shell palette matches - {len(SHARED)} tokens")


SIZE_KEYS = {"meta": "SizeMeta", "label": "SizeLabel", "body": "SizeBody", "head": "SizeHead",
             "readout": "SizeReadout", "counter": "SizeCounter", "hero": "SizeHero"}
APP_CSS = paths.ROOT / "gpo_macro" / "web" / "static" / "app.css"


def check_shell_sizes() -> None:
    """The type scale is written three times - theme.py, Theme.xaml, app.css -
    and a size that drifts in one shell is a page that no longer reads as the
    same instrument."""
    xaml = dict(re.findall(r'<sys:Double x:Key="(\w+)">(\d+)</sys:Double>',
                           THEME_XAML.read_text(encoding="utf-8")))
    css = dict(re.findall(r"--size-(\w+): (\d+)px;", APP_CSS.read_text(encoding="utf-8")))
    drift = []
    for name, key in SIZE_KEYS.items():
        want = str(theme.SIZES[name])
        if xaml.get(key) != want:
            drift.append(f"{name}={want} but Theme.xaml {key}={xaml.get(key) or 'missing'}")
        if css.get(name) != want:
            drift.append(f"{name}={want} but app.css --size-{name}={css.get(name) or 'missing'}")
    assert not drift, "type scale drift:\n  " + "\n  ".join(drift)
    print(f"type scale matches in all three shells - {len(SIZE_KEYS)} sizes")


def check_vocabulary() -> None:
    """Every state and event kind the shell renders must carry a colour, or the
    dashboard falls back to grey and the word loses its pair."""
    from gpo_macro.fisher import State
    missing = [s.value for s in State if s.value not in theme.STATE_COLORS]
    assert not missing, f"states with no colour: {missing}"
    for kind, style in theme.EVENT_STYLES.items():
        assert len(style) == 2 and style[1].startswith("#"), f"bad event style: {kind}"
    print(f"vocabulary ok - {len(State)} states, {len(theme.EVENT_STYLES)} event kinds")


def main() -> int:
    check_contrast()
    check_shell_palette()
    check_shell_sizes()
    check_vocabulary()
    print("\nall theme checks passed")
    return 0


test_contrast = check_contrast
test_shell_palette = check_shell_palette
test_shell_sizes = check_shell_sizes
test_vocabulary = check_vocabulary


if __name__ == "__main__":
    raise SystemExit(main())
