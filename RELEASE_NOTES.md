## 1.3.2 - Linux support, the browser window, and the dusk fix

**Pre-release: no Windows installer yet.** Windows users on 1.2.0 are not prompted to update. The `GPO Fishing Macro Setup 1.3.2.exe` will be attached once it is built and checked on Windows; until then, Windows runs from source exactly as before (`run-from-source.bat`).

### Linux

The whole macro runs on Linux under X11 or XWayland:

```sh
git clone https://github.com/ZeekyBlast/GPO-Fishing-Macro.git
cd GPO-Fishing-Macro
./run-from-source.sh
```

That makes the venv, starts the engine and opens the window in your browser at `http://127.0.0.1:8790`. The game has to be an X11 window (Sober or a Wine client under XWayland; `xwininfo -root -tree | grep -i roblox` should print something). Calibration is done on a capture of the game, in the browser - no overlay. Add `--console` for the terminal-only bot. See "Linux" in the README for the details (focus, sound).

### What changed

- **Dusk fix (1.3.1, shipped as 1.3.2)** - after ~40 minutes the sea passed for a gauge: the bot pressed the mouse on an empty cast and pulled the line back every three seconds. A gauge is now required to be a narrow pill with dark borders, and a bite takes three frames. Also: the walk home's trim now converges however far it overshot.

- **The browser window** - `python main.py --serve` on any OS: the same dashboard, calibration and settings, served on this machine only, with a per-launch token.
- **The engine on Linux** - Win32 and X11 backends behind one interface; grabs are window-relative all the way down, so the game can be on any monitor; the corner failsafe covers every monitor; the game's own window class outranks a browser tab titled "Roblox".
- **Fixes** - the toggle key resumes a paused bot instead of stopping it (alt-tabbing no longer ends the session); pausing releases the mouse; single-character hotkeys register (`<1>` used to bind the left mouse button); settings patches can no longer be read half-written by the bot thread.
- **Settings** - a status line under Save; a feature's knobs appear when you switch it on (57 rows become 24 by default; the filter finds any of them); hotkeys are captured from a keypress, and a duplicate or unbindable pair is refused before it is saved.
- **Dashboard** - caught and per hour as two large counters; a compact always-on-top strip; keyboard focus rings; a slightly larger small type scale.
- **Tooling** - `pyproject.toml`, ruff and mypy clean, pytest, CI on Ubuntu (under Xvfb, with the X11 backend exercised against a real window) and Windows including a `dotnet build` of the WPF window, and a lock file.

Automating gameplay violates Roblox's Terms of Service and can get your account banned; use an alt, not your main.
