# DESIGN.md: GPO Fishing Macro

The app is an **instrument**, not a game skin. Two people use it: someone
glancing across the room to check it is still fishing, and the same person an
hour later reading live numbers to tune a control loop. Everything below serves
those two readings and nothing else.

Tokens live in [`gpo_macro/theme.py`](gpo_macro/theme.py) and, for the shell's
chrome, in [`ui-csharp/Theme.xaml`](ui-csharp/Theme.xaml). Nothing hard-codes a
colour, a font or a gap, and `test_theme.py` fails if the two copies drift.

## The two processes

The window is a WPF app; everything it shows comes from Python. `MacroUI.exe`
spawns `python main.py --rpc` and talks to it in newline-delimited JSON over
stdin/stdout ([`gpo_macro/rpc.py`](gpo_macro/rpc.py)): commands in, a 10 Hz
`tick` and every bus event out.

The split is drawn where the risk is. Vision, capture, input, hotkeys and the
control loop stay in Python, tuned against real gameplay and untouched by the
move. The shell owns pixels and nothing else, so a bug in it cannot mis-aim a
click. Three things follow from that, and they are the reason this is not just
a second copy of the UI:

- **The settings form is not written twice.** It is generated from the schema
  the engine sends at startup, which is [`gpo_macro/form.py`](gpo_macro/form.py)
  read aloud. A new knob appears in both windows with no C# change.
- **State and event colours travel with the data.** `theme.py` remains the one
  place that decides what green means; `Theme.xaml` only holds chrome, and
  `test_theme.py` compares the two palettes.
- **The engine decides what "Roblox" means.** The overlay reports physical
  screen pixels; the shell asks the engine for the client origin and subtracts
  it, so window-relative coordinates are computed against the same window the
  bot will actually drive.

Calibration coordinates are the one place a shell bug *would* be silent, so
they are handled carefully: the process is manifested PerMonitorV2 to match
`main.py`, and the overlay converts its own mouse events with `PointToScreen`
rather than reading the cursor, because by the time a handler runs the pointer has
moved, and a quick press-and-drag would anchor pixels away from the press.

## Theme

Dark only, and deliberately. This sits beside a fullscreen Roblox window at
night. There is no light mode and no toggle.

## Colour

Near-black surfaces, one committed accent, two fault colours.

| Token | Value | Role |
|---|---|---|
| `BG` | `#0C0E10` | window ground |
| `SURFACE` | `#141719` | raised panel |
| `SURFACE_2` | `#1B1F22` | input, inset well, meter track |
| `LINE` | `#252A2E` | hairline |
| `LINE_STRONG` | `#3A4247` | hover, focus rim |
| `TEXT` | `#E9ECEE` | primary |
| `TEXT_2` | `#BFC6C9` | secondary prose |
| `MUTED` | `#8A9296` | labels |
| `FAINT` | `#646E72` | metadata |
| `GREEN` `GREEN_DIM` | `#4EC98A` `#1E3A2C` | **the accent** |
| `AMBER` | `#E0A33A` | attention, not yet a fault |
| `RED` | `#C2453F` | fault |

**Green is the only accent, and it means one thing: the hook is on the fish.**
That is the entire job of this program, so green fills the on-target meter,
marks HOLD, marks a catch, and marks Start. It is never decoration and never
marks mere selection; the tab bar stays grey for exactly that reason.

Amber is for states that want attention but are not yet wrong: waiting,
paused, recast timeout, an uncalibrated region. Red is only for faults.

**Colour is never the sole signal.** Every coloured thing prints a word beside
it: `HOLD` / `drop`, `on the fish` / `off the fish`, the state name in the
status strip. Read the app in greyscale and nothing is lost.

Contrast is verified, not eyeballed. `theme.audit()` checks every
foreground-on-surface pair the app actually renders against WCAG AA (4.5:1 for
text, 3:1 for metadata and fault fills), and `test_theme.py` fails the build if
any pair falls short:

```bash
python tests/test_theme.py
```

That check is what caught `FAINT` at `#5B6569`: fine on `BG`, 2.77:1 on
`SURFACE_2`. It is now `#646E72`.

## Typography

**Cascadia Mono** for anything that is a number, an identifier, a label or a
status. **Segoe UI** for sentences a human wrote. The rule is literal: if it is
data, it is mono; if it is prose, it is sans. That split is what makes this read
as an instrument rather than an app.

Every counter, every telemetry reading, every region and colour value, the
event log, and the small-caps section headings are mono. Only the notes,
button labels and field labels are sans.

Scale is fixed, not fluid: 10px metadata · 11px labels · 12px UI · 14px
headings · 15px the live readout · 22px counters · 30px the state word.

## Layout

- **Panels are for things that are genuinely separate**: the status strip, one
  counter tile, the reel readout, the log, one settings section. Not for
  fencing off every heading. Nested panels are always wrong here.
- Headings and explanatory notes sit *on the page*, above the panel they
  describe, never inside it.
- `PAD` 12px between panels and inside them, `GAP` 6px between related
  controls, and no interactive row below 26px.
- The dashboard is fixed top to bottom (status, counters, reel, log), so the
  thing you glance at never moves.

## Components

- **Status strip.** State word at 30px mono, the Roblox window it is driving,
  a sentence saying what happens next, and the three controls. One row.
- **Counter tile.** Small-caps mono label over a 22px mono number. Six of them,
  equal width.
- **Reel readout.** The tuning surface: HOLD/drop, on/off the fish, on-target
  percentage as both a number and a meter, then bar and fish positions with
  their velocities and the error between them.
- **Buttons.** Quiet by default: surface fill, hairline border, plain text.
  Only Start is filled with the accent. Panic is a quiet button with red text,
  not a red slab: it should be findable, not shouted.
- **Log line.** `HH:MM:SS` then a one-character mark then the message, coloured
  by kind. Mono throughout, because most lines are numbers.

## Empty states

Every one teaches rather than shrugging. `"Launch Roblox and open GPO."`,
`"Calibrate the scan region before starting."`, `"Ready. Start here or press
f6."`, `"waiting for a bite, readings appear here during a fight"`. The
dashboard never says only "idle".

## The log

The event log is the one control that takes unbounded input, and it is capped
at 300 lines for that reason. Two things about it are deliberate rather than
incidental:

- It does not virtualize. Three hundred rows do not need it, and the
  virtualizing panel's item generator is where a crash lived: scrolling to the
  tail from inside the collection's own change notification made WPF run a
  layout pass while more lines were still arriving, and the generator's count
  drifted from the collection's.
- The scroll to the tail is deferred below layout priority and coalesced, so a
  burst of twenty events costs one scroll once the list has settled.

## Fallbacks

`start.bat` launches the window. `python main.py` runs the same bot with no
window and no shell, reading the same `settings.json`, useful when the
interface itself is the thing that is broken. Calibration needs the window.

## Discord

The webhook posts the same vocabulary the app shows: the embed colours are the
theme tokens, and the session embed carries the same six numbers as the counter
tiles. Faults are rate-limited and report what they suppressed rather than
going quiet; the Settings tab shows delivery state, because a webhook you
cannot verify is a webhook you do not trust. The URL is a credential: it is
masked in the field, never logged, and never echoed into an embed.

A stored devil fruit posts the banner screenshot itself rather than a parsed
name. The game already draws the name legibly; a picture cannot be misread,
and it costs no OCR dependency.
