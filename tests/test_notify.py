"""Self-check for the Discord notifier. Never touches the network.

Run: python test_notify.py
"""

import paths  # noqa: F401  - puts the project root on sys.path
import time

from gpo_macro.config import AppConfig
from gpo_macro.notify import DiscordNotifier, valid_url
from gpo_macro.stats import Event, EventBus, Stats

GOOD = "https://discord.com/api/webhooks/123456789012345678/" + "x" * 40


class FakeTransport:
    """Records payloads and replays a scripted list of HTTP statuses."""

    def __init__(self, statuses=None):
        self.statuses = list(statuses or [])
        self.calls = []
        self.images = []

    def __call__(self, url, payload, image_path=""):
        self.calls.append(payload)
        self.images.append(image_path)
        return self.statuses.pop(0) if self.statuses else 204


def make(statuses=None, url=GOOD):
    cfg = AppConfig()
    cfg.webhook.url = url
    cfg.webhook.periodic_stats_minutes = 0
    transport = FakeTransport(statuses)
    n = DiscordNotifier(EventBus(), Stats(), lambda: cfg, transport=transport)
    return n, transport, cfg


def drain(n, expected, timeout=3.0):
    n.start()
    deadline = time.time() + timeout
    while time.time() < deadline and n.status().sent + n.status().failed < expected:
        time.sleep(0.02)
    n.stop()


# URL shape. A pasted Slack or bit.ly link must not be treated as a webhook.
assert valid_url(GOOD)
assert not valid_url("https://example.com/api/webhooks/1/abc")
assert not valid_url("https://discord.com/api/webhooks/short")
assert not valid_url("")

# A rejected webhook is not retried - 404 means revoked, retrying cannot help.
n, transport, _ = make([404])
n.submit("info", n._base("info", "t", "", "#4EC98A"), throttle=False)
drain(n, 1)
assert len(transport.calls) == 1, transport.calls
assert "rejected" in n.status().last_result, n.status().last_result

# 429 is honoured and the same payload is re-sent.
n, transport, _ = make([429, 204])
n.submit("info", n._base("info", "t", "", "#4EC98A"), throttle=False)
drain(n, 1, timeout=8.0)
assert len(transport.calls) == 2, transport.calls
assert n.status().sent == 1, n.status()

# Error storms are throttled, and what was held back is reported, not lost.
n, transport, _ = make()
assert n.submit("error", n._base("error", "boom", "", "#C2453F")) is True
for _ in range(20):
    n.submit("error", n._base("error", "boom", "", "#C2453F"))
assert n.status().suppressed == 20, n.status()
n._last_sent["error"] = -1e9                      # pretend the interval elapsed
n.submit("error", n._base("error", "boom", "", "#C2453F"))
drain(n, 2)
footer = transport.calls[-1]["embeds"][0]["footer"]["text"]
assert "20 more suppressed" in footer, footer

# The queue is bounded; overflow drops the oldest instead of growing forever.
n, transport, _ = make()
for i in range(200):
    n.submit("info", n._base("info", f"m{i}", "", "#4EC98A"), throttle=False)
assert n.status().dropped > 0, "queue never reported a drop"
assert n._queue.qsize() <= 64, n._queue.qsize()

# Nothing is attempted at all without a real webhook URL.
n, transport, cfg = make(url="")
n.on_event(Event(kind="error", message="boom"))
drain(n, 0, timeout=0.3)
assert transport.calls == [], transport.calls
assert n.send_test() == "no webhook URL set"
cfg.webhook.url = "https://example.com/hook"
assert "not a Discord webhook" in n.send_test()

# The session embed carries the numbers the dashboard shows.
n, transport, _ = make()
n._stats.record_fish(); n._stats.record_fish(); n._stats.record_fail()
names = {f["name"]: f["value"] for f in n.session_embed("x", "#4EC98A")["embeds"][0]["fields"]}
assert names["Caught"] == "2" and names["Escaped"] == "1", names
assert names["Hook rate"] == "67%", names

# A stored fruit rides with its banner screenshot, and the embed points at it.
n, transport, _ = make()
n.on_event(Event(kind="fruit", message="stored a devil fruit from slot 7",
                 data={"image": "captures/fruit/shot.png"}))
drain(n, 1)
assert transport.images == ["captures/fruit/shot.png"], transport.images
image_url = transport.calls[-1]["embeds"][0]["image"]["url"]
assert image_url == "attachment://shot.png", image_url

print("all notifier checks passed")
