"""Mixes several audio sources and streams them to ffmpeg: track 1 = the mix, plus (optionally)
one track per source so you can rebalance or mute them later in an editor.

Like the old single-source pump it runs on a fixed clock: exactly 48000 frames/s go out whether
or not a source delivered anything, so silence never stalls ffmpeg or desyncs the audio."""
from __future__ import annotations

import logging
import socket
import threading
import time

import numpy as np

from Capture.audio_sources import CHANNELS, RATE, AudioSource

log = logging.getLogger("gamecapture.audio")


class AudioMixer:
    rate, channels = RATE, CHANNELS
    MAINTAIN_S = 1.0

    def __init__(self, sources: list[AudioSource], separate_tracks: bool = False) -> None:
        self.sources = sources
        self.separate_tracks = separate_tracks and len(sources) > 1
        self._socks: list[socket.socket] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def track_names(self) -> list[str]:
        return ["Mix"] + ([s.label for s in self.sources] if self.separate_tracks else [])

    @property
    def name(self) -> str:
        return " + ".join(s.label for s in self.sources)

    def open_sources(self) -> None:
        """Start every source; ones that can't start are dropped (with a warning). Raise if none work."""
        working = []
        for src in self.sources:
            try:
                src.start()
                working.append(src)
            except Exception as exc:
                log.warning("Audio source %s unavailable: %s", src.label, exc)
        if not working:
            raise RuntimeError("no audio source could be opened")
        self.sources = working
        if len(working) < 2:
            self.separate_tracks = False

    def start_many(self, ports: list[int], alive=lambda: True, connect_timeout: float = 10.0) -> None:
        """ffmpeg opens its inputs one after another and reads a little from each before opening
        the next - so audio must already be flowing into input 1 while we connect to input 2.
        (Waiting for every connection first deadlocks: ffmpeg waits for data, we wait for ffmpeg.)"""
        self._socks = [None] * len(ports)
        for i, port in enumerate(ports):
            self._socks[i] = self._connect(port, alive, connect_timeout)
            if i == 0:
                self._thread = threading.Thread(target=self._pump, name="audio-mixer", daemon=True)
                self._thread.start()

    @staticmethod
    def _connect(port: int, alive, timeout: float) -> socket.socket:
        deadline = time.monotonic() + timeout
        while True:
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                if not alive():
                    raise RuntimeError("ffmpeg exited before its audio input opened")
                if time.monotonic() > deadline:
                    raise RuntimeError("ffmpeg never opened its audio input")
                time.sleep(0.02)
        sock.settimeout(2.0)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock

    @staticmethod
    def _pcm(frames: np.ndarray) -> bytes:
        return (np.clip(frames, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

    def mix(self, n: int) -> tuple[bytes, list[bytes]]:
        """n frames of the mix (+ each source on its own when separate tracks are on)."""
        parts = [s.pull(n) for s in self.sources]
        total = np.sum(parts, axis=0) if parts else np.zeros((n, CHANNELS), np.float32)
        return self._pcm(total), ([self._pcm(p) for p in parts] if self.separate_tracks else [])

    def _pump(self) -> None:
        min_frames = RATE // 200  # >= 5 ms per send
        start, sent, last_maintain = time.monotonic(), 0, 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_maintain >= self.MAINTAIN_S:
                last_maintain = now
                for s in self.sources:
                    s.maintain()
            due = int((now - start) * RATE) - sent
            if due < min_frames:
                time.sleep(0.004)
                continue
            mix, tracks = self.mix(due)
            try:
                for sock, data in zip(list(self._socks), [mix, *tracks]):
                    if sock is not None:   # later tracks join as soon as ffmpeg opens them
                        sock.sendall(data)
            except OSError:
                break  # ffmpeg closed its inputs (normal on stop)
            sent += due

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        for s in self.sources:
            try:
                s.stop()
            except Exception:
                pass
        for sock in self._socks:
            try:
                if sock is not None:
                    sock.close()
            except OSError:
                pass
