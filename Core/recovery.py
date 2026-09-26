"""Rebuilds a highlights .json from a leftover .chapters.txt (when saving a match failed).

The chapters file only holds your moments (plus game start/end) as "video time + title", so the
recovered markers have no raw event data and the champion must be supplied by hand."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from Core.sidecar import Sidecar
from Games.registry import GameRegistry


@dataclass
class RecoveryResult:
    video: Path
    sidecar: Path
    markers: int
    kda: str
    result: str
    champion: str
    duplicate_mkv: Path | None


class HighlightRecovery:
    MULTIKILLS = {"Double kill": 2, "Triple kill": 3, "Quadra kill": 4, "PENTAKILL": 5}
    RULES = (  # (pattern, type, importance) - first match wins, order matters
        (r"^Killed by ", "death", 1),
        (r"^Killed ", "kill", 2),
        (r"^Assist on ", "assist", 1),
        (r"^First blood", "first_blood", 2),
        (r"^(Double kill|Triple kill|Quadra kill|PENTAKILL)$", "multikill", 3),
        (r"STOLEN", "objective", 3),
        (r"(Dragon|Baron|Rift Herald|Voidgrub|Atakhan) taken by", "objective", 2),
        (r"^(Turret|Inhibitor) destroyed", "structure", 1),
        (r"^Ace", "ace", 3),
        (r"^Game start$", "game_start", 0),
        (r"^Game end - ", "game_end", 0),
    )

    # ---------- parsing ----------

    @staticmethod
    def read_chapters(path: Path) -> list[tuple[float, str]]:
        """(seconds, title) for each [CHAPTER] in an FFMETADATA file."""
        chapters, current = [], None
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line == "[CHAPTER]":
                current = {"timebase": 0.001}
                chapters.append(current)
            elif current is not None and "=" in line:
                key, value = line.split("=", 1)
                if key == "TIMEBASE":
                    num, den = value.split("/")
                    current["timebase"] = int(num) / int(den)
                elif key == "START":
                    current["start"] = int(value)
                elif key == "title":
                    current["title"] = re.sub(r"\\(.)", r"\1", value)
        return [(c["start"] * c["timebase"], c["title"]) for c in chapters if "start" in c and "title" in c]

    @classmethod
    def classify(cls, label: str) -> tuple[str, int]:
        for pattern, kind, importance in cls.RULES:
            if re.search(pattern, label):
                return kind, importance
        return "other", 1

    @classmethod
    def markers_from_chapters(cls, chapters: list[tuple[float, str]]) -> list[dict]:
        markers = []
        for t, title in chapters:
            for label in title.split(" + "):  # merged chapters hold several moments
                if label == "Start":
                    continue  # padding chapter added by ChapterWriter, not a real event
                kind, importance = cls.classify(label)
                event = {"KillStreak": cls.MULTIKILLS[label]} if kind == "multikill" else {}
                markers.append({"type": kind, "label": label, "importance": importance,
                                "involves_me": kind not in ("game_start", "game_end"),
                                "event": event, "video_time": round(t, 2), "game_time": None,
                                "recovered": True})
        return markers

    # ---------- files ----------

    @staticmethod
    def find_video(chapters_file: Path) -> tuple[Path | None, Path | None]:
        """(best video, duplicate .mkv or None). The chapters file is named after the original .mkv."""
        stem = chapters_file.name.split(".chapters")[0]
        folder = chapters_file.parent
        mp4s = sorted(folder.glob(f"{stem}*.mp4"))
        mkv = folder / f"{stem}.mkv"
        if mp4s:
            return mp4s[0], (mkv if mkv.exists() else None)
        return (mkv if mkv.exists() else None), None

    @staticmethod
    def tag_from_name(video: Path, original_stem: str) -> dict:
        """'LoL_<time>_Sion_Lose_6-5-8.mp4' -> champion/result/K/D/A saved at the end of the match."""
        rest = video.stem[len(original_stem):].lstrip("_")
        m = re.fullmatch(r"(?P<champion>.+)_(?P<result>[A-Za-z]+)_(?P<k>\d+)-(?P<d>\d+)-(?P<a>\d+)", rest)
        if not m:
            return {}
        return {"champion": m["champion"], "result": m["result"],
                "kills": int(m["k"]), "deaths": int(m["d"]), "assists": int(m["a"])}

    ORIGINAL_STEM = re.compile(r"^(.+?_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})")

    @classmethod
    def recover_from_video(cls, video: Path, ffmpeg, champion: str | None = None, mode: str = "",
                           overwrite: bool = False) -> RecoveryResult:
        """The .json is gone but the .mp4 still has its chapters embedded: rebuild from those."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            meta = Path(tmp) / "chapters.txt"
            ffmpeg.run(["-loglevel", "error", "-y", "-i", str(video), "-f", "ffmetadata", str(meta)])
            if not meta.exists() or not cls.read_chapters(meta):
                raise FileNotFoundError(f"{video.name} has no highlight chapters to recover from "
                                        f"(only auto-recorded .mp4 files have them)")
            m = cls.ORIGINAL_STEM.match(video.stem)
            return cls.recover(meta, champion, mode, overwrite, video=video,
                               original_stem=m[1] if m else video.stem)

    @classmethod
    def recover(cls, chapters_file: Path, champion: str | None = None, mode: str = "",
                overwrite: bool = False, video: Path | None = None,
                original_stem: str | None = None) -> RecoveryResult:
        duplicate = None
        if video is None:
            video, duplicate = cls.find_video(chapters_file)
        if video is None:
            raise FileNotFoundError(f"No video next to {chapters_file.name} starting with its name")
        target = Sidecar.path_for(video)
        if target.exists() and not overwrite:
            raise FileExistsError(f"{target.name} already exists (use --overwrite)")

        markers = cls.markers_from_chapters(cls.read_chapters(chapters_file))
        count = lambda kind: sum(1 for m in markers if m["type"] == kind)  # noqa: E731
        end = next((m for m in markers if m["type"] == "game_end"), None)
        result = end["label"].split(" - ", 1)[1] if end else "Unfinished"
        game = {"champion": "Unknown", "mode": mode, "result": result, "kills": count("kill"),
                "deaths": count("death"), "assists": count("assist")}
        stem = original_stem or chapters_file.name.split(".chapters")[0]
        game.update(cls.tag_from_name(video, stem))  # end-of-match truth
        if champion:
            game["champion"] = champion
        game["recovered_from"] = chapters_file.name
        result = game["result"]
        Sidecar.write(video, Sidecar.build(video.name, GameRegistry.LEAGUE.id, game, markers))
        return RecoveryResult(video, target, len(markers), f"{game['kills']}/{game['deaths']}/{game['assists']}",
                              result, game["champion"], duplicate)
