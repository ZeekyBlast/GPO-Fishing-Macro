"""Build the installer people actually download.

    .venv\\Scripts\\python tools\\build_installer.py

Three steps, in order, each verified before the next:

  1. tools/build_runtime.py assembles the bundled Python and runs the real
     self-checks against it.
  2. dotnet publishes the window as one self-contained file, so the machine
     needs no .NET runtime and the install folder is not a heap of DLLs.
  3. Inno Setup packs both into a per-user installer that needs no
     administrator rights.

The result is dist/GPO Fishing Macro Setup <version>.exe.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
ISS = ROOT / "installer" / "GpoFishingMacro.iss"

WEBHOOK = re.compile(r"discord(app)?\.com/api/webhooks/\d+/[\w-]+", re.I)

ISCC_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def find_iscc() -> Path:
    for candidate in ISCC_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "Inno Setup 6 not found. Install it with:\n"
        "    winget install --id JRSoftware.InnoSetup --source winget")


def run(label: str, command: list[str]) -> None:
    print(f"\n=== {label} ===")
    result = subprocess.run(command, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit(f"{label} failed")


def publish_app() -> None:
    target = BUILD / "app"
    if target.exists():
        shutil.rmtree(target)
    run("publishing the window", [
        "dotnet", "publish", str(ROOT / "ui-csharp"), "-c", "Release",
        "-o", str(target), "-nologo"])
    exe = target / "GPO Fishing Macro.exe"
    if not exe.is_file():
        raise SystemExit(f"expected {exe.name} in {target}")
    stray = [p.name for p in target.iterdir() if p.name != exe.name]
    if stray:
        print(f"note: extra files alongside the exe: {', '.join(stray)}")
    print(f"{exe.name}: {exe.stat().st_size / 1_000_000:.0f} MB, single file")


def audit_sources() -> None:
    """Nothing carrying a credential may reach the installer.

    The [Files] section names what ships, but it globs gpo_macro\\*.py and the
    whole runtime tree, so the contents are checked rather than trusted.
    """
    problems = []
    for folder in (ROOT / "gpo_macro", BUILD / "runtime", BUILD / "app"):
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".py", ".json", ".md", ".txt"}:
                if WEBHOOK.search(path.read_text(encoding="utf-8", errors="ignore")):
                    problems.append(str(path.relative_to(ROOT)))
    for name in ("settings.example.json", "README.md", "main.py"):
        text = (ROOT / name).read_text(encoding="utf-8", errors="ignore")
        if WEBHOOK.search(text):
            problems.append(name)
    if problems:
        raise SystemExit("REFUSING TO BUILD, credential found in:\n  " + "\n  ".join(problems))
    print("audit clean: no credentials in anything the installer packs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-runtime", action="store_true",
                        help="reuse build/runtime instead of rebuilding it")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from gpo_macro import __version__

    iscc = find_iscc()

    if args.skip_runtime and (BUILD / "runtime" / "python.exe").is_file():
        print("reusing build/runtime")
    else:
        run("building the bundled runtime",
            [sys.executable, str(ROOT / "tools" / "build_runtime.py")])

    publish_app()
    audit_sources()

    DIST.mkdir(exist_ok=True)
    run("packing the installer", [str(iscc), f"/DAppVersion={__version__}", str(ISS)])

    setup = DIST / f"GPO Fishing Macro Setup {__version__}.exe"
    if not setup.is_file():
        raise SystemExit(f"Inno Setup reported success but {setup.name} is missing")
    print(f"\n{setup.relative_to(ROOT)}  ({setup.stat().st_size / 1_000_000:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
