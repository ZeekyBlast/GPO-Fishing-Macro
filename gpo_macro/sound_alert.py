"""Optional rare-spawn sound alert.

Listens to the system output via WASAPI loopback (what you hear), maintains a
rolling loudness baseline, and fires when a sustained spike exceeds the
sensitivity ratio - e.g. the Megalodon/sea-event roar. Alerts via built-in
beeps (or a custom WAV) and an event the notifier can relay to Discord.

Marked experimental: loopback availability depends on the active audio device.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Optional

import numpy as np

from .config import SoundConfig
from .stats import Event, EventBus

log = logging.getLogger("gpo.sound")

_BLOCK_SECONDS = 0.1
_BASELINE_BLOCKS = 150          # ~15 s of loudness history


def _play_alert(wav_path: str) -> None:
    import winsound
    if wav_path:
        try:
            winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        except Exception:
            log.warning("could not play %s - falling back to beeps", wav_path)
    for freq, duration in ((880, 250), (1100, 250), (880, 250), (1400, 400)):
        try:
            winsound.Beep(freq, duration)
        except Exception:
            break


class SoundListener(threading.Thread):
    def __init__(self, cfg: SoundConfig, bus: EventBus,
                 publish_override: Optional[Callable[[str, str], None]] = None):
        super().__init__(name="sound-listener", daemon=True)
        self.cfg = cfg
        self.bus = bus
        self._publish_override = publish_override
        self._stop = threading.Event()
        self._last_alert = 0.0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:
            self._publish_error(f"sound alert unavailable (sounddevice import failed): {exc}")
            return

        try:
            extra = sd.WasapiSettings(loopback=True)
            stream = sd.InputStream(samplerate=44100, channels=2, dtype="float32",
                                    blocksize=int(44100 * _BLOCK_SECONDS),
                                    extra_settings=extra, callback=self._on_block)
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
            level = getattr(self, "_current_level", None)
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
        _play_alert(self.cfg.alert_wav)

    def _publish_error(self, message: str) -> None:
        log.warning(message)
        self.bus.publish(Event(kind="warn", message=message))
