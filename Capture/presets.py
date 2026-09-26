"""Video quality presets (Low / Medium / High / Ultra) on top of the individual capture settings."""
from __future__ import annotations

from dataclasses import dataclass

from Core.config import CaptureSettings


@dataclass(frozen=True)
class VideoPreset:
    key: str
    title: str
    subtitle: str
    resolution: str
    fps: int
    quality: int

    def apply(self, cap: CaptureSettings) -> None:
        cap.resolution, cap.fps, cap.rate_control, cap.quality = self.resolution, self.fps, "quality", self.quality

    def matches(self, cap: CaptureSettings) -> bool:
        return (cap.resolution, cap.fps, cap.rate_control, cap.quality) == \
               (self.resolution, self.fps, "quality", self.quality)


class VideoPresets:
    ALL = (
        VideoPreset("low", "Low", "Light - 720p 30 fps", "720p", 30, 26),
        VideoPreset("medium", "Medium", "Balanced - 1080p 30 fps", "1080p", 30, 23),
        VideoPreset("high", "High", "Smooth - 1080p 60 fps", "1080p", 60, 21),
        VideoPreset("ultra", "Ultra", "Best - native resolution 60 fps", "native", 60, 18),
    )
    CUSTOM = "custom"

    @classmethod
    def get(cls, key: str) -> VideoPreset | None:
        return next((p for p in cls.ALL if p.key == key), None)

    @classmethod
    def current(cls, cap: CaptureSettings) -> str:
        """Which preset the settings match, or 'custom'."""
        return next((p.key for p in cls.ALL if p.matches(cap)), cls.CUSTOM)
