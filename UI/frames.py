"""Filmstrip frames for the editor timeline: small JPEGs made by ffmpeg in the background."""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPixmap

from Capture.ffmpeg import FFmpeg


class FrameCache(QObject):
    ready = pyqtSignal()   # a frame finished - repaint
    STEP = 1.0             # frames are taken on whole seconds, so small trims reuse them
    WIDTH = 160

    def __init__(self, cache_dir: Path, ffmpeg: FFmpeg | None) -> None:
        super().__init__()
        self.dir = cache_dir
        self.ffmpeg = ffmpeg
        self._pix: dict[tuple[str, float], QPixmap] = {}
        self._pending: set[tuple[str, float]] = set()
        self._failed: set[tuple[str, float]] = set()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="frames")
        self.generation = 0    # bumped whenever a frame finishes, so painters know to redraw

    def _jpg(self, video: str, t: float) -> Path:
        digest = hashlib.sha1(video.encode("utf-8")).hexdigest()[:12]
        return self.dir / f"{digest}_{int(t * 10):07d}.jpg"

    def get(self, video: str, t: float) -> QPixmap | None:
        """Cached frame near time t, or None while it's being made (watch `ready`)."""
        key = (video, round(max(0.0, t) / self.STEP) * self.STEP)
        if key in self._pix:
            return self._pix[key]
        jpg = self._jpg(*key)
        if jpg.exists():
            pm = QPixmap(str(jpg))
            if not pm.isNull():
                self._pix[key] = pm
                return pm
        if self.ffmpeg and key not in self._pending and key not in self._failed:
            self._pending.add(key)
            self._pool.submit(self._make, key, jpg)
        return None

    def _make(self, key: tuple[str, float], jpg: Path) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            if not self.ffmpeg.extract_frame(Path(key[0]), jpg, key[1], width=self.WIDTH):
                self._failed.add(key)
        finally:
            self._pending.discard(key)
            self.generation += 1
            self.ready.emit()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
