"""Self-check for key names.

A hotkey is typed into the settings form as a name - "f6", "a", "ctrl+f9" -
and handed to pynput's parser. Every name parse_key accepts for a keystroke
must also register as a hotkey, and it must mean the same key both ways.

Run: python tests/test_input.py
"""

import paths  # noqa: F401  - puts the project root on sys.path

from pynput.keyboard import HotKey, Key, KeyCode

from gpo_macro.config import HotkeysConfig
from gpo_macro.hotkeys import HotkeyManager
from gpo_macro.input import parse_key, to_pynput_hotkey


def main() -> int:
    # pynput wants special keys in brackets and single characters bare. A
    # bracketed letter is a ValueError, and a bracketed digit is a virtual key
    # code: "<1>" is VK 1, the left mouse button, not the 1 key.
    for name, want in (("f6", "<f6>"), ("F8", "<f8>"), ("a", "a"), ("1", "1"),
                       ("ctrl+shift+f9", "<ctrl>+<shift>+<f9>"), ("escape", "<esc>"),
                       ("control+f9", "<ctrl>+<f9>"), ("space", "<space>"), ("home", "<home>")):
        got = to_pynput_hotkey(name)
        assert got == want, f"{name!r} -> {got!r}, wanted {want!r}"
        HotKey.parse(got)                       # pynput must accept every one

    assert HotKey.parse(to_pynput_hotkey("a")) == [KeyCode.from_char("a")]
    assert HotKey.parse(to_pynput_hotkey("1")) == [KeyCode.from_char("1")]
    assert HotKey.parse(to_pynput_hotkey("1")) != [KeyCode.from_vk(1)], "digit read as VK 1"

    # The keystroke side agrees, including pynput's own key names.
    assert parse_key("home") is Key.home
    assert parse_key("escape") is Key.esc
    assert parse_key("a") == KeyCode.from_char("a")

    # A name nobody can type is refused at the door, and registering it must
    # not raise: hotkeys are rebound on every settings save.
    for bad in ("", "nope", "ctrl+nope"):
        try:
            to_pynput_hotkey(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad!r} was accepted")
    manager = HotkeyManager(lambda: HotkeysConfig(start_stop="nope", panic="f8"),
                            lambda: None, lambda: None)
    manager.restart()                            # logs the problem, keeps running
    assert manager._listener is None
    manager.stop()

    # The corner failsafe covers every monitor, not only the primary one:
    # parking the mouse in a corner of the second screen must stop the bot.
    from fakes import FakeWindowSystem
    from gpo_macro.capture import WindowTracker
    from gpo_macro.input import FailsafeError, InputController
    from gpo_macro.platform import Rect, WindowInfo

    monitors = [Rect(0, 0, 2560, 1440), Rect(2560, 0, 1920, 1080)]
    system = FakeWindowSystem(WindowInfo(1, "Roblox", 2600, 50, 4200, 950), monitors)
    ctl = InputController(WindowTracker(system), jitter=0.0)

    class Mouse:
        position = (0, 0)

    ctl.mouse = Mouse()
    for corner in ((2, 2), (2557, 3), (4477, 1077), (2563, 1077)):
        ctl.mouse.position = corner
        try:
            ctl.failsafe_check()
        except FailsafeError:
            pass
        else:
            raise AssertionError(f"no failsafe at {corner}")
    ctl.mouse.position = (2500, 1000)
    ctl.failsafe_check()                         # the middle of a screen is fine

    # The last stretch of an approach is walked with real relative events,
    # through the window system, closed-loop on the position read back.
    ctl.tracker.refresh()
    ctl.mouse.position = (2700, 300)
    ctl._approach(100, 250)                      # window-relative -> screen (2700, 300)
    assert system.moves and all(m == (6, 0) for m in system.moves), system.moves[:3]

    print("all input checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
