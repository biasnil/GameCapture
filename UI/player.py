"""Video player: Qt Multimedia playback + the highlight timeline + transport controls.

While a highlight plays, the next one is prepared in a second, hidden player (UI/dual_player.py),
so Next highlight is instant instead of stuttering while the video decoder catches up."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from Core.formatting import Format
from Theme.palette import Palette
from UI.dual_player import DualPlayer
from UI.icons import Icons
from UI.timeline import Timeline
from UI.widgets import IconButton, IconTextButton


class PlayerPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lead_seconds = 5.0  # Prev/Next land this long before a highlight
        self._highlight_times: list[float] = []

        self.allow_preload = lambda: True   # set by the owner: setting on, not recording, window shown
        self.player = DualPlayer(allowed=lambda: self.allow_preload())
        self.video = self.player.view
        self.video.setMinimumHeight(260)
        self._path = ""
        self._predict = QTimer(self)
        self._predict.setInterval(700)
        self._predict.timeout.connect(self._prepare_next)

        self.timeline = Timeline()
        self.play_btn = IconButton("play", "Play / pause (Space)", size=22, color=Palette.TEXT)
        self._play_icon = Icons.icon("play", Palette.TEXT, 22)
        self._pause_icon = Icons.icon("pause", Palette.TEXT, 22)
        self.prev_btn = IconTextButton("prev", "Prev highlight")
        self.next_btn = IconTextButton("next", "Next highlight")
        self.prev_btn.setToolTip("Previous highlight (P)")
        self.next_btn.setToolTip("Next highlight (N)")
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("Muted")
        volume_icon = QLabel()
        volume_icon.setPixmap(Icons.pixmap("volume", Palette.TEXT_MUTED, 18))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(110)

        controls = QHBoxLayout()
        for w in (self.play_btn, self.prev_btn, self.next_btn, self.time_label):
            controls.addWidget(w)
        controls.addStretch()
        controls.addWidget(volume_icon)
        controls.addWidget(self.volume)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.video, 1)
        layout.addWidget(self.timeline)
        layout.addLayout(controls)

        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(lambda ms: self.timeline.set_duration(ms / 1000))
        self.player.playbackStateChanged.connect(self._on_state)
        self.timeline.seekRequested.connect(self._user_seek)
        self.play_btn.clicked.connect(self.toggle_play)
        self.prev_btn.clicked.connect(lambda: self.jump_highlight(-1))
        self.next_btn.clicked.connect(lambda: self.jump_highlight(+1))
        self.volume.valueChanged.connect(lambda v: self.player.set_volume(v / 100))

    @property
    def duration(self) -> float:
        return self.player.duration() / 1000

    def load(self, path: Path | None, markers: list[dict]) -> None:
        self._path = str(path) if path else ""
        self.player.unload()
        self.player.load(self._path, at=0.0)   # shows the first frame instead of a black box
        self.timeline.set_markers(markers)
        self.timeline.set_segments([])
        self.timeline.set_position(0)
        self._highlight_times = sorted(m["video_time"] for m in markers if m.get("involves_me"))
        for b in (self.prev_btn, self.next_btn):
            b.setEnabled(bool(self._highlight_times))

    def unload(self) -> None:
        """Release the file (needed before deleting it on Windows)."""
        self.load(None, [])

    def seek(self, seconds: float) -> None:
        self.player.setPosition(int(max(0.0, seconds) * 1000))

    def _user_seek(self, seconds: float) -> None:
        """Clicking / dragging the ruler: you're looking around, so stop predicting for a moment."""
        self.player.quiet()
        self.seek(seconds)

    def skip(self, delta: float) -> None:
        self.seek(self.player.position() / 1000 + delta)

    def toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def play_from(self, seconds: float) -> None:
        seconds = max(0.0, seconds)
        if not self.player.take(self._path, seconds):  # instant if it was prepared, else a normal seek
            self.seek(seconds)
            self.player.play()

    def _target(self, direction: int, pos: float) -> float | None:
        if direction > 0:
            later = [t for t in self._highlight_times if t - self.lead_seconds > pos + 0.5]
            return later[0] if later else None
        earlier = [t for t in self._highlight_times if t - self.lead_seconds < pos - 1.5]
        return earlier[-1] if earlier else None

    def jump_highlight(self, direction: int) -> None:
        target = self._target(direction, self.player.position() / 1000)
        if target is not None:
            self.play_from(target - self.lead_seconds)

    def _prepare_next(self) -> None:
        """While playing: have the next highlight ready in the hidden player."""
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState or not self._path:
            return
        if not self.player.can_prepare():
            self.player.drop_standby()
            return
        target = self._target(+1, self.player.position() / 1000)
        if target is not None:
            self.player.prepare(self._path, max(0.0, target - self.lead_seconds))

    def _on_position(self, ms: int) -> None:
        self.timeline.set_position(ms / 1000)
        self.time_label.setText(f"{Format.duration(ms / 1000)} / {Format.duration(self.duration)}")

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setIcon(self._pause_icon if playing else self._play_icon)
        if playing:
            self._predict.start()
        else:
            self._predict.stop()
