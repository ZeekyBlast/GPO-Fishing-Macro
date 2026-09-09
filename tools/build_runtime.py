"""Assemble the self-contained Python runtime the installer ships.

    .venv\\Scripts\\python tools\\build_runtime.py

Produces build/runtime/: an embedded CPython plus the packages the engine
imports. An installed copy uses this and nothing else, so a person does not
have to install Python, does not have to run pip, and cannot end up running
against some other interpreter that happens to be on PATH.

The packages are copied out of the project venv rather than downloaded, so the
runtime is built from the exact versions the tests were run against.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
RUNTIME = BUILD / "runtime"
CACHE = BUILD / "cache"

PY_VERSION = "3.11.9"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"

# What to copy is resolved from the venv's own dependency metadata rather
# than hand-listed: an allowlist of top-level names misses transitive
# dependencies, which is how a bundle that imported numpy and OpenCV fine
# still died on pynput's dependency on `six`.
SKIP_DISTS = {"pip", "setuptools", "wheel", "pkg_resources", "distribute"}
# Wheels ship their native DLLs in a sibling "<name>.libs" folder that the
# package loads by relative path. Miss it and the import fails at the C
# extension with a bare "DLL load failed".
LIB_DIRS = "*.libs"
# Weight that buys nothing at runtime.
PRUNE_DIRS = {"__pycache__", "tests", "test", "testing", "examples", "doc", "docs"}
PRUNE_SUFFIXES = {".pyi", ".pyx", ".pxd", ".c", ".h", ".cpp", ".chm", ".pdb"}
# 31 MB of video demuxing. The engine only ever calls imread, imwrite, resize,
# cvtColor and matchTemplate, and the tests below prove it stays true.
PRUNE_FILES = {"opencv_videoio_ffmpeg500_64.dll"}


def fetch_embed() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / f"python-{PY_VERSION}-embed-amd64.zip"
    if archive.exists():
        print(f"using cached {archive.name}")
        return archive
    print(f"downloading {EMBED_URL}")
    urllib.request.urlretrieve(EMBED_URL, archive)
    return archive


def site_packages() -> Path:
    for candidate in (ROOT / ".venv" / "Lib" / "site-packages",
                      ROOT / "venv" / "Lib" / "site-packages"):
        if candidate.is_dir():
            return candidate
    raise SystemExit("no project venv found; create one and install requirements.txt first")


def required_distributions() -> set[str]:
    """Every distribution requirements.txt pulls in, transitively.

    Resolved against the venv, which is the environment the tests ran in.
    """
    from importlib import metadata

    def base(spec: str) -> str:
        for stop in "[;<>=!~ (":
            spec = spec.split(stop)[0]
        return spec.strip().lower().replace("_", "-")

    roots = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            roots.append(base(line))

    seen: set[str] = set()
    queue = list(roots)
    while queue:
        name = queue.pop()
        if name in seen or name in SKIP_DISTS:
            continue
        seen.add(name)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            print(f"note: {name} is not installed in the venv")
            continue
        for requirement in dist.requires or []:
            # Extras are optional by definition; the engine does not ask for any.
            if "extra ==" in requirement:
                continue
            queue.append(base(requirement))
    return seen


def top_level_names(distribution_names: set[str]) -> set[str]:
    """Importable top-level names owned by those distributions."""
    from importlib import metadata

    names: set[str] = set()
    for name in sorted(distribution_names):
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        declared = dist.read_text("top_level.txt")
        if declared:
            names.update(n.strip() for n in declared.splitlines() if n.strip())
            continue
        # No top_level.txt: infer from the first path segment of its files.
        for file in dist.files or []:
            head = file.parts[0]
            if head.endswith((".dist-info", ".data", ".pth")) or head == "__pycache__":
                continue
            names.add(head[:-3] if head.endswith(".py") else head)
    return {n for n in names if _is_safe(n)}


def _is_safe(name: str) -> bool:
    """A name must address something directly inside site-packages.

    RECORD files legitimately contain entries like "../../include/foo.h", so an
    unguarded first path segment can be "..", and copying that pulls in the
    whole library rather than one package.
    """
    return bool(name) and name not in {".", ".."} and "/" not in name and "\\" not in name


def prune(folder: Path) -> None:
    for path in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and path.name in PRUNE_DIRS:
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file() and (path.suffix.lower() in PRUNE_SUFFIXES
                                 or path.name in PRUNE_FILES):
            path.unlink(missing_ok=True)


def build() -> None:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)

    with zipfile.ZipFile(fetch_embed()) as zf:
        zf.extractall(RUNTIME)

    # The embedded build reads its search path from this file rather than the
    # registry, which is what keeps it isolated from any other install.
    pth = next(RUNTIME.glob("python*._pth"))
    pth.write_text("\n".join([pth.read_text(encoding="utf-8").split("\n")[0],
                              ".", "Lib", "import site", ""]), encoding="utf-8")

    lib = RUNTIME / "Lib"
    lib.mkdir()
    source = site_packages()
    dists = required_distributions()
    names = top_level_names(dists)
    print(f"{len(dists)} distributions -> {len(names)} top-level modules")

    missing = []
    for name in sorted(names):
        found = False
        for candidate in (source / name, source / f"{name}.py"):
            if candidate.is_dir():
                shutil.copytree(candidate, lib / name, dirs_exist_ok=True)
                found = True
            elif candidate.is_file():
                shutil.copy2(candidate, lib / candidate.name)
                found = True
        for extension in source.glob(f"{name}*.pyd"):     # single-file extensions
            shutil.copy2(extension, lib / extension.name)
            found = True
        if not found:
            missing.append(name)
    if missing:
        print(f"note: no files found for: {', '.join(missing)}")

    for extra in source.glob(LIB_DIRS):
        shutil.copytree(extra, lib / extra.name, dirs_exist_ok=True)
        print(f"copied native DLLs: {extra.name}")

    prune(lib)

    size = sum(f.stat().st_size for f in RUNTIME.rglob("*") if f.is_file())
    print(f"runtime built: {RUNTIME.relative_to(ROOT)}  ({size / 1_000_000:.0f} MB)")


def verify() -> None:
    """The runtime is only useful if the engine actually starts under it."""
    python = RUNTIME / "python.exe"
    checks = [
        ("imports", [str(python), "-c",
                     "import cv2, numpy, mss, pynput, requests; print(cv2.__version__)"]),
        ("engine", [str(python), str(ROOT / "main.py"), "--rpc"]),
    ]
    result = subprocess.run(checks[0][1], capture_output=True, text=True, cwd=str(ROOT))
    if result.returncode != 0:
        print(result.stdout, result.stderr)
        raise SystemExit("bundled runtime cannot import the engine's dependencies")
    print(f"imports ok (OpenCV {result.stdout.strip()})")

    engine = subprocess.Popen(checks[1][1], cwd=str(ROOT), stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        try:
            engine.stdin.write('{"id":1,"cmd":"ping"}\n')
            engine.stdin.flush()
        except OSError:
            # The child is already gone. Its stderr says why, and that is the
            # message worth printing rather than "Invalid argument".
            raise SystemExit("engine exited immediately:\n" + engine.stderr.read())
        for _ in range(200):
            line = engine.stdout.readline()
            if not line:
                break
            if '"hello"' in line:
                print("engine speaks the protocol under the bundled runtime")
                break
        else:
            raise SystemExit("no hello frame from the bundled runtime")
        engine.stdin.write('{"cmd":"shutdown"}\n')
        engine.stdin.flush()
        engine.wait(timeout=15)
    finally:
        if engine.poll() is None:
            engine.kill()

    run_tests(python)


def run_tests(python: Path) -> None:
    """Run the real self-checks against the bundled runtime.

    Pruning is guesswork otherwise. These exercise the OpenCV calls the engine
    actually makes, so if a trimmed DLL mattered, this is where it shows up.
    """
    tests = ROOT / "tests"
    for name in ("test_vision.py", "test_tasks.py", "test_control.py", "test_theme.py"):
        # The embedded runtime does not put a script's own directory on
        # sys.path the way a normal interpreter does, so the tests' `import
        # paths` would fail for a reason that has nothing to do with them.
        bootstrap = (f"import sys, runpy; sys.path.insert(0, r'{tests}'); "
                     f"runpy.run_path(r'{tests / name}', run_name='__main__')")
        result = subprocess.run([str(python), "-c", bootstrap],
                                cwd=str(ROOT), capture_output=True, text=True)
        status = "ok" if result.returncode == 0 else "FAILED"
        print(f"  {name:<18} {status}")
        if result.returncode != 0:
            print((result.stdout + result.stderr)[-1500:])
            raise SystemExit(f"{name} fails under the bundled runtime")
    print("self-checks pass under the bundled runtime")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if not args.verify_only:
        build()
    verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
