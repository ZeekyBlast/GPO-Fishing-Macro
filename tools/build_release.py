"""Package a clean release zip into dist/.

    .venv\\Scripts\\python tools\\build_release.py

Copies an explicit allowlist rather than excluding a denylist. That direction
matters: settings.json holds a Discord webhook URL, which is a credential, and
a forgotten exclusion would ship it. Anything not named below simply does not
travel - and the packager refuses to finish if a credential turns up anyway.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# What a person who downloads this actually needs.
FILES = [
    "main.py",
    "requirements.txt",
    "settings.example.json",
    "start.bat",
    "README.md",
    "DESIGN.md",
]
TREES = [
    ("gpo_macro", "*.py"),
]
SHELL_BUILD = ROOT / "ui-csharp" / "bin" / "Release" / "net8.0-windows"
SHELL_SUFFIXES = {".exe", ".dll", ".json"}      # runtime needs the .deps/.runtimeconfig json

# Anything that looks like a live webhook stops the build.
WEBHOOK = re.compile(r"discord(app)?\.com/api/webhooks/\d+/[\w-]+", re.I)


def build_shell() -> None:
    print("building the C# shell (Release)...")
    result = subprocess.run(
        ["dotnet", "build", str(ROOT / "ui-csharp"), "-c", "Release", "-nologo"],
        cwd=str(ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-2000:], result.stderr[-2000:])
        raise SystemExit("dotnet build failed - is the .NET 8 SDK installed?")


def stage(target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    for name in FILES:
        source = ROOT / name
        if not source.exists():
            raise SystemExit(f"missing {name} - cannot package an incomplete release")
        shutil.copy2(source, target / name)

    for folder, pattern in TREES:
        for source in sorted((ROOT / folder).glob(pattern)):
            destination = target / folder / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    if not SHELL_BUILD.exists():
        raise SystemExit(f"no Release build at {SHELL_BUILD}")
    for source in sorted(SHELL_BUILD.iterdir()):
        if source.is_file() and source.suffix.lower() in SHELL_SUFFIXES:
            destination = target / "MacroUI" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def audit(target: Path) -> None:
    """Last line of defence: read every packaged text file back."""
    problems = []
    for path in sorted(target.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {
                ".py", ".json", ".md", ".bat", ".txt", ".xaml", ".cs"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if WEBHOOK.search(text):
            problems.append(f"{path.relative_to(target)} contains a webhook URL")
        if path.name == "settings.json":
            problems.append("settings.json must never be packaged")
    if problems:
        raise SystemExit("REFUSING TO PACKAGE:\n  " + "\n  ".join(problems))

    example = json.loads((target / "settings.example.json").read_text(encoding="utf-8"))
    if example["webhook"]["url"] or example["webhook"]["user_id"]:
        raise SystemExit("settings.example.json carries personal webhook data")
    print("audit clean - no credentials in the package")


def zip_up(target: Path, version: str) -> Path:
    archive = DIST / f"gpo-fishing-macro-{version}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                zf.write(path, Path(target.name) / path.relative_to(target))
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true",
                        help="package the existing Release output as-is")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from gpo_macro import __version__

    if not args.skip_build:
        build_shell()

    target = DIST / f"gpo-fishing-macro-{__version__}"
    stage(target)
    audit(target)
    archive = zip_up(target, __version__)

    size = archive.stat().st_size / 1_000_000
    print(f"\n{archive.relative_to(ROOT)}  ({size:.1f} MB)")
    print(f"staged folder: {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
