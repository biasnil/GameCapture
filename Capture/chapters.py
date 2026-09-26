"""Writes FFMETADATA chapter files that ffmpeg embeds into the finished video."""
from __future__ import annotations

from pathlib import Path

from Core.formatting import Format


class ChapterWriter:
    @staticmethod
    def write(video: Path, chapters: list[tuple[float, str]], duration: float) -> Path:
        """chapters = (seconds, title) pairs. A 'Start' chapter covers any gap at the beginning."""
        points = sorted(chapters)
        if not points or points[0][0] > 1.0:
            points.insert(0, (0.0, "Start"))
        lines = [";FFMETADATA1"]
        for i, (t, title) in enumerate(points):
            start = int(t * 1000)
            end = int((points[i + 1][0] if i + 1 < len(points) else max(duration, t + 1)) * 1000)
            if end <= start:
                continue
            lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={end}",
                      f"title={Format.ffmeta_escape(title)}"]
        meta = video.with_suffix(".chapters.txt")
        meta.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return meta
