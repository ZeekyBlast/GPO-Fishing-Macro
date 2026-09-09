"""The hold/release decision for GPO's fishing gauge.

Convention: y grows downward. Holding the button RAISES the bar; releasing
lets gravity drop it. So the bar must be held whenever it sits below the fish.

The decision is bang-bang on LEAD-COMPENSATED positions - where the bar will
be in `bar_lead` seconds against where the fish will be in `fish_lead`
seconds. Two properties matter:

- No deadzone. Toggling at the scan rate is what makes the bar hover; a
  deadzone lets it sink until the error grows past the threshold, then
  over-corrects, which is a sawtooth, not tracking.
- The lead on the bar IS the brake. Momentum is subtracted from the position
  the controller reasons about, so it stops at the fish instead of past it,
  with no separate "release early" rule to get out of step with reality.
"""

from __future__ import annotations

import argparse

from .config import ControllerConfig


def should_hold(bar_y: float, bar_vel: float, fish_y: float, fish_vel: float,
                cfg: ControllerConfig) -> bool:
    """True => keep the left mouse button down (raise the bar)."""
    error = (bar_y + bar_vel * cfg.bar_lead) - (fish_y + fish_vel * cfg.fish_lead)
    if cfg.invert:
        error = -error
    return error > 0.0


def _demo() -> None:  # pragma: no cover - simulation
    """Gauge simulation: python -m gpo_macro.controller [--fish-lead 0.06 ...]

    Physics are sized from real captures (343 px pill, 85 px bar, bar speeds
    peaking near 320 px/s). It is a regression guard for the control law, not
    a substitute for tuning against the game.
    """
    import math
    import random

    parser = argparse.ArgumentParser(description="hold/release simulation")
    parser.add_argument("--bar-lead", type=float, default=ControllerConfig.bar_lead)
    parser.add_argument("--fish-lead", type=float, default=ControllerConfig.fish_lead)
    parser.add_argument("--hz", type=float, default=34.0, help="control loop rate")
    parser.add_argument("--seconds", type=float, default=45.0)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    cfg = ControllerConfig(bar_lead=args.bar_lead, fish_lead=args.fish_lead)
    print(f"{'fish':>10}  {'on-target':>9}  {'toggles':>7}")
    for label, erratic in (("calm", False), ("erratic", True)):
        on, total, toggles = simulate(cfg, args.hz, args.seconds, args.seed, erratic,
                                      math=math, random=random)
        print(f"{label:>10}  {on / total * 100:8.1f}%  {toggles:7d}")


def simulate(cfg: ControllerConfig, hz: float, seconds: float, seed: int,
             erratic: bool, math=None, random=None) -> tuple[float, float, int]:
    """Run the gauge sim; returns (on_target_seconds, total_seconds, toggles)."""
    import math as _math
    import random as _random
    math = math or _math
    random = random or _random

    rng = random.Random(seed)
    pill_top, pill_bottom, bar_size = 127.0, 470.0, 85.0
    lo, hi = pill_top + bar_size / 2, pill_bottom - bar_size / 2

    dt = 1.0 / hz
    hold_accel, gravity, vmax = -1100.0, 900.0, 350.0
    y, velocity = hi, 0.0
    fish = (lo + hi) / 2
    fish_target = fish

    # The controller only ever sees sampled positions, so it estimates both
    # velocities the same way the bot does - never the sim's true values.
    est_bar_vel = est_fish_vel = 0.0
    prev_bar = prev_fish = None
    holding = False
    on_target = 0.0
    toggles = 0

    for step in range(int(seconds / dt)):
        t = step * dt
        if erratic:
            # Darts to a new spot every ~0.6 s and rushes there.
            if rng.random() < dt / 0.6:
                fish_target = rng.uniform(lo, hi)
            fish += max(-450.0, min(450.0, (fish_target - fish) * 6.0)) * dt
        else:
            fish = (lo + hi) / 2 + (hi - lo) * 0.35 * math.sin(t * 0.9)
        fish = max(lo, min(hi, fish))

        if prev_bar is not None:
            est_bar_vel = 0.7 * est_bar_vel + 0.3 * (y - prev_bar) / dt
            est_fish_vel = 0.7 * est_fish_vel + 0.3 * (fish - prev_fish) / dt
        prev_bar, prev_fish = y, fish

        hold = should_hold(y, est_bar_vel, fish, est_fish_vel, cfg)
        if hold != holding:
            toggles += 1
            holding = hold

        velocity += (hold_accel if holding else gravity) * dt
        velocity = max(-vmax, min(vmax, velocity))
        y += velocity * dt
        if y <= lo or y >= hi:            # the pill's ends stop the bar dead
            y = max(lo, min(hi, y))
            velocity = 0.0

        if abs(y - fish) <= bar_size / 2:  # fish inside the bar => progress
            on_target += dt

    return on_target, seconds, toggles


if __name__ == "__main__":
    _demo()
