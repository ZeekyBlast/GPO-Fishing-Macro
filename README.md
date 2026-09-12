# GPO Fishing Macro

A clean-room auto-fishing macro for **Grand Piece Online** (Roblox) on Windows.
Screen capture + color detection + a PID controller that plays the reel
minigame: no game injection, no memory reading, just simulated mouse/keyboard.

Inspired by [K's GPO Macros](https://github.com/K3nD4rk-Code-Developer/Ks-GPO-Macros)
(all code written from scratch). Built to be lightweight: **8 dependencies
instead of 104**: no PyTorch, no OCR, no audio SDK stack.

> ⚠️ **Use at your own risk.** Automating gameplay violates Roblox's Terms of
> Service and can get your account banned; use an alt, not your main.
> Personal-use software: don't sell it.

## Features

- **Auto fishing loop**: focus Roblox → cast → detect the minigame → track the
  fish zone with a PID-controlled hold/release → loot → recast
- **Auto-calibration**: finds the minigame on screen by its signature colors,
  or drag-select a region manually (works on any resolution)
- **Window-relative coordinates**: move/resize Roblox without recalibrating
- **Recovery**: black/loading screens, recast timeouts, Roblox losing focus
  (auto-pause), reel-timeout failsafe, panic key instantly releases the mouse
- **Live dashboard**: fish count, fish/hour, session uptime, event log,
  optional live detection preview
- **Bait upkeep** (work in progress, not yet reliable): crafts Rare or
  Legendary bait at Blacksmith Sen until the fish run out, or buys common bait
  at the barrel, then puts the bait back on the rod. Reads the menu's colours
  to know what happened; optionally walks to Sen and back
- **Devil-fruit auto-store**: finds which hotbar slot holds a fruit, stores
  it, and reads the result banner. No OCR
- **Discord webhook** notifications: fruit stored (with a screenshot of the
  banner naming it), milestones, errors, recast timeouts, periodic session
  stats, optional pings. Rate-limit aware, fault messages throttled
- **Rare-spawn sound alert** (experimental): watches system audio loopback for
  a loud spike (e.g. Megalodon roar), beeps + pings you
- Rebindable global hotkeys, all settings auto-saved to `settings.json`

## Install

Windows 10 or 11, 64-bit. Download **GPO Fishing Macro Setup.exe** from the
[releases page](https://github.com/ZeekyBlast/GPO-Fishing-Macro/releases) and run it.

Nothing else is needed. No Python, no .NET, no pip: the installer carries its
own copy of everything the macro imports, so it cannot collide with, or be
broken by, anything already on your machine.

It installs to `%LocalAppData%\Programs\GPO Fishing Macro`, the same
per-user location Chrome and VS Code use, so **it never asks for administrator
rights**. It adds a Start Menu entry and, if you tick the box, a desktop
shortcut. Uninstall it from Settings > Apps like anything else; that removes
the folder, the shortcuts and your settings file.

### Updating

The app checks GitHub for a newer release when it starts and shows the result
under **Settings > Updates**, where there is also a **Check for updates**
button. When one is available, one click downloads that release's installer and
runs it over your copy; your settings and calibration are kept. The launch
check is the only network call the app makes on its own, it sends nothing about
you, and the **Check for updates on launch** box turns it off.

> **Windows will warn you.** The installer is not code-signed, so SmartScreen
> shows "Windows protected your PC". A signing certificate costs a few hundred
> dollars a year, which is hard to justify for a fishing macro. Click **More
> info > Run anyway**, or build it yourself from source below and trust your
> own copy instead.

## Running from source

If you would rather build it, you need Python 3.10+ (tested on 3.11) and the
[.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0):

```bat
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
dotnet build ui-csharp -c Release
run-from-source.bat
```

`run-from-source.bat` creates the venv if it is missing, then opens the window.
`python main.py` runs the same bot with no window at all, printing to the
console, which is useful when the interface itself is what is misbehaving.

To produce an installer of your own:

```bat
.venv\Scripts\python toolsuild_installer.py
```

That bundles the runtime, runs the self-checks against it, publishes the
window as one self-contained file, and writes `dist\GPO Fishing Macro Setup
<version>.exe`.

## Quick start

1. Launch Roblox, enter GPO, equip your rod.
2. Open the **Calibration** tab:
   - Start a fishing minigame manually, then click **Auto-detect region**
     (or **Drag-select region** around the gauge).
   - Click **Test detection**: the saved screenshot should box the whole
     gauge, with the bar centre and the fish marker drawn on it.
   - Optional: bait upkeep points, devil-fruit storage, colour re-sampling if
     Roblox changes its UI.
3. Press **Start** (or the toggle hotkey, default **F6**). Panic key is
   **F8**, which stops everything and releases the mouse instantly.
4. Watch the dashboard. The reel panel shows the on-the-fish percentage for
   the current fight; tune **Bar lead** in Settings against that number.

## Casting

Holding the button longer casts farther, up to a cap GPO never shows. **Cast
hold** in Settings is the power; there is no meter to read, so the Calibration
tab's **Cast** card has a **Test cast** button: cast once with the current
hold, watch where the bobber lands, change the hold, cast again. **Pick cast
point** stores a spot over the water and the cursor moves there before every
cast, so a menu, an upkeep pass or your own hand cannot leave the line aimed
at the dock. Together they make the landing spot repeatable, which is what a
scan region wants.

Two per-cast options, both off by default, both borrowed from K's macro:
*Re-equip rod before every cast* switches to another slot and back so a
dialog that took the rod away heals in one cast, and *Reselect bait before
every cast* clicks your bait's row while the rod is held. About a second per
cast between them.

Settings can be **exported** to a file (without the webhook URL, which is a
credential), **imported** over the current ones, or found with **Open folder**,
all from the bar at the bottom of Settings.

## How the reel control works

Verified against in-game screenshots and the GPO wiki's fishing guide:

- The gauge is a black track with a tall **blue play area** (the background).
  Your hook is the **black bar** sliding inside that play area; the fish is a
  thin **white line** on the gauge that turns **green** while it sits on your
  bar. The side pill is the catch progress: it fills only while the line is
  green, and a full bar = caught fish.
- **Holding the button raises the bar; releasing lets it drop; taps give
  small raises.** The bar has momentum, so the bot uses proportional tap
  control: a **power-hold** when the fish line is far above (beyond 60 px),
  **taps** to close in, release to drop, and it brakes early (scaled by the
  bar's speed) so momentum stops on the fish. Within the bar's extent it
  coasts.
- **Catch vs escape**: a fish is only counted if the line was green
  (overlapping) right before the gauge disappears, because GPO ends the minigame the
  same way on a miss. Escapes are tracked separately on the dashboard.
- If the bar ever moves the wrong way on your setup, tick **Invert raise/drop**
  in Settings; you can also disable tap mode there to get a plain hold/release.

## Calibration notes

- All coordinates are stored **relative to the Roblox window**, so moving the
  window is fine; resizing may require recalibration.
- If detection gets flaky after a game update, re-sample the colours from the
  Calibration tab (gauge blue, bar black). The fish marker has no colour to
  sample; it is found as the thin run that is neither of those.
- Give the scan region room. The gauge is anchored to the bobber, so it slides
  as the camera moves; a box fitted tightly to it clips the bar mid-fight,
  which looks exactly like the gauge disappearing.

### Devil-fruit storing

Every fruit uses the same hotbar icon, so the macro cannot know which fruit it
is holding, and GPO refuses a fruit you already own. It therefore tries each
slot once and lets the game answer:

1. Match the fruit icon across the hotbar, only to answer *is there a fruit at
   all*, so a normal catch costs one screenshot and nothing else.
2. If there is, try the slot keys `1` to `9` then `0` in turn. Slots are equipped
   by their number key (clicking a slot does **not** equip in GPO), and which
   key a slot answers to cannot be read from where it sits: only owned slots
   are drawn while every item keeps its original binding, so a hotbar reading
   `1 2 3 4 5 6 7 0` has its eighth icon on the `0` key.
3. The green **Store Fruit** prompt only appears while a devil fruit is held,
   which makes it both the "is this slot a fruit" test and the button to press.
   It is searched for near the calibrated anchor rather than assumed to sit
   under it: it is a proximity prompt, and an anchor a few pixels off its edge
   would miss every time.
4. Watch the banner strip. `New Item <name>` means stored; the banner is
   screenshotted and posted to Discord, which is how you learn which fruit it
   was. `You can only store one of each fruit!` is written in red, and red is
   how the macro tells refusal from success without reading a character.
5. On a refusal, press the drop key (Backspace) while the fruit is still held,
   so the duplicate despawns instead of clogging the hotbar. **This destroys
   the item**, and only ever runs after GPO has itself refused the store,
   never on a guess, never on a click that got no answer.

   *Drop fruits you already own* gates the whole feature: switch it off and
   storing stops too. Storing without dropping fills the hotbar with duplicates
   that nothing can clear, which is worse than leaving fishing alone.
6. Re-equip the rod, and put the cursor back where it was found. The cast aims
   at wherever the cursor is, so a routine that clicks menus has to hand the
   aim back; otherwise the next cast goes into the dock, or into the bait list
   that sits under the storage prompt's position once you are at the water.

Only one fruit is handled per pass, with the hotbar re-read each time: removing
an item resizes the bar, so a slot's pixel position is only meaningful for the
frame it was measured in.

Refusals are normal and are not logged as errors. Calibration needs four
things, all on the Calibration tab: the **fruit icon** (snip one from a hotbar
slot), the **hotbar row** (a box containing every slot, which does not need to
line up with them), the **banner strip**, and a point anywhere on the **Store
button**, which anchors the search for it.

### Bait upkeep

> **Work in progress.** Auto-craft and auto-buy are calibratable and run, but
> they have not been proven across a long session yet. Expect misses; watch the
> first few passes, and leave both off if you are not around to step in.

Rare and Legendary bait are craft-only: two rare fish or one legendary fish
each, at Blacksmith Sen on the Shells Town dock. A Devil Fruit Rod session
lives on them, and every fruit fished up is a legendary fish that did not come
back as bait, so the stack drains and has to be topped up from the catch.
Common bait is bought at the bait barrel. **Craft or buy, not both**: switching
one on in Settings switches the other off.

Every `N` catches the macro runs one upkeep pass, and every step of it reads
the screen before it clicks, because with a rod in hand a click that lands on
nothing is a cast:

1. Press `T`, wait for Sen's *"are you interested?"* dialogue to appear where
   the **Yes** button was calibrated, click it, wait for the menu.
2. Click your bait's recipe row; the `N/M` counter under the **+** slot
   appearing in red is the proof it selected. Click **+**: a list of your
   eligible fish opens beside the menu. Click its first row and the counter
   turns **green** (two-fish recipes go red at `1/2` and it adds another).
   Click the green **CRAFT** slab, searched for near its anchor like the
   Store Fruit prompt. The counter going red again is one bait made. With
   two or more of the same fish, CRAFT asks how many instead: the slider is
   dragged to its end and **Craft Selected** takes the whole stack at once.
3. Repeat until **+** opens nothing and the game says *"You dont have any
   eligible materials to add!"*. That is the stop; nothing is counted.
4. Close with the red X, click the **...** bubble Sen leaves at the bottom
   (until it is clicked the conversation is still open and `T` does nothing),
   then click your bait's row in the **Fishing Baits** panel that shows while
   the rod is held. GPO drops the selection, so this runs after every pass.

Buying is the same shape: hold the barrel's key, and only once the quantity
box has visibly appeared type the amount, then Confirm. Typing with no box
focused would go to the game, so the routine bails before a keystroke if the
dialog is not there.

**Calibrate crafting** on the Calibration tab walks you through it, because
the points are never all on screen at once: it tells you how to get the game
into each state (dialogue up, menu open with your bait selected, fish list
open, the stack dialog if you have one, the bubble after closing, rod held),
waits for OK, then has you click only what that screen shows. Buying is the barrel's quantity box and Confirm.

**Walking.** If your fishing spot is outside Sen's prompt range, turn on *Walk
to Sen and back* and snip two things: the white `T` badge from his prompt
(standing at him), and his floating **Blacksmith Sen** name (standing exactly
where you fish from). Both legs then stop on what is on screen, never on a
timer. Going in, the walk keys are held until the badge shows. Coming back,
the return keys are held until his name is back where it sat when you snipped
it: the camera never turns on WASD, so where that label is on screen is where
you are. Any overshoot is trimmed with short taps sized by the speed just
measured. A timer only lands right if both legs run at one speed, and a
sprint, a bump from another player or a laggy frame all put a timed leg in
the sea; a measured stop does not care. If the name is ever out of sight the
bot stops on the dock and says so rather than walk blind. Set Roblox's camera
to **Classic** first; Follow mode turns a held `D` into a curve. If five casts
in a row get no bite after a walk, the bot stops and says so rather than
fishing the dock all night.

## Console mode

```bat
.venv\Scripts\python main.py --console
```

Same bot, no window: hotkeys work, events print to the terminal and
`gpo_macro.log`.

## Diagnostics

```bat
.venv\Scripts\python -m gpo_macro.vision                 # live detection preview (q to quit)
.venv\Scripts\python -m gpo_macro.controller             # PID tuning simulation
.venv\Scripts\python tests
un_all.py                    # every self-check
.venv\Scripts\python tools\grab_raw.py --wait            # dump raw gauge frames
```

## Project layout

```
main.py              entry point (console bot / --rpc engine)
gpo_macro/
  config.py          typed settings, JSON persistence
  capture.py         mss grabs + Roblox window find/focus (ctypes)
  vision.py          color masks, bar reading, auto-calibration
  controller.py      PID -> hold/release decisions
  input.py           pynput input + failsafe + hotkey helpers
  fisher.py          bot thread: fishing state machine
  tasks.py           bait craft/buy + the walk to Sen, fruit template match + store
  notify.py          Discord webhooks (worker thread)
  sound_alert.py     WASAPI loopback spike detector (experimental)
  stats.py           thread-safe counters + event bus
  hotkeys.py         rebindable global hotkeys
  form.py            the settings form, described once for both UIs
  rpc.py             headless engine: JSON lines on stdin/stdout
  theme.py           colour, type and spacing tokens (see DESIGN.md)
ui-csharp/           the WPF window: dashboard, settings, calibration
  Engine.cs          spawns and speaks to `main.py --rpc`
  Theme.xaml         chrome tokens, kept in step with theme.py by test_theme.py
  OverlayWindow.cs   full-desktop region drag and point picking
tests/               self-checks; `python tests/run_all.py` runs them all
  fixtures/          frozen gauge captures with known readings
tools/
  grab_raw.py        dump raw gauge frames for tuning
  build_release.py   package a clean zip into dist/
settings.example.json   every knob at its default; your real settings.json
                        is git-ignored because it holds your webhook URL
```

Your own `settings.json`, `templates/fruit.png` and `captures/` stay out of
both git and the release zip: the first is a credential, the others are yours.
