"""Video editor model (Outplayed-style): a project is a row of clips cut from any videos.

    project videos   the source files you imported (recordings, clips, any .mp4/.mkv)
    timeline         clips in play order; each is a [start, end) window of one source

Split / trim / duplicate / delete / reorder only change these numbers - the source files are never
touched. Export re-encodes the timeline into one video (clips can come from different files, sizes
and frame rates, so everything is scaled to one size and joined with ffmpeg's concat filter).

Projects are saved as JSON in the app data folder (Paths.PROJECTS)."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from Capture.ffmpeg import FFmpeg
from Core.clips import Segment
from Core.formatting import Format

log = logging.getLogger("gamecapture.editor")

Progress = Callable[[float, str], None]  # (fraction 0-1, message)
MIN_CLIP = 0.2  # seconds: nothing shorter survives a split or trim


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class SourceVideo:
    """A video in the project's 'Project videos' list."""
    path: str
    duration: float
    label: str = ""
    markers: list[dict] = field(default_factory=list)   # highlights in source time (from the sidecar)

    @property
    def name(self) -> str:
        return self.label or Path(self.path).stem


@dataclass
class EditClip:
    source: str        # path of the source video
    start: float       # in-point, seconds into the source
    end: float         # out-point
    label: str = ""
    id: str = field(default_factory=_new_id)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class ExportSettings:
    resolution: str = "source"   # source (first clip's size) | 1440p | 1080p | 720p | 480p
    fps: int = 60
    quality: int = 21            # constant quality, lower = better
    audio: bool = True

    HEIGHTS = {"1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480}


@dataclass
class EditProject:
    name: str = "Untitled project"
    sources: list[SourceVideo] = field(default_factory=list)
    clips: list[EditClip] = field(default_factory=list)
    export: ExportSettings = field(default_factory=ExportSettings)
    id: str = field(default_factory=_new_id)
    updated: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    SCHEMA = 1

    # ---------- timeline maths ----------

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)

    def clip_offset(self, index: int) -> float:
        """Timeline time where clip `index` begins."""
        return sum(c.duration for c in self.clips[:index])

    def locate(self, t: float) -> tuple[int, float] | None:
        """Timeline time -> (clip index, time inside the source). None for an empty timeline."""
        if not self.clips:
            return None
        t = max(0.0, t)
        offset = 0.0
        for i, c in enumerate(self.clips):
            if t < offset + c.duration or i == len(self.clips) - 1:
                return i, min(c.start + (t - offset), c.end)
            offset += c.duration
        return None  # pragma: no cover

    def source(self, path: str) -> SourceVideo | None:
        return next((s for s in self.sources if s.path == path), None)

    def markers_in(self, clip: EditClip) -> list[dict]:
        """The source's highlights that fall inside this clip (still in source time)."""
        src = self.source(clip.source)
        return [m for m in (src.markers if src else []) if clip.start <= m.get("video_time", -1) < clip.end]

    # ---------- editing ----------

    def add_source(self, path: str | Path, duration: float, label: str = "",
                   markers: list[dict] | None = None) -> SourceVideo:
        path = str(path)
        existing = self.source(path)
        if existing is not None:
            return existing
        src = SourceVideo(path, float(duration), label, list(markers or []))
        self.sources.append(src)
        return src

    def remove_source(self, path: str) -> None:
        self.sources = [s for s in self.sources if s.path != path]
        self.clips = [c for c in self.clips if c.source != path]

    def add_clip(self, source: str | Path, start: float, end: float, label: str = "",
                 index: int | None = None) -> EditClip | None:
        src = self.source(str(source))
        if src is not None and src.duration > 0:
            end = min(end, src.duration)
        start = max(0.0, start)
        if end - start < MIN_CLIP:
            return None
        clip = EditClip(str(source), start, end, label)
        self.clips.insert(len(self.clips) if index is None else index, clip)
        return clip

    def highlight_windows(self, path: str, pre: float, post: float, start: float = 0.0,
                          end: float | None = None) -> list[Segment]:
        """Your highlights in a video (between start and end), padded and merged like Sessions does."""
        src = self.source(str(path))
        if src is None:
            return []
        end = src.duration if end is None else end
        markers = [m for m in src.markers if m.get("involves_me") and m.get("type") in Segment.HIGHLIGHT_TYPES
                   and start <= m.get("video_time", -1) < end]
        out = []
        for seg in Segment.from_markers(markers, pre, post, src.duration or None):
            seg.start, seg.end = max(seg.start, start), min(seg.end, end)
            if seg.duration >= MIN_CLIP:
                out.append(seg)
        return out

    def add_highlights(self, path: str | Path, pre: float, post: float, index: int | None = None) -> int:
        """Each highlight of a video becomes its own clip (at `index`, default the end). Returns how many."""
        at = len(self.clips) if index is None else index
        segs = self.highlight_windows(str(path), pre, post)
        for k, seg in enumerate(segs):
            self.clips.insert(at + k, EditClip(str(path), seg.start, seg.end, seg.label))
        return len(segs)

    def keep_highlights(self, index: int, pre: float, post: float) -> int:
        """Ripple: replace a clip by just its highlights, one clip each - the parts in between go and
        everything after moves up. Returns how many clips it became (0 = no highlights, nothing changed)."""
        if not 0 <= index < len(self.clips):
            return 0
        c = self.clips[index]
        segs = self.highlight_windows(c.source, pre, post, c.start, c.end)
        if not segs:
            return 0
        self.clips[index:index + 1] = [EditClip(c.source, s.start, s.end, s.label) for s in segs]
        return len(segs)

    def split(self, t: float) -> bool:
        """Cut the clip under timeline time t in two."""
        hit = self.locate(t)
        if hit is None:
            return False
        i, src_t = hit
        c = self.clips[i]
        if src_t - c.start < MIN_CLIP or c.end - src_t < MIN_CLIP:
            return False
        tail = EditClip(c.source, src_t, c.end, c.label)
        c.end = src_t
        self.clips.insert(i + 1, tail)
        return True

    def delete(self, index: int) -> None:
        if 0 <= index < len(self.clips):
            del self.clips[index]

    def duplicate(self, index: int) -> int:
        if not 0 <= index < len(self.clips):
            return -1
        c = self.clips[index]
        self.clips.insert(index + 1, EditClip(c.source, c.start, c.end, c.label))
        return index + 1

    def move(self, index: int, to: int) -> int:
        """Move clip `index` so it ends up at position `to`. Returns its new index."""
        if not 0 <= index < len(self.clips):
            return index
        to = max(0, min(to, len(self.clips) - 1))
        self.clips.insert(to, self.clips.pop(index))
        return to

    def trim(self, index: int, start: float | None = None, end: float | None = None) -> None:
        """Move a clip's in and/or out point, never past the source or below MIN_CLIP."""
        if not 0 <= index < len(self.clips):
            return
        c = self.clips[index]
        src = self.source(c.source)
        limit = src.duration if src and src.duration > 0 else float("inf")
        if start is not None:
            c.start = min(max(0.0, start), c.end - MIN_CLIP)
        if end is not None:
            c.end = max(min(end, limit), c.start + MIN_CLIP)

    # ---------- files ----------

    def to_dict(self) -> dict:
        data = asdict(self)
        data["schema"] = self.SCHEMA
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "EditProject":
        export_fields = ExportSettings.__dataclass_fields__
        project = cls(
            name=data.get("name") or "Untitled project",
            sources=[SourceVideo(s["path"], float(s.get("duration", 0)), s.get("label", ""), s.get("markers", []))
                     for s in data.get("sources", []) if s.get("path")],
            clips=[EditClip(c["source"], float(c["start"]), float(c["end"]), c.get("label", ""),
                            c.get("id") or _new_id()) for c in data.get("clips", []) if c.get("source")],
            export=ExportSettings(**{k: v for k, v in (data.get("export") or {}).items() if k in export_fields}),
            id=data.get("id") or _new_id(),
            updated=data.get("updated") or datetime.now().isoformat(timespec="seconds"),
        )
        return project

    def missing_sources(self) -> list[str]:
        return [s.path for s in self.sources if not Path(s.path).exists()]


class ProjectStore:
    """Editor projects as <id>.json files in one folder."""

    def __init__(self, folder: Path) -> None:
        self.dir = folder

    def _file(self, project_id: str) -> Path:
        return self.dir / f"{project_id}.json"

    def exists(self, project_id: str) -> bool:
        return self._file(project_id).exists()

    def save(self, project: EditProject) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        project.updated = datetime.now().isoformat(timespec="seconds")
        path = self._file(project.id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(project.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def load(self, project_id: str) -> EditProject | None:
        try:
            return EditProject.from_dict(json.loads(self._file(project_id).read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("Could not open project %s: %s", project_id, exc)
            return None

    def list(self) -> list[EditProject]:
        """Newest first."""
        if not self.dir.is_dir():
            return []
        found = []
        for f in self.dir.glob("*.json"):
            p = self.load(f.stem)
            if p is not None:
                found.append((p.updated, f.stat().st_mtime_ns, p))  # same second: last written wins
        return [p for *_, p in sorted(found, key=lambda x: x[:2], reverse=True)]

    def delete(self, project_id: str) -> None:
        self._file(project_id).unlink(missing_ok=True)


class ProjectExporter:
    """Renders the timeline into one video."""

    def __init__(self, ffmpeg: FFmpeg) -> None:
        self.ffmpeg = ffmpeg

    def probe(self, video: Path) -> dict:
        """{'duration', 'width', 'height', 'audio'} from ffmpeg's banner (no ffprobe needed)."""
        err = self.ffmpeg.run(["-i", str(video)]).stderr
        info = {"duration": 0.0, "width": 0, "height": 0, "audio": "Audio:" in err}
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
        if m:
            info["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
        m = re.search(r"Stream #\d+:\d+.*?Video:.*?(\d{2,5})x(\d{2,5})", err)
        if m:
            info["width"], info["height"] = int(m[1]), int(m[2])
        return info

    def output_size(self, project: EditProject, probes: dict[str, dict]) -> tuple[int, int]:
        first = probes.get(project.clips[0].source, {}) if project.clips else {}
        w, h = first.get("width") or 1920, first.get("height") or 1080
        target = ExportSettings.HEIGHTS.get(project.export.resolution)
        if target and target < h:  # never upscale
            w, h = round(w * target / h), target
        return w - w % 2, h - h % 2

    def build_args(self, project: EditProject, out: Path, encoder: str,
                   probes: dict[str, dict]) -> list[str]:
        clips = [c for c in project.clips if c.duration > 0]
        if not clips:
            raise ValueError("The timeline is empty")
        w, h = self.output_size(project, probes)
        fps = int(project.export.fps) or 60
        want_audio = project.export.audio
        args = ["-loglevel", "error", "-y", "-nostats", "-progress", "pipe:1"]
        for c in clips:
            args += ["-ss", f"{c.start:.3f}", "-t", f"{c.duration:.3f}", "-i", c.source]
        chains, pads = [], []
        for i, c in enumerate(clips):
            chains.append(f"[{i}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{i}]")
            pad = f"[v{i}]"
            if want_audio:
                if probes.get(c.source, {}).get("audio"):
                    chains.append(f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo,"
                                  f"atrim=0:{c.duration:.3f},apad=whole_dur={c.duration:.3f}[a{i}]")
                else:  # a clip without sound gets silence, so the others stay in sync
                    chains.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{c.duration:.3f}[a{i}]")
                pad += f"[a{i}]"
            pads.append(pad)
        graph = ";".join(chains) + ";" + "".join(pads) + \
            f"concat=n={len(clips)}:v=1:a={int(want_audio)}[v]" + ("[a]" if want_audio else "")
        args += ["-filter_complex", graph, "-map", "[v]"]
        if want_audio:
            args += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        args += [*FFmpeg.encoder(encoder).file_args(int(project.export.quality)),
                 "-map_chapters", "-1", "-movflags", "+faststart", str(out)]
        return args

    def export(self, project: EditProject, out: Path, encoder: str = "x264",
               progress: Progress | None = None) -> Path:
        missing = project.missing_sources()
        used = {c.source for c in project.clips}
        if any(m in used for m in missing):
            raise FileNotFoundError("Missing video: " + ", ".join(Path(m).name for m in missing if m in used))
        out.parent.mkdir(parents=True, exist_ok=True)
        if progress:
            progress(0.0, "Preparing...")
        probes = {src: self.probe(Path(src)) for src in used}
        args = self.build_args(project, out, encoder, probes)
        total_us = project.duration * 1_000_000
        with self.ffmpeg.popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               encoding="utf-8", errors="replace") as proc:
            for line in proc.stdout:
                key, _, value = line.strip().partition("=")
                if key in ("out_time_us", "out_time_ms") and value.isdigit() and progress and total_us:
                    progress(min(int(value) / total_us, 0.99), f"Rendering {Format.duration(int(value) / 1e6)} "
                                                               f"of {Format.duration(project.duration)}...")
            err = proc.stderr.read()
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"Export failed: {err.strip()[-300:]}")
        if progress:
            progress(1.0, "Video exported")
        log.info("Editor export saved: %s", out.name)
        return out

    @staticmethod
    def output_name(project: EditProject) -> str:
        return f"{Format.slug(project.name) or 'project'}_{datetime.now():%Y-%m-%d_%H-%M-%S}.mp4"
