"""Shared paths for the test scripts.

Every test imports this first. It puts the project root on `sys.path` so
`import gpo_macro` works when a test is run directly from anywhere, and it
hands out a per-run scratch directory so nothing a test writes lands in the
project folder - an earlier version generated `fixtures/fruit_icon.png` beside
the real fixtures, which made a build artifact look like checked-in data.
"""

from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_scratch: Path | None = None


def scratch() -> Path:
    """A temp directory for this run, removed when the process exits."""
    global _scratch
    if _scratch is None:
        _scratch = Path(tempfile.mkdtemp(prefix="gpo-test-"))
        atexit.register(shutil.rmtree, _scratch, ignore_errors=True)
    return _scratch


def fixture(name: str) -> str:
    """Absolute path to a checked-in fixture image."""
    return str(FIXTURES / name)
