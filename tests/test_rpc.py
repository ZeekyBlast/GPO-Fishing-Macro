"""Protocol check for the C# shell's engine: `python main.py --rpc`.

Spawns the engine exactly the way MacroUI.exe does and asserts the frames the
shell depends on. Run it after touching rpc.py or form.py:

    .venv/Scripts/python tests/test_rpc.py
"""


from __future__ import annotations

import paths  # noqa: F401  - puts the project root on sys.path

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = paths.ROOT
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def read_until(proc, predicate, timeout=25.0):
    """Return the first stdout frame matching predicate, or raise."""
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            raise AssertionError(f"engine closed stdout. saw: {seen[-5:]}")
        line = line.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            raise AssertionError(f"non-JSON on the protocol stream: {line[:200]!r}")
        seen.append(frame.get("t"))
        if predicate(frame):
            return frame
    raise AssertionError(f"timed out. frame kinds seen: {seen[-20:]}")


def send(proc, **request):
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()


def make_engine(name: str, sink=None):
    """An in-process engine, not started: commands run, frames go to `sink`
    (nowhere by default)."""
    from gpo_macro.config import AppConfig, ConfigStore
    from gpo_macro.rpc import Engine

    return Engine(ConfigStore(paths.scratch() / name, AppConfig()), sink=sink)


def check_dispatch_and_hello() -> None:
    """dispatch hands its reply back to whichever transport asked - the
    stdin loop writes it, the browser gets it as the HTTP response - and the
    hello frame can be rebuilt for a client that connects late."""
    from gpo_macro import theme

    frames: list[dict] = []
    engine = make_engine("dispatch-settings.json", sink=frames.append)

    reply = engine.dispatch({"id": 4, "cmd": "ping"})
    assert reply["t"] == "reply" and reply["id"] == 4 and reply["ok"], reply
    assert "pong" in reply["result"]
    assert frames == [], "dispatch sent the reply itself; the transport owns that"
    bad = engine.dispatch({"id": 5, "cmd": "nonsense"})
    assert bad["ok"] is False and "unknown command" in bad["error"], bad

    # A capture is the game's client area; without the game it is a refusal.
    cap = engine.dispatch({"id": 6, "cmd": "capture"})
    if cap["ok"]:
        assert cap["result"]["png"] and cap["result"]["width"] > 0, cap["result"].keys()
    else:
        assert "window" in cap["error"].lower(), cap

    hello = engine.hello_frame()
    assert hello["t"] == "hello" and hello["schema"] and hello["config"]["hotkeys"]["start_stop"]
    assert hello["theme"]["palette"]["GREEN"] == theme.GREEN, "the page's colours come from theme.py"
    assert hello["theme"]["mono"] == theme.MONO
    print("dispatch ok - replies returned, hello rebuildable with the palette")


def check_hotkey_validation() -> None:
    """A hotkey the listener cannot bind, or two hotkeys on one key, is
    refused before anything is saved - otherwise the settings file holds a
    pair that silently never registers again."""
    engine = make_engine("hotkeys-settings.json")
    try:
        for patch, expect in (({"start_stop": "f8"}, "panic"),
                              ({"panic": "f6"}, "toggle"),
                              ({"panic": "nope"}, "nope")):
            try:
                engine.cmd_set_config({"patch": {"hotkeys": patch}})
            except ValueError as exc:
                assert expect in str(exc), (patch, str(exc))
            else:
                raise AssertionError(f"{patch} was accepted")
        assert (engine.cfg.hotkeys.start_stop, engine.cfg.hotkeys.panic) == ("f6", "f8")
        assert not engine.store.path.exists(), "a refused patch was written to disk"

        result = engine.cmd_set_config({"patch": {"hotkeys": {"start_stop": "ctrl+f9"}}})
        assert result["config"]["hotkeys"]["start_stop"] == "ctrl+f9"
        assert engine.store.path.exists()
    finally:
        engine.hotkeys.stop()
    print("hotkey validation ok - bad or duplicate keys refused before saving")


def check_toggle_resumes_paused() -> None:
    """The toggle key cycles start / stop, and a paused bot resumes.

    The dashboard says "Press f6 to resume" and the bot says "toggle to
    resume"; a toggle that stopped a paused bot made every focus-loss pause
    end the session by the time the user reacted."""
    from gpo_macro.fisher import State

    class FakeBot:
        def __init__(self, state):
            self.state = state
            self.stopped = self.toggled = False

        def is_alive(self):
            return True

        def stop(self):
            self.stopped = True

        def request_pause_toggle(self):
            self.toggled = True

    engine = make_engine("toggle-settings.json")
    engine.bot = FakeBot(State.PAUSED)
    assert engine.cmd_toggle({}) == {"toggled": True}, "paused bot was not resumed"
    assert engine.bot.toggled and not engine.bot.stopped, "toggle stopped a paused bot"

    engine.bot = FakeBot(State.WAIT)
    assert engine.cmd_toggle({}) == {"stopped": True}
    assert engine.bot.stopped and not engine.bot.toggled, "toggle did not stop a running bot"

    # Stop is still stop, even when paused: the button that says Stop must mean it.
    engine.bot = FakeBot(State.PAUSED)
    assert engine.cmd_stop({}) == {"stopped": True} and engine.bot.stopped
    print("toggle ok - resumes a paused bot, stops a running one")


def main() -> int:
    check_toggle_resumes_paused()
    check_hotkey_validation()
    check_dispatch_and_hello()

    exe = str(PYTHON) if PYTHON.exists() else sys.executable
    # A scratch settings file: the round trips below write, and the real
    # settings.json is the user's.
    proc = subprocess.Popen(
        [exe, str(ROOT / "main.py"), "--rpc", "--settings", str(paths.scratch() / "rpc-settings.json")],
        cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
    try:
        hello = read_until(proc, lambda f: f.get("t") == "hello")
        assert hello["schema"], "hello carries no settings schema"
        assert {"section", "obj", "attr", "label", "kind", "effect", "advanced"}             <= set(hello["schema"][0])
        assert hello["defaults"]["controller"]["bar_lead"] is not None, "defaults missing"
        assert hello["config"]["hotkeys"]["start_stop"], "config missing in hello"
        kinds = {f["kind"] for f in hello["schema"]}
        assert kinds <= {"str", "int", "float", "bool", "secret", "hotkey"}, f"unknown kinds: {kinds}"
        assert {f["kind"] for f in hello["schema"] if f["obj"] == "hotkeys"} == {"hotkey"}
        # Every schema entry must name a field that actually exists.
        for field in hello["schema"]:
            section = hello["config"][field["obj"]]
            assert field["attr"] in section, f"{field['obj']}.{field['attr']} not in config"
        # A gate names another field, one that comes earlier in the form so
        # the knobs it reveals appear under it, and one whose "on" is
        # readable: a bool, or a text that is non-empty.
        order = [(f["obj"], f["attr"]) for f in hello["schema"]]
        by_key = {key: f for key, f in zip(order, hello["schema"])}
        for field in hello["schema"]:
            assert isinstance(field["gate"], list), field
            for gate in field["gate"]:
                key = (gate["obj"], gate["attr"])
                assert key in by_key, f"{field['obj']}.{field['attr']} gated on unknown {key}"
                assert order.index(key) < order.index((field["obj"], field["attr"])), \
                    f"gate {key} comes after the field it reveals"
                assert by_key[key]["kind"] in ("bool", "secret", "str"), key
        assert by_key[("bait", "walk_keys")]["gate"] == [{"obj": "bait", "attr": "walk_to_sen"}]
        assert by_key[("webhook", "user_id")]["gate"] == [{"obj": "webhook", "attr": "url"}]
        assert by_key[("bait", "every_n_catches")]["gate"] == [
            {"obj": "bait", "attr": "auto_craft"}, {"obj": "bait", "attr": "auto_buy"}]
        assert by_key[("fishing", "rod_key")]["gate"] == [], "rod_key is used with equip_rod off"
        shown = sum(1 for f in hello["schema"] if not f["gate"] and not f["advanced"])
        assert shown <= 26, f"{shown} fields show by default - too many are ungated"
        print(f"hello ok - {len(hello['schema'])} settings fields, {shown} shown by default")

        reply = (send(proc, id=1, cmd="ping"),
                 read_until(proc, lambda f: f.get("t") == "reply" and f.get("id") == 1))[1]
        assert reply["ok"], reply
        print("ping ok")

        tick = read_until(proc, lambda f: f.get("t") == "tick")
        for key in ("running", "state", "stats", "window", "webhook", "region_valid", "fault"):
            assert key in tick, f"tick missing {key}"
        assert tick["fault"] in (None, "no_window", "no_gauge", "window_lost", "webhook_failed")
        assert tick["running"] is False and tick["state"] == "idle"
        print(f"tick ok - state={tick['state']} window={bool(tick['window'])}")

        send(proc, id=2, cmd="window")
        window = read_until(proc, lambda f: f.get("t") == "reply" and f.get("id") == 2)
        assert window["ok"], window
        assert "origin_x" in window["result"], window
        print(f"window ok - found={window['result']['found']}")

        # A round-trip through set_config must come back changed, and must not
        # clobber the neighbouring value.
        before = hello["config"]["controller"]["bar_lead"]
        send(proc, id=3, cmd="set_config",
             patch={"controller": {"bar_lead": round(before + 0.01, 4)}})
        saved = read_until(proc, lambda f: f.get("t") == "reply" and f.get("id") == 3)
        assert saved["ok"], saved
        after = saved["result"]["config"]["controller"]
        assert abs(after["bar_lead"] - (before + 0.01)) < 1e-6, after
        assert after["fish_lead"] == hello["config"]["controller"]["fish_lead"]
        send(proc, id=4, cmd="set_config", patch={"controller": {"bar_lead": before}})
        read_until(proc, lambda f: f.get("t") == "reply" and f.get("id") == 4)
        print("set_config ok - round-trip restored")

        # Craft or buy, never both: the one just switched on wins.
        modes = hello["config"]["bait"]
        send(proc, id=6, cmd="set_config", patch={"bait": {"auto_buy": True, "auto_craft": False}})
        read_until(proc, lambda f: f.get("t") == "reply" and f.get("id") == 6)
        send(proc, id=7, cmd="set_config", patch={"bait": {"auto_buy": True, "auto_craft": True}})
        both = read_until(proc, lambda f: f.get("t") == "reply" and f.get("id") == 7)
        bait = both["result"]["config"]["bait"]
        assert bait["auto_craft"] and not bait["auto_buy"], bait
        send(proc, id=8, cmd="set_config",
             patch={"bait": {"auto_buy": modes["auto_buy"], "auto_craft": modes["auto_craft"]}})
        read_until(proc, lambda f: f.get("t") == "reply" and f.get("id") == 8)
        print("set_config ok - one bait mode at a time")

        # A test cast needs a Roblox window; without one it is a clean refusal,
        # with one it echoes the hold it used. Either way it must answer.
        send(proc, id=9, cmd="test_cast")
        cast = read_until(proc, lambda f: f.get("t") == "reply" and f.get("id") == 9)
        assert cast["ok"] or "no Roblox window" in cast.get("error", ""), cast
        if cast["ok"]:
            assert cast["result"]["hold"] == hello["config"]["fishing"]["cast_hold_duration"]
        print("test_cast ok -", "cast" if cast["ok"] else "refused, no window")

        send(proc, id=5, cmd="nonsense")
        bad = read_until(proc, lambda f: f.get("t") == "reply" and f.get("id") == 5)
        assert bad["ok"] is False and "unknown command" in bad["error"], bad
        print("unknown command rejected")

        send(proc, id=6, cmd="shutdown")
        read_until(proc, lambda f: f.get("t") == "reply" and f.get("id") == 6)
        assert proc.wait(timeout=10) == 0, "engine did not exit cleanly"
        print("shutdown ok")
    finally:
        if proc.poll() is None:
            proc.kill()
    print("\nall rpc checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
