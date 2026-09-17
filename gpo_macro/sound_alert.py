"""Optional rare-spawn sound alert.

Listens to what the speakers play - WASAPI loopback on Windows, the
PulseAudio/PipeWire monitor source on Linux, both found by the platform's
WindowSystem - keeps a rolling loudness baseline, and fires when a sustained
spike exceeds the sensitivity ratio: the Megalodon roar, a sea event.
Alerts with the built-in beeps (or a custom WAV) and an event the notifier
can relay to Discord.

Marked experimental: whether a loopback device exists depends on the
machine's audio setup.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Optional

import numpy as np

from . import platform
from .config import SoundConfig
from .stats import Event, EventBus

log = logging.getLogger("gpo.sound")

_BLOCK_SECONDS = 0.1
_BASELINE_BLOCKS = 150          # ~15 s of loudness history


class SoundListener(threading.Thread):
    def __init__(self, cfg: SoundConfig, bus: EventBus,
                 publish_override: Optional[Callable[[str, str], None]] = None,
                 system: Optional[platform.WindowSystem] = None):
        super().__init__(name="sound-listener", daemon=True)
        self.cfg = cfg
        self.bus = bus
        self.system = system or platform.default()
        self._publish_override = publish_override
        self._stop = threading.Event()
        self._last_alert = 0.0
        self._current_level: Optional[float] = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:
            self._publish_error(f"sound alert unavailable (sounddevice import failed): {exc}")
            return

        kwargs = self.system.loopback_stream_kwargs()
        if kwargs is None:
            self._publish_error("sound alert: no loopback capture device - needs WASAPI on "
                                "Windows, or a PulseAudio/PipeWire monitor source on Linux - "
                                "feature disabled for this session")
            return
        try:
            channels = kwargs.pop("channels", 2)
            stream = sd.InputStream(samplerate=44100, channels=channels, dtype="float32",
                                    blocksize=int(44100 * _BLOCK_SECONDS),
                                    callback=self._on_block, **kwargs)
            stream.start()
        except Exception as exc:
            self._publish_error(
                f"sound alert could not open loopback capture: {exc} - "
                f"feature disabled for this session")
            return

        log.info("sound listener active (sensitivity %.1f)", self.cfg.sensitivity)
        history: deque[float] = deque(maxlen=_BASELINE_BLOCKS)
        spike_start: Optional[float] = None
        while not self._stop.is_set() and stream.active:
            time.sleep(_BLOCK_SECONDS)
            level = self._current_level
            if level is None:
                continue
            history.append(level)
            if len(history) < _BASELINE_BLOCKS // 2:
                continue
            baseline = float(np.median(history))
            spike = baseline > 1e-5 and level > baseline * self.cfg.sensitivity
            now = time.monotonic()
            if spike:
                if spike_start is None:
                    spike_start = now
                elif now - spike_start >= self.cfg.trigger_hold \
                        and now - self._last_alert >= self.cfg.cooldown:
                    self._last_alert = now
                    spike_start = None
                    self._publish_alert(level, baseline)
            else:
                spike_start = None
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    # The sounddevice callback runs on PortAudio's thread - keep it tiny.
    def _on_block(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        self._current_level = float(np.sqrt(np.mean(indata ** 2)))

    def _publish_alert(self, level: float, baseline: float) -> None:
        message = (f"loud audio spike detected "
                   f"(level {level:.3f} vs baseline {baseline:.3f}) - check the game!")
        log.info(message)
        event = Event(kind="sound", message=message)
        self.bus.publish(event)
        self.system.play_alert(self.cfg.alert_wav)

    def _publish_error(self, message: str) -> None:
        log.warning(message)
        self.bus.publish(Event(kind="warn", message=message))
