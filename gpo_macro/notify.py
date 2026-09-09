"""Discord webhook notifications.

Posting runs on its own worker thread so a slow or dead webhook can never
block the bot or the GUI. Three things the naive version got wrong and this
one handles:

- **Rate limits.** Discord answers 429 with a `retry_after`; ignoring it gets
  the webhook throttled harder. The worker sleeps for exactly that long and
  re-sends the same payload.
- **Error storms.** A macro fault repeats every loop. Each kind of message has
  a minimum interval, and whatever was suppressed in between is reported as a
  count on the next one that goes out, so nothing is silently lost.
- **Backpressure.** The queue is bounded. If the network is down for an hour
  the oldest messages are dropped, not accumulated until memory runs out.

The webhook URL is a credential: it is never logged, never put in an embed,
and only ever compared against a prefix.
"""

from __future__ import annotations

import json
import logging
import ssl
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from requests.adapters import HTTPAdapter

from .config import WebhookConfig
from .stats import Event, EventBus, Stats, format_uptime
from . import theme

log = logging.getLogger("gpo.notify")

WEBHOOK_PREFIXES = ("https://discord.com/api/webhooks/",
                    "https://discordapp.com/api/webhooks/",
                    "https://ptb.discord.com/api/webhooks/",
                    "https://canary.discord.com/api/webhooks/")

QUEUE_LIMIT = 64
MAX_ATTEMPTS = 3

# How often a given kind may post. Catches and milestones are deliberate and
# rare; faults repeat every loop and need a leash.
MIN_INTERVAL = {"error": 60.0, "timeout": 120.0, "warn": 60.0}


class _OSTrustAdapter(HTTPAdapter):
    """Verify TLS against the operating system's certificate store.

    requests defaults to certifi's bundle, which does not know about roots
    installed locally. Antivirus HTTPS scanning (Norton, Kaspersky, ESET and
    friends) re-signs every connection with exactly such a root: browsers
    accept it because it is in the Windows store, and requests rejects it with
    "unable to get local issuer certificate". Trusting what the OS trusts is
    both the fix and the correct policy for a desktop app - verification stays
    fully on, only the trust anchors change.
    """

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = ssl.create_default_context()
        return super().init_poolmanager(*args, **kwargs)


def _session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", _OSTrustAdapter())
    return session


def _color(hex_color: str) -> int:
    return int(hex_color.lstrip("#"), 16)


def valid_url(url: str) -> bool:
    """True if this looks like a Discord webhook URL. Cheap shape check only."""
    return url.startswith(WEBHOOK_PREFIXES) and len(url) > 60


@dataclass
class NotifierStatus:
    """What the Settings tab shows about the webhook. Read-only snapshot."""

    configured: bool = False
    sent: int = 0
    failed: int = 0
    dropped: int = 0
    suppressed: int = 0
    queued: int = 0
    last_result: str = "nothing sent yet"
    last_attempt: float = 0.0


@dataclass
class _Message:
    kind: str
    payload: dict[str, Any]
    image_path: str = ""
    attempts: int = 0
    created: float = field(default_factory=time.monotonic)


class DiscordNotifier:
    def __init__(self, bus: EventBus, stats: Stats, get_config,
                 transport: Optional[Callable[..., int]] = None):
        """`transport(url, payload) -> http status` is injectable so the tests
        exercise the retry and throttle logic without touching the network."""
        self._bus = bus
        self._stats = stats
        self._get_config = get_config          # -> AppConfig, read live
        self._transport = transport or self._http_post
        self._queue: "queue.Queue[Optional[_Message]]" = queue.Queue(maxsize=QUEUE_LIMIT)
        self._thread: Optional[threading.Thread] = None
        self._periodic_stop = threading.Event()
        self._periodic_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._status = NotifierStatus()
        self._last_sent: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}
        self._session = _session()

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self._bus.subscribe(self.on_event)
        self._thread = threading.Thread(target=self._run, name="webhook", daemon=True)
        self._thread.start()
        self.restart_periodic()

    def stop(self) -> None:
        self._periodic_stop.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=3.0)

    def restart_periodic(self) -> None:
        """Called after the settings change, so editing the interval takes
        effect without restarting the app."""
        self._periodic_stop.set()
        if self._periodic_thread and self._periodic_thread.is_alive():
            self._periodic_thread.join(timeout=1.0)
        self._periodic_stop = threading.Event()
        minutes = self._get_config().webhook.periodic_stats_minutes
        if minutes > 0:
            self._periodic_thread = threading.Thread(
                target=self._periodic_loop, args=(minutes, self._periodic_stop),
                name="webhook-periodic", daemon=True)
            self._periodic_thread.start()

    # --------------------------------------------------------------- status

    def status(self) -> NotifierStatus:
        cfg = self._get_config().webhook
        with self._lock:
            snap = NotifierStatus(**vars(self._status))
        snap.configured = valid_url(cfg.url)
        snap.queued = self._queue.qsize()
        if not cfg.url:
            snap.last_result = "no webhook URL set"
        elif not snap.configured:
            snap.last_result = "URL is not a Discord webhook"
        return snap

    def _note(self, **fields) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self._status, key, value)

    def _bump(self, field_name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self._status, field_name, getattr(self._status, field_name) + amount)

    # ------------------------------------------------------------- plumbing

    def _http_post(self, url: str, payload: dict[str, Any], image_path: str = "") -> int:
        """Plain JSON, or multipart when an image rides along.

        A screenshot beats reading the banner with OCR: the fruit's name is
        drawn right there, and a picture cannot be misparsed."""
        if not image_path:
            return self._session.post(url, json=payload, timeout=10).status_code
        path = Path(image_path)
        if not path.exists() or path.stat().st_size > 8_000_000:
            return self._session.post(url, json=payload, timeout=10).status_code
        with path.open("rb") as handle:
            response = self._session.post(
                url, timeout=20,
                data={"payload_json": json.dumps(payload)},
                files={"files[0]": (path.name, handle, "image/png")})
        return response.status_code

    def submit(self, kind: str, payload: dict[str, Any], throttle: bool = True,
               image_path: str = "") -> bool:
        """Queue a message. False if it was throttled or the queue was full."""
        if throttle:
            interval = MIN_INTERVAL.get(kind, 0.0)
            now = time.monotonic()
            if interval and now - self._last_sent.get(kind, -1e9) < interval:
                self._suppressed[kind] = self._suppressed.get(kind, 0) + 1
                self._bump("suppressed")
                return False
            self._last_sent[kind] = now
            held = self._suppressed.pop(kind, 0)
            if held:
                payload = _add_footnote(payload, f"+{held} more suppressed in the last "
                                                 f"{interval:.0f}s")
        try:
            self._queue.put_nowait(_Message(kind, payload, image_path))
        except queue.Full:
            # Drop the oldest rather than the newest: recent state is more
            # useful than a backlog from when the network first went away.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(_Message(kind, payload, image_path))
            except (queue.Empty, queue.Full):
                pass
            self._bump("dropped")
            return False
        return True

    def _run(self) -> None:
        while True:
            message = self._queue.get()
            if message is None:
                break
            self._deliver(message)

    def _deliver(self, message: _Message) -> None:
        url = self._get_config().webhook.url
        if not valid_url(url):
            return
        while message.attempts < MAX_ATTEMPTS:
            message.attempts += 1
            try:
                status = self._transport(url, message.payload, message.image_path)
            except requests.RequestException as exc:
                # Never log the URL - the exception text can carry it.
                self._note(last_result=f"network error ({type(exc).__name__})",
                           last_attempt=time.time())
                self._bump("failed")
                time.sleep(min(8.0, 2.0 ** message.attempts))
                continue
            if status == 429:
                time.sleep(2.0 * message.attempts)
                continue
            if 200 <= status < 300:
                self._note(last_result=f"delivered ({status})", last_attempt=time.time())
                self._bump("sent")
                return
            if 400 <= status < 500:
                # Bad payload or a revoked webhook - retrying cannot help.
                self._note(last_result=f"rejected by Discord ({status})",
                           last_attempt=time.time())
                self._bump("failed")
                return
            time.sleep(min(8.0, 2.0 ** message.attempts))
        self._note(last_result=f"gave up after {MAX_ATTEMPTS} attempts",
                   last_attempt=time.time())
        self._bump("failed")

    def _periodic_loop(self, minutes: int, stop: threading.Event) -> None:
        while not stop.wait(minutes * 60):
            snap = self._stats.snapshot()
            if snap["fish_total"] == 0 and snap["fails"] == 0:
                continue  # nothing has happened; stay quiet
            self.submit("stats", self.session_embed("Session update", theme.GREEN),
                        throttle=False)

    # -------------------------------------------------------------- content

    def _base(self, kind: str, title: str, description: str,
              colour: str, fields: Optional[list[dict]] = None) -> dict[str, Any]:
        cfg: WebhookConfig = self._get_config().webhook
        embed: dict[str, Any] = {
            "title": title,
            "description": description,
            "color": _color(colour),
            "footer": {"text": f"GPO Fishing Macro · up {format_uptime(self._stats.snapshot()['uptime_seconds'])}"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        }
        if fields:
            embed["fields"] = fields
        payload: dict[str, Any] = {"username": "GPO Fishing Macro", "embeds": [embed]}
        want_ping = ((kind in ("fruit", "sound") and cfg.ping_on_fruit)
                     or (kind == "error" and cfg.ping_on_error))
        if want_ping and cfg.user_id.isdigit():
            payload["content"] = f"<@{cfg.user_id}>"
        return payload

    def session_embed(self, title: str, colour: str) -> dict[str, Any]:
        snap = self._stats.snapshot()
        landed = snap["fish_total"] + snap["fails"]
        hook_rate = f"{100.0 * snap['fish_total'] / landed:.0f}%" if landed else "-"
        return self._base("stats", title, "", colour, fields=[
            {"name": "Caught", "value": str(snap["fish_total"]), "inline": True},
            {"name": "Per hour", "value": f"{snap['fish_per_hour']:.0f}", "inline": True},
            {"name": "Hook rate", "value": hook_rate, "inline": True},
            {"name": "Escaped", "value": str(snap["fails"]), "inline": True},
            {"name": "Recast timeouts", "value": str(snap["recast_timeouts"]), "inline": True},
            {"name": "Session", "value": format_uptime(snap["uptime_seconds"]), "inline": True},
        ])

    def send_test(self) -> str:
        """Queue a test message. Returns why it will not be sent, or ''."""
        cfg = self._get_config().webhook
        if not cfg.url:
            return "no webhook URL set"
        if not valid_url(cfg.url):
            return "that URL is not a Discord webhook (expected discord.com/api/webhooks/...)"
        self.submit("info", self._base(
            "info", "Webhook test",
            "If you can read this, the macro can reach Discord.", theme.GREEN),
            throttle=False)
        return ""

    # ----------------------------------------------------------- bus bridge

    def on_event(self, event: Event) -> None:
        cfg: WebhookConfig = self._get_config().webhook
        if not valid_url(cfg.url):
            return
        kind = event.kind
        if kind == "fruit":
            # The banner screenshot names the fruit, so the embed does not have to.
            image = str(event.data.get("image") or "")
            payload = self._base("fruit", "Devil fruit stored", event.message, theme.AMBER)
            if image:
                payload["embeds"][0]["image"] = {"url": f"attachment://{Path(image).name}"}
            self.submit("fruit", payload, image_path=image)
        elif kind == "sound":
            self.submit("sound", self._base("sound", "Rare spawn sound", event.message,
                                            theme.AMBER))
        elif kind == "error":
            self.submit("error", self._base("error", "Macro error", event.message, theme.RED))
        elif kind == "timeout" and cfg.ping_on_error:
            self.submit("timeout", self._base("timeout", "Recast timeout", event.message,
                                              theme.AMBER))
        elif kind == "milestone" and cfg.log_milestones:
            self.submit("milestone", self.session_embed(event.message, theme.GREEN),
                        throttle=False)
        elif kind == "info" and event.message == "bot started":
            self.submit("info", self._base("info", "Macro started", "", theme.GREEN),
                        throttle=False)
        elif kind == "info" and event.message == "bot stopped":
            self.submit("info", self.session_embed("Macro stopped", theme.MUTED),
                        throttle=False)


def _add_footnote(payload: dict[str, Any], note: str) -> dict[str, Any]:
    """Append a line to the first embed's footer without mutating the original."""
    copy = {**payload, "embeds": [dict(e) for e in payload.get("embeds", [])]}
    if copy["embeds"]:
        footer = dict(copy["embeds"][0].get("footer", {}))
        footer["text"] = f"{footer.get('text', '')} · {note}".strip(" ·")
        copy["embeds"][0]["footer"] = footer
    return copy
