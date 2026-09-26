"""Shrinks a clip so it fits Discord's upload limit (20 MB for free accounts since Aug 2026).

Picks a bitrate from the clip's length, lowers the resolution when that bitrate would look
blocky, re-encodes on the GPU, and retries a little smaller if the result still overshoots."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from Capture.ffmpeg import FFmpeg

log = logging.getLogger("gamecapture.discord")


class ClipTooLongError(ValueError):
    pass


class DiscordFitter:
    SAFETY = 0.95           # aim 5% under the limit (container overhead, rounding)
    AUDIO_KBPS = 96
    MIN_VIDEO_KBPS = 150    # below this it isn't worth watching
    # (minimum video kbps, max height): more bits -> keep more resolution
    LADDER = ((4000, 1080), (1800, 720), (700, 480), (0, 360))

    def __init__(self, ffmpeg: FFmpeg, encoder: str = "x264") -> None:
        self.ffmpeg = ffmpeg
        self.encoder = encoder

    @classmethod
    def limit_bytes(cls, limit_mb: float) -> int:
        return int(limit_mb * 1_000_000 * cls.SAFETY)  # decimal MB: the stricter reading

    def fits(self, path: Path, limit_mb: float) -> bool:
        return path.stat().st_size <= self.limit_bytes(limit_mb)

    @classmethod
    def plan(cls, duration: float, limit_mb: float, factor: float = 1.0) -> tuple[int, int]:
        """(video kbps, max height) for a clip of this length."""
        total_kbps = cls.limit_bytes(limit_mb) * 8 / 1000 / max(duration, 0.1) * factor
        video = int(total_kbps - cls.AUDIO_KBPS)
        if video < cls.MIN_VIDEO_KBPS:
            max_seconds = cls.limit_bytes(limit_mb) * 8 / 1000 / (cls.MIN_VIDEO_KBPS + cls.AUDIO_KBPS)
            raise ClipTooLongError(f"{duration:.0f} s is too long for {limit_mb:g} MB "
                                   f"(max about {max_seconds:.0f} s) - trim the clip or use a bigger limit")
        height = next(h for kbps, h in cls.LADDER if video >= kbps)
        return video, height

    def fit(self, src: Path, limit_mb: float, out: Path | None = None,
            progress: Callable[[float, str], None] | None = None) -> Path:
        """Returns a file that fits: `src` itself if it already does, else a new *_discord.mp4."""
        if self.fits(src, limit_mb):
            return src
        duration = self.ffmpeg.duration(src)
        if not duration:
            raise RuntimeError(f"Couldn't read the length of {src.name}")
        out = out or src.with_name(f"{src.stem}_discord.mp4")
        profile = FFmpeg.encoder(self.encoder)
        audio = self.ffmpeg.has_audio(src)
        for attempt, factor in enumerate((1.0, 0.85, 0.7), 1):
            kbps, height = self.plan(duration, limit_mb, factor)
            if progress:
                progress((attempt - 1) / 3, f"Compressing for Discord ({kbps} kbps, {height}p)...")
            args = ["-loglevel", "error", "-y", "-i", str(src), *FFmpeg.AV_MIX, "-map_chapters", "-1",
                    "-vf", f"scale=-2:'min({height},ih)'", *profile.file_bitrate_args(kbps)]
            args += ["-c:a", "aac", "-b:a", f"{self.AUDIO_KBPS}k"] if audio else ["-an"]
            args += ["-movflags", "+faststart", str(out)]
            r = self.ffmpeg.run(args, timeout=900)
            if r.returncode != 0 or not out.exists():
                raise RuntimeError(f"Compression failed: {r.stderr.strip()[-300:]}")
            if self.fits(out, limit_mb):
                log.info("Discord copy: %s (%.1f MB, %dp)", out.name, out.stat().st_size / 1e6, height)
                if progress:
                    progress(1.0, "Ready for Discord")
                return out
            log.info("%.1f MB is still over %g MB - trying smaller", out.stat().st_size / 1e6, limit_mb)
        raise RuntimeError(f"Couldn't get {src.name} under {limit_mb:g} MB")
