"""Entry point. The window lives in ui-csharp/; this is the engine.

    python main.py                # console bot: hotkeys + log output, no window
    python main.py --rpc          # engine for the C# shell (start.bat)

Per-monitor DPI awareness is set here and mirrored by the shell's manifest, so
a calibration coordinate means the same pixel in both processes.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys
from pathlib import Path

from gpo_macro import APP_NAME, __version__
from gpo_macro.config import AppConfig, ConfigStore

SETTINGS_PATH = Path(__file__).with_name("settings.json")


def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _setup_logging(console: bool) -> None:
    handlers: list[logging.Handler] = [logging.FileHandler("gpo_macro.log", encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def run_console(store: ConfigStore) -> int:
    """Same services as the shell drives, with no window and no pipe."""
    from gpo_macro.fisher import FishingBot
    from gpo_macro.hotkeys import HotkeyManager
    from gpo_macro.notify import DiscordNotifier
    from gpo_macro.stats import EventBus, Stats

    cfg = store.config
    bus = EventBus()
    stats = Stats()
    bus.subscribe(lambda e: print(f"[{e.kind:>9}] {e.message}"))
    notifier = DiscordNotifier(bus, stats, lambda: store.config)

    bot: list[FishingBot] = []

    def toggle() -> None:
        if bot and bot[0].is_alive():
            bot[0].stop()
        elif cfg.scan_region.valid():
            bot.clear()
            bot.append(FishingBot(cfg, stats, bus))
            bot[0].start()
        else:
            print("scan region not calibrated - run start.bat once "
                  "and use the Calibration tab")

    def panic() -> None:
        if bot and bot[0].is_alive():
            bot[0].panic()

    hotkeys = HotkeyManager(lambda: store.config.hotkeys, toggle, panic)
    bus.start()
    notifier.start()
    hotkeys.start()
    print(f"{APP_NAME} v{__version__} console mode - "
          f"{cfg.hotkeys.start_stop} toggle, {cfg.hotkeys.panic} panic, Ctrl+C quit")
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        if bot and bot[0].is_alive():
            bot[0].stop()
            bot[0].join(timeout=3)
        hotkeys.stop()
        notifier.stop()
        bus.stop()
    return 0


def main() -> int:
    try:
        return _run(sys.argv[1:])
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return 0
    except BaseException:
        # Keep the console open when launched by double-click, and leave a trace.
        import traceback
        error = traceback.format_exc()
        try:
            Path("crash.log").write_text(error, encoding="utf-8")
        except Exception:
            pass
        # Under --rpc stdin is a pipe from the shell: prompting would hang, and
        # the shell reads crash.log instead.
        print(error, file=sys.stderr)
        if "--rpc" not in sys.argv:
            print("An error occurred (also saved to crash.log).")
            try:
                input("Press Enter to exit...")
            except EOFError:
                pass
        return 1


def _run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="GPO Fishing Macro")
    parser.add_argument("--console", action="store_true",
                        help="run the bot with log output (the default)")
    parser.add_argument("--rpc", action="store_true",
                        help="engine speaking JSON lines on stdin/stdout, for the C# shell")
    parser.add_argument("--settings", default=str(SETTINGS_PATH), help="settings.json path")
    args = parser.parse_args(argv)

    _enable_dpi_awareness()
    _setup_logging(console=not args.rpc)   # --rpc owns stdout; logs go to file only

    cfg = AppConfig.load(args.settings)
    store = ConfigStore(args.settings, cfg)

    if args.rpc:
        from gpo_macro.rpc import run_rpc
        return run_rpc(store)

    return run_console(store)


if __name__ == "__main__":
    raise SystemExit(main())
