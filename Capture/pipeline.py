"""Decides how captured frames reach the encoder: native size, or resized on the GPU / CPU.

Resizing is probed once per (monitor, size) with a real 3-frame capture, so the choice is
known to work on this PC: GPU scalers first (zero CPU cost), CPU scaling only as a fallback."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from Capture.ffmpeg import FFmpeg, RateControl
from Core.config import CaptureSettings

log = logging.getLogger("gamecapture.video")


@dataclass(frozen=True)
class VideoPlan:
    filter: str | None
    size: tuple[int, int] | None        # output size (None = unknown native size)
    scaling: str                         # "native" | "gpu" | "cpu"
    native: tuple[int, int] | None

    @property
    def description(self) -> str:
        size = f"{self.size[0]}x{self.size[1]}" if self.size else "native resolution"
        if self.scaling == "native":
            return f"Recording {size} (no resizing)"
        src = f"{self.native[0]}x{self.native[1]}" if self.native else "screen"
        where = "on the GPU" if self.scaling == "gpu" else "on the CPU (costs some in-game FPS)"
        return f"Recording {size}, resized from {src} {where}"


class VideoPipeline:
    HEIGHTS = {"native": None, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480}

    def __init__(self, ffmpeg: FFmpeg, encoder: str) -> None:
        self.ffmpeg = ffmpeg
        self.profile = FFmpeg.encoder(encoder)
        self._native: dict[int, tuple[int, int] | None] = {}
        self._plans: dict[tuple, VideoPlan] = {}
        self._lock = threading.Lock()
        self.last_plan: VideoPlan | None = None

    def native_size(self, monitor: int) -> tuple[int, int] | None:
        if monitor not in self._native:
            self._native[monitor] = self.ffmpeg.screen_size(monitor)
        return self._native[monitor]

    def plan(self, cap: CaptureSettings) -> VideoPlan:
        with self._lock:
            native = self.native_size(cap.monitor)
            height = self.HEIGHTS.get(cap.resolution)
            if height is None or (native is not None and height >= native[1]):
                plan = VideoPlan(self.profile.native_filter, native, "native", native)  # never upscale
            else:
                aspect = native[0] / native[1] if native else 16 / 9
                width = int(round(height * aspect / 2)) * 2
                key = (cap.monitor, width, height)
                if key not in self._plans:
                    self._plans[key] = self._probe_scaler(cap.monitor, width, height, native)
                plan = self._plans[key]
            if plan != self.last_plan:
                log.info("Video: %s", plan.description)
            self.last_plan = plan
            return plan

    def _probe_scaler(self, monitor: int, width: int, height: int, native) -> VideoPlan:
        for where, vf in self.profile.scalers(width, height):
            if self.ffmpeg.probe_filter(self.profile.name, monitor, vf):
                if where == "cpu":
                    log.warning("No GPU resizer available for %s - resizing on the CPU", self.profile.name)
                return VideoPlan(vf, (width, height), where, native)
        log.warning("Resizing to %dx%d failed - recording at native resolution", width, height)
        return VideoPlan(self.profile.native_filter, native, "native", native)

    def video_args(self, cap: CaptureSettings) -> list[str]:
        plan = self.plan(cap)
        rc = RateControl(cap.rate_control, cap.quality, cap.bitrate_kbps)
        return self.profile.capture_args(cap.fps, rc, plan.filter)
