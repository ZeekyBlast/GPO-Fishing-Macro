#!/usr/bin/env sh
# Developer launcher for a source checkout on Linux.
#
# The window is Windows-only for now, so this runs the engine in console
# mode: hotkeys work, events print here and to gpo_macro.log. It needs
# Python 3.10+, an X11 or XWayland session (DISPLAY set), and the game in an
# X11 window - see "Linux" in README.md. Calibration still needs the window;
# copy a settings.json calibrated elsewhere, or set scan_region by hand.
#
# It creates the project venv if it is missing and installs requirements.txt
# into it. Extra arguments go to main.py (e.g. --settings other.json).
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "First run: creating virtual environment..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -r requirements.txt
fi

exec .venv/bin/python main.py "$@"
