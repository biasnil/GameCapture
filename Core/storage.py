"""Disk usage by category, and the auto-delete limit for old recordings.

Only full recordings are ever auto-deleted (oldest first). Clips you exported are never touched,
and recordings with a starred highlight are kept when keep_favorites is on."""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from Core.config import StorageSettings
from Core.library import RecordingEntry, RecordingLibrary
from Core.sidecar import Sidecar

log = logging.getLogger("gamecapture.storage")

GB = 1024 ** 3


@dataclass
class StorageUsage:
    recordings: int     # bytes: videos + their .json
    clips: int
    other: int          # everything else on the drive
    free: int
    total: int

    @property
    def gamecapture(self) -> int:
        return self.recordings + self.clips


class StorageManager:
    def __init__(self, recordings_dir: Path, clips_dir: Path, settings: StorageSettings) -> None:
        self.recordings_dir = recordings_dir
        self.clips_dir = clips_dir
        self.settings = settings
        self.library = RecordingLibrary(recordings_dir)

    @staticmethod
    def _size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def entry_bytes(self, e: RecordingEntry) -> int:
        return self._size(e.video) + self._size(Sidecar.path_for(e.video))

    def usage(self) -> StorageUsage:
        rec = sum(self.entry_bytes(e) for e in self.library.scan())
        clips = sum(self._size(p) for p in self.clips_dir.glob("*.mp4")) if self.clips_dir.is_dir() else 0
        disk = shutil.disk_usage(self.recordings_dir)
        other = max(0, disk.total - disk.free - rec - clips)
        return StorageUsage(rec, clips, other, disk.free, disk.total)

    def protected(self, e: RecordingEntry) -> bool:
        return self.settings.keep_favorites and any(m.get("favorite") for m in e.markers)

    def plan_cleanup(self, exclude: set[Path] = frozenset()) -> list[RecordingEntry]:
        """Oldest-first recordings to delete so recordings fit under the limit."""
        if not self.settings.limit_enabled or self.settings.limit_gb <= 0:
            return []
        entries = self.library.scan(exclude=exclude)
        total = sum(self.entry_bytes(e) for e in entries)
        limit = self.settings.limit_gb * GB
        victims = []
        for e in sorted(entries, key=lambda e: e.when):  # oldest first
            if total <= limit:
                break
            if self.protected(e) or e.video in exclude:
                continue
            victims.append(e)
            total -= self.entry_bytes(e)
        return victims

    def enforce(self, exclude: set[Path] = frozenset(), thumbs_dir: Path | None = None) -> list[Path]:
        """Delete what plan_cleanup() picked. Files in use are skipped (tried again next time)."""
        deleted = []
        for e in self.plan_cleanup(exclude):
            try:
                e.video.unlink()
            except OSError as exc:
                log.warning("Auto-delete skipped %s (%s)", e.video.name, exc)
                continue
            Sidecar.path_for(e.video).unlink(missing_ok=True)
            if thumbs_dir is not None:
                (thumbs_dir / f"{e.video.stem}.jpg").unlink(missing_ok=True)
            deleted.append(e.video)
            log.info("Storage limit: deleted %s (%.1f GB)", e.video.name, e.size_mb / 1024)
        return deleted
