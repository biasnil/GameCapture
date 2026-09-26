"""Audio sources for isolated recording: the game, other apps (e.g. Discord), the microphone,
or everything you hear. Each source turns whatever its device delivers into 48 kHz stereo float32
and keeps it in a small buffer that the mixer drains on a fixed clock."""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

log = logging.getLogger("gamecapture.audio")

RATE = 48000      # everything is mixed at 48 kHz stereo
CHANNELS = 2


# ======================================================================== helpers

class FrameBuffer:
    """Thread-safe FIFO of stereo float32 frames. pull(n) always returns n frames (silence-padded).
    If a source runs ahead of the clock (buffer > max_s), old audio is dropped down to keep_s
    so latency can't creep up during a long match."""

    def __init__(self, max_s: float = 0.25, keep_s: float = 0.05) -> None:
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._lock = threading.Lock()
        self._max, self._keep = int(RATE * max_s), int(RATE * keep_s)

    def push(self, frames: np.ndarray) -> None:
        if frames.size == 0:
            return
        with self._lock:
            self._chunks.append(frames)
            self._frames += len(frames)
            if self._frames > self._max:
                self._drop(self._frames - self._keep)

    def _drop(self, n: int) -> None:
        while n > 0 and self._chunks:
            head = self._chunks[0]
            if len(head) <= n:
                self._chunks.pop(0)
                n -= len(head)
                self._frames -= len(head)
            else:
                self._chunks[0] = head[n:]
                self._frames -= n
                n = 0

    def pull(self, n: int) -> np.ndarray:
        out = np.zeros((n, CHANNELS), np.float32)
        with self._lock:
            filled = 0
            while filled < n and self._chunks:
                head = self._chunks[0]
                take = min(len(head), n - filled)
                out[filled:filled + take] = head[:take]
                if take == len(head):
                    self._chunks.pop(0)
                else:
                    self._chunks[0] = head[take:]
                filled += take
                self._frames -= take
        return out

    def __len__(self) -> int:
        return self._frames


class StereoConverter:
    """Any channel count / sample rate -> 48 kHz stereo, keeping resampling phase between chunks."""

    def __init__(self, rate: int, channels: int) -> None:
        self.rate, self.channels = int(rate), int(channels)
        self._step = self.rate / RATE
        self._pos = 0.0
        self._prev: np.ndarray | None = None

    def convert(self, samples: np.ndarray) -> np.ndarray:
        x = samples.reshape(-1, self.channels).astype(np.float32, copy=False)
        if self.channels == 1:
            x = np.repeat(x, 2, axis=1)
        elif self.channels > 2:
            x = x[:, :2]
        if self.rate == RATE:
            return x
        data = x if self._prev is None else np.vstack([self._prev, x])
        last = len(data) - 1
        if last < 1:
            self._prev = data
            return np.zeros((0, 2), np.float32)
        t = np.arange(self._pos, last, self._step)
        out = np.empty((len(t), 2), np.float32)
        idx = np.arange(len(data))
        for c in range(2):
            out[:, c] = np.interp(t, idx, data[:, c])
        next_pos = (t[-1] + self._step) if len(t) else self._pos
        self._pos = next_pos - last
        self._prev = data[-1:]
        return out


class ProcessFinder:
    """PIDs of the top-level processes with these exe names (children are captured with them)."""

    @staticmethod
    def top_pids(names) -> list[int]:
        import psutil
        wanted = {n.lower() for n in names}
        found = {}
        for p in psutil.process_iter(["name", "ppid"]):
            if (p.info.get("name") or "").lower() in wanted:
                found[p.pid] = p.info.get("ppid")
        return [pid for pid, ppid in found.items() if ppid not in found]


# ======================================================================== sources

class AudioSource:
    """Base: a named, volume-scaled buffer. Subclasses fill `self.buffer`."""

    def __init__(self, label: str, volume: float = 1.0) -> None:
        self.label = label
        self.volume = volume
        self.buffer = FrameBuffer()

    def start(self) -> None:
        """Open the device. Raise if it can't be used at all."""

    def stop(self) -> None:
        pass

    def maintain(self) -> None:
        """Called every few seconds from the mixer thread (e.g. an app that opened late)."""

    def pull(self, n: int) -> np.ndarray:
        frames = self.buffer.pull(n)
        return frames * self.volume if self.volume != 1.0 else frames


class _PyAudioSource(AudioSource):
    """Shared by the microphone and 'everything I hear' (WASAPI loopback) via PyAudioWPatch."""

    def _find_device(self, pa) -> dict:
        raise NotImplementedError

    def start(self) -> None:
        import pyaudiowpatch as pyaudio
        self._pa = pyaudio.PyAudio()
        try:
            dev = self._find_device(pa=self._pa)
            channels = max(1, min(2, int(dev["maxInputChannels"])))
            rate = int(dev["defaultSampleRate"])
            self._conv = StereoConverter(rate, channels)

            def on_audio(in_data, frame_count, time_info, status):
                self.buffer.push(self._conv.convert(np.frombuffer(in_data, np.float32)))
                return None, pyaudio.paContinue
            self._stream = self._pa.open(format=pyaudio.paFloat32, channels=channels, rate=rate, input=True,
                                         input_device_index=dev["index"], frames_per_buffer=rate // 100,
                                         stream_callback=on_audio)
            log.info("Audio source %s: %s (%d Hz, %d ch)", self.label, dev["name"], rate, channels)
        except Exception:
            self._pa.terminate()
            raise

    def stop(self) -> None:
        try:
            self._stream.stop_stream()
            self._stream.close()
        except Exception:
            pass
        try:
            self._pa.terminate()
        except Exception:
            pass


class MicrophoneSource(_PyAudioSource):
    def __init__(self, volume: float = 1.0) -> None:
        super().__init__("Microphone", volume)

    def _find_device(self, pa) -> dict:
        return pa.get_default_input_device_info()


class SystemAudioSource(_PyAudioSource):
    """Everything you hear on the default output device."""

    def __init__(self, volume: float = 1.0) -> None:
        super().__init__("Everything", volume)

    def _find_device(self, pa) -> dict:
        import pyaudiowpatch as pyaudio
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if speakers.get("isLoopbackDevice"):
            return speakers
        for dev in pa.get_loopback_device_info_generator():
            if speakers["name"] in dev["name"]:
                return dev
        raise RuntimeError(f"No loopback device for '{speakers['name']}'")


class AppAudioSource(AudioSource):
    """Only the sound of one app (and its child processes), via Windows' per-app capture (proc-tap).
    Needs Windows 10 2004+. Attaches late if the app opens after recording started."""
    RESCAN_S = 5.0

    def __init__(self, label: str, exe_names, volume: float = 1.0) -> None:
        super().__init__(label, volume)
        self.exe_names = tuple(exe_names)
        self._taps: dict[int, tuple[object, FrameBuffer]] = {}
        self._lock = threading.Lock()
        self._last_scan = 0.0

    def start(self) -> None:
        import proctap  # noqa: F401  - fail here (not later) if the package is missing
        self._attach()
        if not self._taps:
            log.info("Audio source %s: %s isn't running yet - will attach when it starts",
                     self.label, ", ".join(self.exe_names))

    def _attach(self) -> None:
        from proctap import ProcessAudioCapture
        self._last_scan = time.monotonic()
        pids = set(ProcessFinder.top_pids(self.exe_names))
        with self._lock:
            for pid in list(self._taps):
                if pid not in pids:                   # app closed
                    self._close(self._taps.pop(pid)[0])
            for pid in pids - set(self._taps):
                buf = FrameBuffer()
                try:
                    tap = ProcessAudioCapture(
                        pid, on_data=lambda data, _n, b=buf: b.push(np.frombuffer(data, np.float32).reshape(-1, 2)))
                    tap.start()
                except Exception as exc:
                    log.warning("Audio source %s: can't capture pid %d (%s)", self.label, pid, exc)
                    continue
                self._taps[pid] = (tap, buf)
                log.info("Audio source %s: capturing pid %d", self.label, pid)

    def maintain(self) -> None:
        if time.monotonic() - self._last_scan >= self.RESCAN_S:
            try:
                self._attach()
            except Exception:
                log.exception("Audio source %s: rescan failed", self.label)

    @staticmethod
    def _close(tap) -> None:
        try:
            tap.close()
        except Exception:
            pass

    def pull(self, n: int) -> np.ndarray:
        with self._lock:
            bufs = [b for _, b in self._taps.values()]
        out = np.zeros((n, CHANNELS), np.float32)
        for b in bufs:
            out += b.pull(n)
        return out * self.volume if self.volume != 1.0 else out

    def stop(self) -> None:
        with self._lock:
            for tap, _ in self._taps.values():
                self._close(tap)
            self._taps.clear()
