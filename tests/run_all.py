"""Run every self-check in this folder.

    .venv\\Scripts\\python tests\\run_all.py

Each test is a plain script that asserts, so they are run as subprocesses:
one failing test reports and the rest still run.
"""

from __future__ import annotations

import paths

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# test_rpc spawns the engine and test_fisher drives a bot thread, so they are
# slower than the rest; order is cheapest-first to fail fast.
ORDER = ["test_theme.py", "test_control.py", "test_vision.py", "test_tasks.py",
         "test_notify.py", "test_fisher.py", "test_rpc.py"]


def main() -> int:
    found = sorted(p.name for p in HERE.glob("test_*.py"))
    scripts = [n for n in ORDER if n in found] + [n for n in found if n not in ORDER]

    failures = []
    for name in scripts:
        print(f"{name:<20}", end="", flush=True)
        result = subprocess.run([sys.executable, str(HERE / name)],
                                cwd=str(paths.ROOT), capture_output=True, text=True)
        if result.returncode == 0:
            print("PASS")
            continue
        print("FAIL")
        failures.append(name)
        tail = (result.stdout + result.stderr).strip().splitlines()[-12:]
        for line in tail:
            print("    " + line)

    print()
    if failures:
        print(f"{len(failures)} of {len(scripts)} failed: {', '.join(failures)}")
        return 1
    print(f"all {len(scripts)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
