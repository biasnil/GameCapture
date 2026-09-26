"""The recordings folder as a list of entries (video + sidecar)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from Core.sidecar import Sidecar
from Games.registry import GameRegistry


@dataclass
class RecordingEntry:
    video: Path
    meta: dict | None
    when: datetime
    size_mb: float

    @property
    def game_id(self) -> str | None:
        return (self.meta or {}).get("game_id")

    @property
    def game_name(self) -> str:
        info = GameRegistry.get(self.game_id)
        return info.name if info else "Manual recording"

    @property
    def editable(self) -> bool:
        """Sidecar exists and isn't from a newer GameCapture version."""
        return Sidecar.is_writable(self.meta)

    @property
    def game(self) -> dict:
        return (self.meta or {}).get("game", {})

    @property
    def markers(self) -> list[dict]:
        """The marker dicts themselves - edits (favourites) persist via RecordingLibrary.save."""
        return [m for m in (self.meta or {}).get("markers", []) if "video_time" in m]

    @property
    def my_markers(self) -> list[dict]:
        return [m for m in self.markers if m.get("involves_me")]

    @property
    def title(self) -> str:
        return self.game.get("champion") or self.game.get("title") or "Manual recording"

    @property
    def result(self) -> str:
        return self.game.get("result", "")

    @property
    def kda(self) -> str:
        g = self.game
        return f"{g['kills']}/{g['deaths']}/{g['assists']}" if "kills" in g else ""

    @property
    def mode(self) -> str:
        return self.game.get("mode", "")

    @property
    def kind(self) -> str:
        """'session', 'highlights' or 'match' (manual recordings count as match)."""
        if self.game.get("session"):
            return "session"
        return "highlights" if self.game.get("highlights_only") else "match"

    @property
    def mode_name(self) -> str:
        if self.kind == "session":
            return "Session"
        info = GameRegistry.get(self.game_id)
        names = info.mode_names if info else {}
        name = names.get(self.mode, self.mode.title() if self.mode else "")
        return f"{name} · highlights" if self.kind == "highlights" else name

    @property
    def thumb_time(self) -> float:
        """Thumbnail from your first kill - more interesting than the fountain."""
        kills = sorted(m["video_time"] for m in self.my_markers if m.get("type") in ("kill", "multikill"))
        return kills[0] if kills else 120.0

    def search_text(self) -> str:
        return f"{self.game_name} {self.title} {self.result} {self.mode_name} {self.kda} {self.when:%d %b}".lower()


class RecordingLibrary:
    VIDEO_EXTS = {".mp4", ".mkv"}

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def scan(self, exclude: Iterable[Path] = ()) -> list[RecordingEntry]:
        """All finished recordings, newest first. `exclude` = files still being recorded/converted."""
        if isinstance(exclude, Path):
            exclude = [exclude]
        hidden = {Path(p).name for p in exclude}
        entries: list[RecordingEntry] = []
        if not self.folder.is_dir():
            return entries
        for p in self.folder.iterdir():
            if p.suffix.lower() not in self.VIDEO_EXTS or ".tmp" in p.name:
                continue
            if p.name in hidden:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            entries.append(RecordingEntry(p, Sidecar.read(p), datetime.fromtimestamp(st.st_mtime),
                                          st.st_size / 1_048_576))
        entries.sort(key=lambda e: e.when, reverse=True)
        return entries

    @staticmethod
    def save(entry: RecordingEntry) -> None:
        if entry.meta is not None:
            Sidecar.write(entry.video, entry.meta)
