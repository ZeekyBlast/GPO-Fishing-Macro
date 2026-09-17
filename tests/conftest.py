"""pytest wiring for the self-check scripts.

Each tests/test_*.py is a script that runs on its own (`python tests/test_x.py`,
or all of them through run_all.py) and also exposes test_* entries for pytest.
The project root goes on sys.path here for pytest; paths.py does the same for
a script run directly.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
