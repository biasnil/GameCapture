"""Highlights mode: turns a full match recording into a short video of just your highlights.

Uses ffmpeg's concat demuxer with stream copy - no re-encoding, so it takes seconds and costs
nothing in game. Cuts land on keyframes (every 2 s), which the clip padding covers."""
from __future__ import annotations

import copy
import logging
import tempfile
from pathlib import Path

from Capture.ffmpeg import FFmpeg
from Core.clips import Segment
from Core.config import ClipSettings
from Core.sidecar import Sidecar

log = logging.getLogger("gamecapture.condense")


class HighlightCondenser:
    TYPES = {"kill", "multikill", "objective", "ace", "first_blood", "bookmark"}  # same as the pre-ticked export set

    def __init__(self, ffmpeg: FFmpeg, clips: ClipSettings) -> None:
        self.ffmpeg = ffmpeg
        self.clips = clips

    def segments(self, markers: list[dict]) -> list[Segment]:
        mine = [m for m in markers if m.get("involves_me") and m.get("type") in self.TYPES]
        return Segment.from_markers(mine, self.clips.pre_seconds, self.clips.post_seconds)

    @staticmethod
    def remap(markers: list[dict], segments: list[Segment]) -> list[dict]:
        """Move markers from full-match time to highlights-video time; drop ones that were cut."""
        out, offset = [], 0.0
        for seg in segments:
            for m in markers:
                if seg.start <= m["video_time"] <= seg.end:
                    moved = copy.deepcopy(m)
                    moved["video_time"] = round(offset + m["video_time"] - seg.start, 2)
                    out.append(moved)
            offset += seg.duration
        return out

    def condense(self, video: Path, meta: dict) -> Path | None:
        """Returns the highlights video, or None if the match had no highlights.
        Raises RuntimeError if ffmpeg fails (the caller then keeps the full recording)."""
        segments = self.segments(meta.get("markers", []))
        if not segments:
            return None
        out = video.with_name(f"{video.stem}_highlights.mp4")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            src = str(video).replace("\\", "/").replace("'", "'\\''")
            for seg in segments:
                f.write(f"file '{src}'\ninpoint {seg.start:.3f}\noutpoint {seg.end:.3f}\n")
            listing = Path(f.name)
        try:
            r = self.ffmpeg.run(["-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                                 *FFmpeg.AV_ONLY, "-map_chapters", "-1", "-c", "copy",
                                 "-movflags", "+faststart", str(out)], timeout=600)
        finally:
            listing.unlink(missing_ok=True)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(f"Highlights video failed: {r.stderr.strip()[-300:]}")

        new_meta = copy.deepcopy(meta)
        new_meta["video"] = out.name
        new_meta["markers"] = self.remap(meta.get("markers", []), segments)
        new_meta["game"] = {**meta.get("game", {}), "highlights_only": True, "condensed_from": video.name,
                            "full_length_s": round(segments[-1].end, 1)}
        Sidecar.write(out, new_meta)
        log.info("Highlights video: %d moments, %.0f s (from %s)", len(segments),
                 sum(s.duration for s in segments), video.name)
        return out
