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


def main() -> int:
    exe = str(PYTHON) if PYTHON.exists() else sys.executable
    proc = subprocess.Popen(
        [exe, str(ROOT / "main.py"), "--rpc"],
        cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
    try:
        hello = read_until(proc, lambda f: f.get("t") == "hello")
        assert hello["schema"], "hello carries no settings schema"
        assert {"section", "obj", "attr", "label", "kind"} <= set(hello["schema"][0])
        assert hello["config"]["hotkeys"]["start_stop"], "config missing in hello"
        kinds = {f["kind"] for f in hello["schema"]}
        assert kinds <= {"str", "int", "float", "bool", "secret"}, f"unknown kinds: {kinds}"
        # Every schema entry must name a field that actually exists.
        for field in hello["schema"]:
            section = hello["config"][field["obj"]]
            assert field["attr"] in section, f"{field['obj']}.{field['attr']} not in config"
        print(f"hello ok - {len(hello['schema'])} settings fields")

        reply = (send(proc, id=1, cmd="ping"),
                 read_until(proc, lambda f: f.get("t") == "reply" and f.get("id") == 1))[1]
        assert reply["ok"], reply
        print("ping ok")

        tick = read_until(proc, lambda f: f.get("t") == "tick")
        for key in ("running", "state", "stats", "window", "webhook", "region_valid"):
            assert key in tick, f"tick missing {key}"
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
