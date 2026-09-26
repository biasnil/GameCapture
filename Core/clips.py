"""Turns highlight markers into clips: separate files (instant stream copy) or one reel (re-encoded)."""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from Capture.ffmpeg import FFmpeg
from Core.formatting import Format

log = logging.getLogger("gamecapture.clips")

Progress = Callable[[float, str], None]  # (fraction 0-1, message)


@dataclass
class Segment:
    start: float
    end: float
    label: str

    # Your moments that count as highlights by default (Sessions pre-ticks these, the editor cuts them)
    HIGHLIGHT_TYPES = frozenset({"kill", "multikill", "objective", "ace", "first_blood", "bookmark"})

    @property
    def duration(self) -> float:
        return self.end - self.start

    @classmethod
    def from_markers(cls, markers: list[dict], pre: float, post: float,
                     duration: float | None = None) -> list["Segment"]:
        """One window per marker; overlapping windows (kill -> double kill) merge into one."""
        segments: list[Segment] = []
        for m in sorted(markers, key=lambda m: m["video_time"]):
            t = float(m["video_time"])
            start, end = max(0.0, t - pre), t + post
            if duration:
                end = min(end, duration)
            if end <= start:
                continue
            if segments and start <= segments[-1].end:
                last = segments[-1]
                last.end = max(last.end, end)
                if m["label"] not in last.label:
                    last.label = f"{last.label} + {m['label']}"
            else:
                segments.append(cls(start, end, m["label"]))
        return segments


    MOMENT_GAP = 4.0  # s: events this close are one moment (kill -> double kill), anything further is its own

    @classmethod
    def per_moment(cls, markers: list[dict], pre: float, post: float, duration: float | None = None,
                   cuts: list[float] | tuple = ()) -> list["Segment"]:
        """One clip per highlight moment, with a clear edge between them - never merged into one long clip.

        Where two padded windows would overlap, the two clips meet at a real cut in between (`cuts`: where a
        Highlights-mode video jumps in time), else halfway between the two moments - so a Highlights video
        splits into back-to-back clips with no footage lost or used twice."""
        groups: list[list[tuple[float, str]]] = []
        for m in sorted(markers, key=lambda m: m["video_time"]):
            t = float(m["video_time"])
            if groups and t - groups[-1][-1][0] <= cls.MOMENT_GAP:
                groups[-1].append((t, m["label"]))
            else:
                groups.append([(t, m["label"])])
        segments: list[Segment] = []
        for group in groups:
            end = group[-1][0] + post
            labels = list(dict.fromkeys(label for _, label in group))   # unique, in order
            segments.append(cls(max(0.0, group[0][0] - pre), min(end, duration) if duration else end,
                                " + ".join(labels)))
        for i in range(len(segments) - 1):
            a, b = segments[i], segments[i + 1]
            if a.end > b.start:
                lo, hi = groups[i][-1][0], groups[i + 1][0][0]
                mid = (lo + hi) / 2
                between = [c for c in cuts if lo < c < hi]
                edge = min(between, key=lambda c: abs(c - mid)) if between else mid
                a.end = b.start = edge   # they meet exactly there: nothing lost, nothing twice
        return [s for s in segments if s.duration > 0.05]


class ClipExporter:
    def __init__(self, ffmpeg: FFmpeg) -> None:
        self.ffmpeg = ffmpeg

    def export_clips(self, video: Path, segments: list[Segment], out_dir: Path,
                     progress: Progress | None = None) -> list[Path]:
        """Stream copy, so it's near-instant. Starts snap to the previous keyframe (<= 2 s earlier)."""
        out_dir.mkdir(parents=True, exist_ok=True)
        made: list[Path] = []
        for i, seg in enumerate(segments, 1):
            if progress:
                progress((i - 1) / len(segments), f"Clip {i}/{len(segments)}: {seg.label}")
            out = out_dir / f"{video.stem}_{i:02d}_{Format.slug(seg.label)}.mp4"
            r = self.ffmpeg.run(["-loglevel", "error", "-y", "-ss", f"{seg.start:.3f}", "-i", str(video),
                                 "-t", f"{seg.duration:.3f}", *FFmpeg.AV_ONLY, "-map_chapters", "-1",
                                 "-c", "copy", "-avoid_negative_ts", "make_zero",
                                 "-movflags", "+faststart", str(out)], timeout=300)
            if r.returncode != 0 or not out.exists():
                raise RuntimeError(f"Clip {i} failed: {r.stderr.strip()[-300:]}")
            made.append(out)
            log.info("Clip saved: %s", out.name)
        if progress:
            progress(1.0, f"{len(made)} clips saved")
        return made

    def export_reel(self, video: Path, segments: list[Segment], out: Path, encoder: str,
                    quality: int, progress: Progress | None = None) -> Path:
        """All segments joined into one video, re-encoded on the GPU for clean cuts."""
        out.parent.mkdir(parents=True, exist_ok=True)
        audio = self.ffmpeg.has_audio(video)
        args = ["-loglevel", "error", "-y", "-nostats", "-progress", "pipe:1"]
        for seg in segments:
            args += ["-ss", f"{seg.start:.3f}", "-t", f"{seg.duration:.3f}", "-i", str(video)]
        pads = "".join(f"[{i}:v:0]" + (f"[{i}:a:0]" if audio else "") for i in range(len(segments)))
        graph = f"{pads}concat=n={len(segments)}:v=1:a={int(audio)}[v]" + ("[a]" if audio else "")
        args += ["-filter_complex", graph, "-map", "[v]"]
        if audio:
            args += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        args += [*FFmpeg.encoder(encoder).file_args(quality), "-map_chapters", "-1",
                 "-movflags", "+faststart", str(out)]

        total_us = sum(s.duration for s in segments) * 1_000_000
        with self.ffmpeg.popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               encoding="utf-8", errors="replace") as proc:
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                if key in ("out_time_us", "out_time_ms") and value.isdigit() and progress and total_us:
                    progress(min(int(value) / total_us, 0.99), "Encoding highlight reel...")
            err = proc.stderr.read()
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"Reel failed: {err.strip()[-300:]}")
        if progress:
            progress(1.0, "Highlight reel saved")
        log.info("Highlight reel saved: %s", out.name)
        return out
