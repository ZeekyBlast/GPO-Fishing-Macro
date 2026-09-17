#!/usr/bin/env sh
# Launcher for a source checkout on Linux: starts the engine and opens the
# window in your browser (python main.py --serve). It needs Python 3.10+, an
# X11 or XWayland session (DISPLAY set), and the game in an X11 window - see
# "Linux" in README.md.
#
# It creates the project venv if it is missing and installs requirements.txt
# into it. Arguments go to main.py: `--console` for the terminal-only bot,
# `--no-browser` to print the address instead of opening it.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "First run: creating virtual environment..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -r requirements.txt
fi

if [ "$#" -eq 0 ]; then
    set -- --serve
fi
exec .venv/bin/python main.py "$@"
