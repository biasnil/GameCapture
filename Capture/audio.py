"""System audio ("what you hear") via WASAPI loopback, streamed to ffmpeg over local TCP.

WASAPI loopback delivers *nothing* while no sound is playing, which would desync audio
and stall ffmpeg's muxer. So a clock-driven pump sends exactly `rate` frames per second,
using captured audio when it's there and silence when it isn't."""
from __future__ import annotations

import logging
import socket
import threading
import time

import pyaudiowpatch as pyaudio

log = logging.getLogger("gamecapture.audio")


class LoopbackAudio:
    MAX_BUFFER_S = 0.25   # if capture runs ahead of the clock, trim back...
    TRIM_TO_S = 0.05      # ...to this much latency

    def __init__(self) -> None:
        self._pa = pyaudio.PyAudio()
        try:
            self.device = self._find_loopback()
        except Exception:
            self._pa.terminate()
            raise
        self.rate = int(self.device["defaultSampleRate"])
        self.channels = int(self.device["maxInputChannels"])
        self._frame_bytes = 2 * self.channels  # int16
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._stream = None
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    @property
    def name(self) -> str:
        return self.device["name"]

    def _find_loopback(self) -> dict:
        wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if speakers.get("isLoopbackDevice"):
            return speakers
        for dev in self._pa.get_loopback_device_info_generator():
            if speakers["name"] in dev["name"]:
                return dev
        raise RuntimeError(f"No loopback device found for '{speakers['name']}'")

    def start(self, port: int, connect_timeout: float = 10.0, alive=lambda: True) -> None:
        """alive(): is ffmpeg still running? If it dies we stop waiting straight away."""
        deadline = time.monotonic() + connect_timeout
        while True:  # ffmpeg opens its listening socket a moment after launch
            try:
                self._sock = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                if not alive():
                    raise RuntimeError("ffmpeg exited before its audio input opened")
                if time.monotonic() > deadline:
                    raise RuntimeError("ffmpeg never opened its audio input")
                time.sleep(0.05)
        self._sock.settimeout(2.0)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._stream = self._pa.open(
            format=pyaudio.paInt16, channels=self.channels, rate=self.rate, input=True,
            input_device_index=self.device["index"], frames_per_buffer=self.rate // 100,
            stream_callback=self._on_audio,
        )
        self._thread = threading.Thread(target=self._pump, name="audio-pump", daemon=True)
        self._thread.start()

    def _on_audio(self, in_data, frame_count, time_info, status):
        with self._lock:
            self._buf.extend(in_data)
        return None, pyaudio.paContinue

    def _pump(self) -> None:
        fb = self._frame_bytes
        max_bytes = int(self.rate * self.MAX_BUFFER_S) * fb
        keep_bytes = int(self.rate * self.TRIM_TO_S) * fb
        min_frames = self.rate // 200  # send in >= 5 ms chunks
        start, sent = time.monotonic(), 0
        while not self._stop.is_set():
            due = int((time.monotonic() - start) * self.rate) - sent
            if due < min_frames:
                time.sleep(0.004)
                continue
            need = due * fb
            with self._lock:
                if len(self._buf) > max_bytes:
                    del self._buf[: len(self._buf) - keep_bytes]
                chunk = bytes(self._buf[:need])
                del self._buf[:need]
            if len(chunk) < need:
                chunk += bytes(need - len(chunk))  # silence
            try:
                self._sock.sendall(chunk)
            except OSError:
                break  # ffmpeg closed its input (normal on stop)
            sent += due

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._pa.terminate()
