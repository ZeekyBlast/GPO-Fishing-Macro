"""Self-check for the hold/release law.

Run: python test_control.py
"""

import paths  # noqa: F401  - puts the project root on sys.path
from gpo_macro.config import ControllerConfig
from gpo_macro.controller import should_hold, simulate

cfg = ControllerConfig()

# Direction: hold raises the bar, so hold whenever the bar sits below the fish.
assert should_hold(300.0, 0.0, 200.0, 0.0, cfg)          # bar below => raise
assert not should_hold(200.0, 0.0, 300.0, 0.0, cfg)      # bar above => drop
assert should_hold(200.0, 0.0, 300.0, 0.0, ControllerConfig(invert=True))

# The bar lead is the brake: rising fast at the fish must release early.
assert not should_hold(210.0, -300.0, 200.0, 0.0, cfg), "no braking on approach"
# ...but not so early that it stalls short of a far target.
assert should_hold(400.0, -300.0, 200.0, 0.0, cfg), "braked too early"

# No deadzone: a bar sitting a hair low must still be told to hold, or it
# sinks away between corrections.
assert should_hold(201.0, 0.0, 200.0, 0.0, cfg)

# Tracking, against fish that dart. Thresholds sit well under the measured
# means (99% calm / 80% erratic) so this catches regressions, not noise.
for erratic, floor in ((False, 95.0), (True, 72.0)):
    scores = [simulate(cfg, 34.0, 45.0, seed, erratic)[0] / 45.0 * 100 for seed in range(8)]
    worst, mean = min(scores), sum(scores) / len(scores)
    label = "erratic" if erratic else "calm"
    print(f"{label:>8} fish: on-target mean={mean:.1f}% worst={worst:.1f}%")
    assert mean >= floor, f"{label} tracking regressed to {mean:.1f}%"

print("all control checks passed")
