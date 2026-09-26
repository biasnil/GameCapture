"""Video thumbnails made with ffmpeg in the background and cached as JPEGs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPixmap

from Capture.ffmpeg import FFmpeg


class ThumbnailCache(QObject):
    ready = pyqtSignal(str)  # video path whose thumbnail just became available

    def __init__(self, cache_dir: Path, ffmpeg: FFmpeg | None) -> None:
        super().__init__()
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg = ffmpeg
        self._pix: dict[str, QPixmap] = {}
        self._pending: set[str] = set()
        self._failed: set[str] = set()
        self._made: set[str] = set()  # generated this session: trust it even if clocks disagree
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thumbs")

    def _jpg(self, video: Path) -> Path:
        return self.dir / f"{video.stem}.jpg"

    def get(self, video: Path, at: float = 120.0) -> QPixmap | None:
        """Cached pixmap, or None while it's being generated (watch `ready`)."""
        key = str(video)
        if key in self._pix:
            return self._pix[key]
        jpg = self._jpg(video)
        try:
            fresh = jpg.exists() and (key in self._made or jpg.stat().st_mtime >= video.stat().st_mtime)
        except OSError:
            fresh = False
        if fresh:
            pm = QPixmap(str(jpg))
            if not pm.isNull():
                self._pix[key] = pm
                return pm
        if self.ffmpeg and key not in self._pending and key not in self._failed:
            self._pending.add(key)
            self._pool.submit(self._make, video, jpg, at)
        return None

    def _make(self, video: Path, jpg: Path, at: float) -> None:
        key = str(video)
        if any(self.ffmpeg.extract_frame(video, jpg, t) for t in (at, 1.0)):  # short clips: use the start
            self._made.add(key)
        else:
            self._failed.add(key)
        self._pending.discard(key)
        self.ready.emit(key)

    def forget(self, video: Path) -> None:
        self._pix.pop(str(video), None)
        self._jpg(video).unlink(missing_ok=True)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
