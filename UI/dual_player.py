"""Two media players behind one video view, so the next clip can be ready before you jump to it.

The *active* player is the one you see and hear. The *standby* player can be told to prepare a
file + time (`prepare`): it opens the file muted and hidden, seeks there and stops on the first
decoded frame. Jumping to exactly that spot (`take`) then just swaps the two - no seek, no decode
from the previous keyframe, no hitch. Any other jump falls back to a normal seek.

Preloading is only a bonus: the owner decides when it's allowed (setting on, not recording, window
visible) through `allowed`, and a player that wasn't ready in time is simply ignored.

Also fixes Qt's black picture after loading a file: a paused / freshly loaded player shows its
current frame (`show_still`)."""
from __future__ import annotations

import time
from typing import Callable

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import QStackedWidget

PLAYING = QMediaPlayer.PlaybackState.PlayingState


class _Slot:
    """One QMediaPlayer + its audio output + its video widget."""

    def __init__(self) -> None:
        self.video = QVideoWidget()
        self.video.setStyleSheet("background: #000;")
        self.audio = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.path = ""                   # file currently loaded
        self.target: float | None = None  # standby: seconds it is preparing / holding
        self.ready = False               # standby: first frame at `target` decoded, paused there
        self.still = False               # showing one frame (play muted -> pause on first frame)

    def load(self, path: str) -> None:
        if path != self.path:
            self.path = path
            self.player.setSource(QUrl.fromLocalFile(path) if path else QUrl())


class DualPlayer(QObject):
    # Same signals as QMediaPlayer, always from the active player.
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    playbackStateChanged = pyqtSignal(object)
    mediaStatusChanged = pyqtSignal(object)
    swapped = pyqtSignal()              # the standby player just became the visible one

    TOLERANCE = 0.6                     # s: a prepared spot this close to the requested one is used

    def __init__(self, allowed: Callable[[], bool] = lambda: True) -> None:
        super().__init__()
        self.allowed = allowed
        self.view = QStackedWidget()
        self.view.setStyleSheet("background: #000;")
        self._slots = [_Slot(), _Slot()]
        self._active = 0
        self._volume = 0.8
        self._quiet_until = 0.0         # no predicting until then (you were scrubbing around)
        self._pending_seek: float | None = None
        for i, slot in enumerate(self._slots):
            self.view.addWidget(slot.video)
            p = slot.player
            p.positionChanged.connect(lambda ms, i=i: self._forward(i, self.positionChanged, ms))
            p.durationChanged.connect(lambda ms, i=i: self._forward(i, self.durationChanged, ms))
            p.playbackStateChanged.connect(lambda s, i=i: self._forward(i, self.playbackStateChanged, s))
            p.mediaStatusChanged.connect(lambda s, i=i: self._on_status(i, s))
            slot.video.videoSink().videoFrameChanged.connect(lambda _f, i=i: self._on_frame(i))
        self._apply_audio()

    # ---------- QMediaPlayer-like API (active player) ----------

    @property
    def _a(self) -> _Slot:
        return self._slots[self._active]

    @property
    def _s(self) -> _Slot:
        return self._slots[1 - self._active]

    @property
    def path(self) -> str:
        return self._a.path

    @property
    def loading(self) -> bool:
        """The active player is still opening a file (its position isn't meaningful yet)."""
        return self._pending_seek is not None

    def holds(self, path: str) -> bool:
        """Either player has this file open."""
        return any(slot.path == path for slot in self._slots)

    def setSource(self, url: QUrl) -> None:
        self.load(url.toLocalFile() if url.isValid() and not url.isEmpty() else "")

    def source(self) -> QUrl:
        return self._a.player.source()

    def load(self, path: str, at: float | None = None) -> None:
        """Open a file in the active player (no-op if it's already open) and show `at` as a still."""
        slot = self._a
        if path != slot.path:
            slot.player.stop()
            slot.load(path)
            self._pending_seek = at if at is not None else 0.0
        elif at is not None:
            self.setPosition(int(at * 1000))
        if not path:
            self.drop_standby()

    def setPosition(self, ms: int) -> None:
        a = self._a
        if self._pending_seek is not None:      # still opening: remember where to go
            self._pending_seek = ms / 1000
            return
        a.player.setPosition(int(ms))
        if a.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState and a.path:
            self._still(a)  # a stopped player shows nothing; a paused one redraws by itself

    def position(self) -> int:
        if self._pending_seek is not None:
            return int(self._pending_seek * 1000)
        return self._a.player.position()

    def duration(self) -> int:
        return self._a.player.duration()

    def play(self) -> None:
        self._a.still = False
        self._apply_audio()
        self._a.player.play()

    def pause(self) -> None:
        self._a.player.pause()

    def stop(self) -> None:
        self._a.player.stop()

    def playbackState(self):
        return self._a.player.playbackState()

    def mediaStatus(self):
        return self._a.player.mediaStatus()

    def setActiveAudioTrack(self, index: int) -> None:
        if hasattr(self._a.player, "setActiveAudioTrack"):
            self._a.player.setActiveAudioTrack(index)

    def set_volume(self, volume: float) -> None:
        self._volume = volume
        self._apply_audio()

    def unload(self) -> None:
        """Release both files (needed before deleting one on Windows)."""
        for slot in self._slots:
            slot.player.stop()
            slot.load("")
            slot.target, slot.ready, slot.still = None, False, False
        self._pending_seek = None

    # ---------- preloading ----------

    def quiet(self, seconds: float = 5.0) -> None:
        """You're scrubbing / jumping around: stop predicting for a moment."""
        self._quiet_until = time.monotonic() + seconds
        self.drop_standby()

    def can_prepare(self) -> bool:
        return time.monotonic() >= self._quiet_until and self.allowed()

    def prepare(self, path: str, at: float) -> None:
        """Get `path` at `at` seconds ready in the standby player (hidden, muted)."""
        if not path or not self.can_prepare():
            return
        s = self._s
        if s.path == path and s.target is not None and abs(s.target - at) < 0.05:
            return  # already on it
        s.target, s.ready, s.still = at, False, False
        s.audio.setMuted(True)
        if s.path != path:
            s.player.stop()
            s.load(path)        # seek + play once loaded (_on_status)
        else:
            s.player.setPosition(int(at * 1000))
            s.player.play()     # decode until the first frame, then pause (_on_frame)

    def is_prepared(self, path: str, at: float) -> bool:
        s = self._s
        return s.ready and s.path == path and s.target is not None and abs(s.target - at) <= self.TOLERANCE

    def take(self, path: str, at: float, play: bool = True) -> bool:
        """Jump to `path` at `at`: instant if the standby player has exactly that ready."""
        if not self.is_prepared(path, at):
            return False
        old, new = self._a, self._s
        self._active = 1 - self._active
        new.target, new.ready = None, False
        self.view.setCurrentWidget(new.video)
        old.player.pause()
        old.target, old.ready, old.still = None, False, False
        self._apply_audio()
        if play:
            new.player.play()
        self.durationChanged.emit(new.player.duration())
        self.positionChanged.emit(new.player.position())
        self.playbackStateChanged.emit(new.player.playbackState())
        self.swapped.emit()
        return True

    def drop_standby(self) -> None:
        s = self._s
        if s.target is not None or s.player.playbackState() == PLAYING:
            s.player.pause()
        s.target, s.ready = None, False

    # ---------- internals ----------

    def _apply_audio(self) -> None:
        for i, slot in enumerate(self._slots):
            active = i == self._active
            slot.audio.setVolume(self._volume)
            slot.audio.setMuted(not active or slot.still)

    def _forward(self, i: int, signal, value) -> None:
        if i == self._active:
            signal.emit(value)

    def _still(self, slot: _Slot) -> None:
        """Show the frame at the current position: Qt shows nothing until it has played once."""
        slot.still = True
        slot.audio.setMuted(True)
        slot.player.play()

    def _on_status(self, i: int, status) -> None:
        slot = self._slots[i]
        loaded = status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia)
        if i == self._active:
            if loaded and self._pending_seek is not None:
                at, self._pending_seek = self._pending_seek, None
                if hasattr(slot.player, "setActiveAudioTrack"):
                    slot.player.setActiveAudioTrack(0)   # track 1 = the mix
                slot.player.setPosition(int(at * 1000))
                if slot.player.playbackState() != PLAYING:
                    self._still(slot)
            self.mediaStatusChanged.emit(status)
        elif loaded and slot.target is not None and not slot.ready \
                and slot.player.playbackState() != PLAYING:
            if hasattr(slot.player, "setActiveAudioTrack"):
                slot.player.setActiveAudioTrack(0)
            slot.player.setPosition(int(slot.target * 1000))
            slot.player.play()

    def _on_frame(self, i: int) -> None:
        slot = self._slots[i]
        if slot.still:                      # active, paused: one frame is all we wanted
            slot.still = False
            slot.player.pause()
            self._apply_audio()
        elif i != self._active and slot.target is not None and not slot.ready:
            if slot.player.position() / 1000 >= slot.target - 0.25:
                slot.player.pause()         # parked on the first frame of the next clip
                slot.ready = True

