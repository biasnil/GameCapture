"""Video editor page (Outplayed-style): project videos on the left, preview in the middle,
a clip timeline underneath with split / duplicate / delete, and Export video.

Send highlights here from Sessions (Edit button), or import any video. Projects save themselves
to the app data folder as you edit."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar, QSlider,
                             QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from Capture.ffmpeg import FFmpeg
from Core.editor import EditProject, ProjectExporter, ProjectStore
from Core.formatting import Format
from Core.paths import Paths
from Core.sidecar import Sidecar
from Theme.palette import Palette
from UI.edit_timeline import EditTimeline
from UI.frames import FrameCache
from UI.icons import Icons
from UI.shell import Shell
from UI.widgets import IconButton, IconTextButton, SegmentedControl, ToggleSwitch

if TYPE_CHECKING:
    from Core.clips import Segment
    from Core.library import RecordingEntry
    from UI.main_window import MainWindow

log = logging.getLogger("gamecapture.ui")
ROLE_PATH = Qt.ItemDataRole.UserRole
VIDEO_FILTER = "Videos (*.mp4 *.mkv *.mov *.webm *.avi);;All files (*)"


class _ExportSignals(QObject):
    progress = pyqtSignal(int, str)
    done = pyqtSignal(bool, str, object)   # ok, message, path


class SequencePlayer(QObject):
    """Plays the timeline clip after clip with one QMediaPlayer, switching source files as needed."""
    positionChanged = pyqtSignal(float)    # timeline seconds
    playingChanged = pyqtSignal(bool)

    def __init__(self, video: QVideoWidget) -> None:
        super().__init__()
        self.audio = QAudioOutput()
        self.audio.setVolume(0.8)
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(video)
        self.project: EditProject | None = None
        self.index = -1
        self._source = ""
        self._pending: float | None = None     # seek to apply once the new file is loaded
        self._pending_t = 0.0                   # ...and the timeline time it stands for
        self._want_play = False
        self.player.positionChanged.connect(self._on_position)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.playbackStateChanged.connect(
            lambda s: self.playingChanged.emit(s == QMediaPlayer.PlaybackState.PlayingState))

    @property
    def playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def set_project(self, project: EditProject | None) -> None:
        self.project = project
        self.unload()

    def unload(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self._source, self.index, self._pending = "", -1, None

    def position(self) -> float:
        if not self.project or not 0 <= self.index < len(self.project.clips):
            return 0.0
        if self._pending is not None:  # still opening the file: report where we're going
            return self._pending_t
        c = self.project.clips[self.index]
        inside = min(max(self.player.position() / 1000 - c.start, 0.0), c.duration)
        return self.project.clip_offset(self.index) + inside

    def seek(self, t: float) -> None:
        hit = self.project.locate(t) if self.project else None
        if hit is None:
            self.unload()
            self.positionChanged.emit(0.0)
            return
        self.index, src_t = hit
        self._show(self.project.clips[self.index].source, src_t, max(0.0, t))
        self.positionChanged.emit(max(0.0, t))

    def _show(self, source: str, src_t: float, t: float) -> None:
        if source != self._source:
            self._source = source
            self._pending, self._pending_t = src_t, t
            self.player.setSource(QUrl.fromLocalFile(source))
        else:
            self.player.setPosition(int(src_t * 1000))

    def toggle(self) -> None:
        if self.playing:
            self._want_play = False
            self.player.pause()
            return
        if not self.project or not self.project.clips:
            return
        if self.index < 0 or self.position() >= self.project.duration - 0.05:
            self.seek(0.0)
        self._want_play = True
        self.player.play()

    def pause(self) -> None:
        self._want_play = False
        self.player.pause()

    def _on_status(self, status) -> None:
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) \
                and self._pending is not None:
            if hasattr(self.player, "setActiveAudioTrack"):
                self.player.setActiveAudioTrack(0)   # track 1 = the mix (files with separate tracks)
            self.player.setPosition(int(self._pending * 1000))
            self._pending = None
            if self._want_play:
                self.player.play()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia and self._want_play:
            self._advance()

    def _on_position(self, ms: int) -> None:
        if not self.project or not 0 <= self.index < len(self.project.clips) or self._pending is not None:
            return
        c = self.project.clips[self.index]
        if ms / 1000 >= c.end - 0.02 and self._want_play:
            self._advance()
            return
        self.positionChanged.emit(self.position())

    def _advance(self) -> None:
        nxt = self.index + 1
        if nxt >= len(self.project.clips):
            self._want_play = False
            self.player.pause()
            self.positionChanged.emit(self.project.duration)
            return
        cur, c = self.project.clips[self.index], self.project.clips[nxt]
        self.index = nxt
        if c.source == cur.source and abs(c.start - cur.end) < 0.05:
            return  # the next clip carries straight on in the same file
        self._show(c.source, c.start, self.project.clip_offset(nxt))
        if self._want_play:
            self.player.play()


class EditorPage(QWidget):
    RESOLUTIONS = (("source", "Same as video"), ("1440p", "1440p"), ("1080p", "1080p"), ("720p", "720p"),
                   ("480p", "480p"))

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.cfg = win.cfg
        self.store = ProjectStore(Paths.PROJECTS)
        self.frames = FrameCache(Paths.DATA / "Cache", FFmpeg.locate(self.cfg.ffmpeg_path))
        self.project = EditProject()
        self.sig = _ExportSignals()
        self._exporting = False
        self._loading = False
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save)
        self._build()
        self._wire()
        self._open_latest()

    # ================================================================ UI

    def _build(self) -> None:
        title = QLabel("Video editor")
        title.setObjectName("PageTitle")
        crumb = QLabel("›")
        crumb.setObjectName("Crumb")
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("ProjectName")
        self.name_edit.setFixedWidth(260)
        self.name_edit.setToolTip("Project name - also the exported file's name")
        self.projects = QComboBox()
        self.projects.setFixedWidth(210)
        self.projects.setToolTip("Open another project")
        new_btn = IconTextButton("plus", "New project")
        new_btn.clicked.connect(self.new_project)
        self.delete_project_btn = IconButton("trash", "Delete this project (your videos are kept)")
        self.delete_project_btn.clicked.connect(self.delete_project)
        self.export_btn = IconTextButton("reel", "Export video", primary=True)
        self.export_btn.setToolTip("Render the timeline into one video in your clips folder")
        top = QHBoxLayout()
        for w in (title, crumb, self.name_edit):
            top.addWidget(w)
        top.addStretch()
        for w in (self.projects, new_btn, self.delete_project_btn):
            top.addWidget(w)
        top.addSpacing(12)
        top.addWidget(self.export_btn)

        # preview + transport
        self.video = QVideoWidget()
        self.video.setMinimumHeight(240)
        self.video.setStyleSheet("background: #000;")
        self.seq = SequencePlayer(self.video)
        self.play_btn = IconButton("play", "Play / pause (Space)", size=24, color=Palette.TEXT)
        self._play_icon = Icons.icon("play", Palette.TEXT, 24)
        self._pause_icon = Icons.icon("pause", Palette.TEXT, 24)
        self.prev_btn = IconButton("prev", "Previous clip", size=20, color=Palette.TEXT)
        self.next_btn = IconButton("next", "Next clip", size=20, color=Palette.TEXT)
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("Muted")
        volume_icon = QLabel()
        volume_icon.setPixmap(Icons.pixmap("volume", Palette.TEXT_MUTED, 18))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(100)
        transport = QHBoxLayout()
        transport.addWidget(volume_icon)
        transport.addWidget(self.volume)
        transport.addWidget(self.time_label)
        transport.addStretch()
        for w in (self.prev_btn, self.play_btn, self.next_btn):
            transport.addWidget(w)
        transport.addStretch()
        spacer = QWidget()
        spacer.setFixedWidth(self.volume.width() + 110)
        transport.addWidget(spacer)
        preview = QVBoxLayout()
        preview.setContentsMargins(0, 0, 0, 0)
        preview.addWidget(self.video, 1)
        preview.addLayout(transport)

        middle = QHBoxLayout()
        middle.setSpacing(12)
        middle.addWidget(self._build_side_panel())
        middle.addLayout(preview, 1)

        # timeline toolbar + timeline
        self.split_btn = IconButton("scissors", "Split the clip at the playhead (S)", size=18)
        self.dup_btn = IconButton("copy", "Duplicate the selected clip (Ctrl+D)", size=18)
        self.del_btn = IconButton("trash", "Remove the selected clip from the timeline (Delete)", size=18)
        self.clip_info = QLabel()
        self.clip_info.setObjectName("Muted")
        self.total_label = QLabel()
        self.total_label.setObjectName("Muted")
        tools = QHBoxLayout()
        tools.addWidget(self.clip_info)
        tools.addStretch()
        for b in (self.split_btn, self.dup_btn, self.del_btn):
            tools.addWidget(b)
        tools.addSpacing(10)
        tools.addWidget(self.total_label)
        self.timeline = EditTimeline(self.frames)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.status = QLabel()
        self.status.setWordWrap(True)
        status_row = QHBoxLayout()
        status_row.addWidget(self.progress, 1)
        status_row.addWidget(self.status, 2)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        root.addLayout(top)
        root.addLayout(middle, 1)
        root.addLayout(tools)
        root.addWidget(self.timeline)
        root.addLayout(status_row)

    def _build_side_panel(self) -> QWidget:
        self.side_tabs = SegmentedControl([("videos", "Project videos"), ("export", "Export settings")], compact=True)
        self.side_tabs.set_value("videos")
        self.side_stack = QStackedWidget()

        # --- project videos
        self.sources = QListWidget()
        self.sources.setIconSize(QSize(112, 63))
        self.sources.setViewMode(QListView.ViewMode.ListMode)
        self.sources.setWordWrap(True)
        self.sources.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sources.setToolTip("Double-click to add the whole video to the timeline")
        import_btn = IconTextButton("plus", "Import videos")
        import_btn.clicked.connect(self.import_videos)
        self.add_btn = IconTextButton("scissors", "Add to timeline")
        self.add_btn.setToolTip("Add the selected video to the end of the timeline")
        self.add_btn.clicked.connect(self._add_selected_source)
        buttons = QHBoxLayout()
        buttons.addWidget(import_btn)
        buttons.addWidget(self.add_btn)
        videos = QWidget()
        vl = QVBoxLayout(videos)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.sources, 1)
        vl.addLayout(buttons)
        hint = QLabel("Tip: in Sessions, tick highlights and press Edit to send them here.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        vl.addWidget(hint)

        # --- export settings
        self.res_combo = QComboBox()
        for value, label in self.RESOLUTIONS:
            self.res_combo.addItem(label, value)
        self.fps_combo = QComboBox()
        for fps in (30, 60, 120):
            self.fps_combo.addItem(f"{fps} FPS", fps)
        self.quality = QSpinBox()
        self.quality.setRange(14, 32)
        self.quality.setToolTip("Lower = better picture and bigger file (18-24 is sensible)")
        self.audio_switch = ToggleSwitch(True)
        form = QFormLayout()
        form.addRow("Resolution", self.res_combo)
        form.addRow("Frame rate", self.fps_combo)
        form.addRow("Quality", self.quality)
        form.addRow("Sound", self.audio_switch)
        where = QLabel("Exported videos go to your clips folder (Clips page).")
        where.setObjectName("Muted")
        where.setWordWrap(True)
        export = QWidget()
        el = QVBoxLayout(export)
        el.setContentsMargins(0, 6, 0, 0)
        el.addLayout(form)
        el.addWidget(where)
        el.addStretch()

        self.side_stack.addWidget(videos)
        self.side_stack.addWidget(export)
        panel = QWidget()
        panel.setFixedWidth(280)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(self.side_tabs)
        pl.addWidget(self.side_stack, 1)
        return panel

    def _wire(self) -> None:
        self.side_tabs.changed.connect(lambda v: self.side_stack.setCurrentIndex(0 if v == "videos" else 1))
        self.name_edit.editingFinished.connect(self._rename)
        self.projects.activated.connect(self._on_project_picked)
        self.export_btn.clicked.connect(self.export)
        self.sources.itemDoubleClicked.connect(lambda item: self.add_source_to_timeline(item.data(ROLE_PATH)))
        self.sources.customContextMenuRequested.connect(self._source_menu)
        self.sources.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.play_btn.clicked.connect(self.toggle_play)
        self.prev_btn.clicked.connect(lambda: self.jump_clip(-1))
        self.next_btn.clicked.connect(lambda: self.jump_clip(+1))
        self.volume.valueChanged.connect(lambda v: self.seq.audio.setVolume(v / 100))
        self.seq.positionChanged.connect(self._on_position)
        self.seq.playingChanged.connect(lambda on: self.play_btn.setIcon(self._pause_icon if on else self._play_icon))
        self.timeline.seekRequested.connect(self.seek)
        self.timeline.selectionChanged.connect(lambda _: self._update_buttons())
        self.timeline.edited.connect(self.changed)
        self.split_btn.clicked.connect(self.split)
        self.dup_btn.clicked.connect(self.duplicate)
        self.del_btn.clicked.connect(self.delete_clip)
        self.res_combo.currentIndexChanged.connect(lambda _: self._export_setting("resolution",
                                                                                  self.res_combo.currentData()))
        self.fps_combo.currentIndexChanged.connect(lambda _: self._export_setting("fps", self.fps_combo.currentData()))
        self.quality.valueChanged.connect(lambda v: self._export_setting("quality", v))
        self.audio_switch.toggled.connect(lambda on: self._export_setting("audio", on))
        self.sig.progress.connect(self._on_progress)
        self.sig.done.connect(self._on_done)

    # ================================================================ projects

    def _open_latest(self) -> None:
        projects = self.store.list()
        self.open_project(projects[0] if projects else EditProject(), save=False)

    def open_project(self, project: EditProject, save: bool = True) -> None:
        if save:
            self.save()
        self.seq.pause()
        self.project = project
        self._loading = True
        self.name_edit.setText(project.name)
        ex = project.export
        self.res_combo.setCurrentIndex(max(0, self.res_combo.findData(ex.resolution)))
        self.fps_combo.setCurrentIndex(max(0, self.fps_combo.findData(ex.fps)))
        self.quality.setValue(ex.quality)
        self.audio_switch.setChecked(ex.audio)
        self._loading = False
        self.seq.set_project(project)
        self.timeline.set_project(project)
        self._fill_sources()
        self._fill_projects()
        self.seek(0.0)
        missing = project.missing_sources()
        self.say(f"Missing {len(missing)} video(s) - they were moved or deleted: "
                  + ", ".join(Path(m).name for m in missing[:3]) if missing else "", ok=not missing)

    def new_project(self) -> None:
        self.open_project(EditProject(name=self._unique_name("New project")))
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def delete_project(self) -> None:
        answer = QMessageBox.question(self, "Delete project",
                                      f"Delete the project '{self.project.name}'?\n\n"
                                      "Only the edit is deleted - your recordings and exported videos are kept.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.save_timer.stop()
        self.store.delete(self.project.id)
        projects = self.store.list()
        self.open_project(projects[0] if projects else EditProject(), save=False)

    def _unique_name(self, base: str) -> str:
        names = {p.name for p in self.store.list()} | {self.project.name}
        name, n = base, 2
        while name in names:
            name, n = f"{base} {n}", n + 1
        return name

    def _fill_projects(self) -> None:
        self.projects.blockSignals(True)
        self.projects.clear()
        saved = self.store.list()
        if not any(p.id == self.project.id for p in saved):
            saved.insert(0, self.project)
        for p in saved:
            self.projects.addItem(p.name, p.id)
        self.projects.setCurrentIndex(max(0, self.projects.findData(self.project.id)))
        self.projects.blockSignals(False)

    def _on_project_picked(self, index: int) -> None:
        project_id = self.projects.itemData(index)
        if project_id and project_id != self.project.id:
            project = self.store.load(project_id)
            if project is not None:
                self.open_project(project)

    def _rename(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.name_edit.setText(self.project.name)
            return
        if name != self.project.name:
            self.project.name = name
            self.changed()
            self._fill_projects()

    def changed(self) -> None:
        """Something in the project changed: repaint, and save shortly."""
        self.timeline.update()
        self._update_labels()
        self._update_buttons()
        if not self._loading:
            self.save_timer.start(600)

    def save(self) -> None:
        self.save_timer.stop()
        if not self.project.sources and not self.project.clips and not self.store.exists(self.project.id):
            return  # don't litter the projects folder with empty ones
        try:
            self.store.save(self.project)
        except OSError as exc:
            log.error("Could not save the editor project: %s", exc)

    def _export_setting(self, field: str, value) -> None:
        if not self._loading and value is not None:
            setattr(self.project.export, field, value)
            self.changed()

    # ================================================================ project videos

    def _ffmpeg(self) -> FFmpeg | None:
        ffmpeg = self.win.engine.ffmpeg or FFmpeg.locate(self.cfg.ffmpeg_path)
        if ffmpeg is None:
            QMessageBox.warning(self, "GameCapture", "ffmpeg not found - run Tools\\get_ffmpeg.py")
        elif self.frames.ffmpeg is None:
            self.frames.ffmpeg = ffmpeg
        return ffmpeg

    def import_videos(self) -> None:
        start = str(self.win.output_dir if self.win.output_dir.exists() else Path.home())
        paths, _ = QFileDialog.getOpenFileNames(self, "Import videos", start, VIDEO_FILTER)
        added = [p for p in (self.add_source(Path(p)) for p in paths) if p]
        if added:
            self.say(f"Imported {len(added)} video{'s' if len(added) != 1 else ''} - double-click one to add it "
                      "to the timeline")

    def add_source(self, path: Path, label: str = "", markers: list[dict] | None = None) -> str | None:
        """Put a video in Project videos (no timeline change). Returns its path, or None if unreadable."""
        existing = self.project.source(str(path))
        if existing is not None:
            return existing.path
        ffmpeg = self._ffmpeg()
        if ffmpeg is None:
            return None
        duration = ffmpeg.duration(path) or 0.0
        if duration <= 0:
            self.say(f"Couldn't read {path.name} - is it a video?", ok=False)
            return None
        if markers is None:
            data = Sidecar.read(path) or {}
            markers = data.get("markers", [])
            label = label or (data.get("game") or {}).get("title") or (data.get("game") or {}).get("champion") or ""
        self.project.add_source(path, duration, label, markers)
        self._fill_sources()
        self.changed()
        return str(path)

    def add_source_to_timeline(self, path: str | None) -> None:
        src = self.project.source(path) if path else None
        if src is None:
            return
        clip = self.project.add_clip(src.path, 0.0, src.duration, src.name)
        if clip is not None:
            self._after_timeline_add(len(self.project.clips) - 1)

    def _add_selected_source(self) -> None:
        item = self.sources.currentItem()
        if item is not None:
            self.add_source_to_timeline(item.data(ROLE_PATH))

    def add_segments(self, entry: "RecordingEntry", segments: list["Segment"]) -> int:
        """From Sessions: the ticked highlights of a recording become clips at the end of the timeline."""
        if self.add_source(entry.video, entry.title, entry.markers) is None:
            return 0
        first = len(self.project.clips)
        for seg in segments:
            self.project.add_clip(entry.video, seg.start, seg.end, seg.label)
        added = len(self.project.clips) - first
        if added:
            self._after_timeline_add(first)
        return added

    def _after_timeline_add(self, index: int) -> None:
        self.timeline.set_selected(index)
        self.changed()
        self.seek(self.project.clip_offset(index))

    def _fill_sources(self) -> None:
        self.sources.clear()
        thumbs = self.win.thumbs
        for src in self.project.sources:
            path = Path(src.path)
            n = sum(1 for m in src.markers if m.get("involves_me"))
            info = Format.duration(src.duration) + (f"  ·  {n} highlight{'s' if n != 1 else ''}" if n else "")
            item = QListWidgetItem(f"{src.name}\n{info}")
            item.setData(ROLE_PATH, src.path)
            item.setToolTip(src.path)
            pm = thumbs.get(path, min(120.0, src.duration / 3)) if path.exists() else None
            item.setIcon(Icons.icon("reel", Palette.TEXT_MUTED, 40) if pm is None else QIcon(pm))
            if not path.exists():
                item.setForeground(Qt.GlobalColor.red)
                item.setText(f"{src.name}\nMissing - moved or deleted")
            self.sources.addItem(item)
        if not self.project.sources:
            empty = QListWidgetItem("No videos yet - press Import videos, or send highlights here from Sessions")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.sources.addItem(empty)
        self._update_buttons()

    def on_thumbnail(self, video: str) -> None:
        if any(s.path == video for s in self.project.sources):
            self._fill_sources()

    def _source_menu(self, pos) -> None:
        item = self.sources.itemAt(pos)
        path = item.data(ROLE_PATH) if item else None
        if not path:
            return
        menu = QMenu(self)
        menu.addAction("Add to timeline", lambda: self.add_source_to_timeline(path))
        menu.addAction("Rename...", lambda: self._rename_source(path))
        menu.addAction("Show in folder", lambda: Shell.reveal(Path(path)))
        menu.addSeparator()
        menu.addAction("Remove from project", lambda: self._remove_source(path))
        menu.exec(self.sources.mapToGlobal(pos))

    def _rename_source(self, path: str) -> None:
        src = self.project.source(path)
        if src is None:
            return
        name, ok = QInputDialog.getText(self, "Rename video", "Name in this project:", text=src.name)
        if ok and name.strip():
            src.label = name.strip()
            self._fill_sources()
            self.changed()

    def _remove_source(self, path: str) -> None:
        used = sum(1 for c in self.project.clips if c.source == path)
        if used:
            answer = QMessageBox.question(self, "Remove video",
                                          f"{used} clip(s) on the timeline come from this video. Remove them too?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.seq.unload()
        self.project.remove_source(path)
        self.timeline.set_selected(-1)
        self._fill_sources()
        self.changed()
        self.seek(0.0)

    # ================================================================ timeline editing

    @property
    def selected(self) -> int:
        i = self.timeline.selected
        return i if 0 <= i < len(self.project.clips) else -1

    def split(self) -> None:
        t = self.seq.position()
        if self.project.split(t):
            hit = self.project.locate(t)
            self.timeline.set_selected(hit[0] if hit else -1)
            self.changed()
        else:
            self.say("Move the playhead inside a clip (not right at its edge) to split it", ok=False)

    def duplicate(self) -> None:
        if self.selected >= 0:
            self.timeline.set_selected(self.project.duplicate(self.selected))
            self.changed()

    def delete_clip(self) -> None:
        i = self.selected
        if i < 0:
            return
        t = self.project.clip_offset(i)
        self.seq.pause()
        self.project.delete(i)
        self.timeline.set_selected(min(i, len(self.project.clips) - 1))
        self.changed()
        self.seek(min(t, self.project.duration))

    def seek(self, t: float) -> None:
        self.seq.seek(t)
        hit = self.project.locate(t)
        if hit is not None and self.selected < 0:
            self.timeline.set_selected(hit[0])
        self._update_labels()

    def toggle_play(self) -> None:
        self.seq.toggle()

    def jump_clip(self, direction: int) -> None:
        if not self.project.clips:
            return
        hit = self.project.locate(self.seq.position())
        i = (hit[0] if hit else 0) + direction
        if direction < 0 and hit and self.seq.position() - self.project.clip_offset(hit[0]) > 1.0:
            i = hit[0]  # like a music player: back = start of this clip first
        i = max(0, min(i, len(self.project.clips) - 1))
        self.timeline.set_selected(i)
        self.seek(self.project.clip_offset(i))
        self._update_buttons()

    def _on_position(self, t: float) -> None:
        self.timeline.set_position(t)
        self.time_label.setText(f"{Format.duration(t)} / {Format.duration(self.project.duration)}")

    def _update_labels(self) -> None:
        n = len(self.project.clips)
        self.total_label.setText(f"{n} clip{'s' if n != 1 else ''}  ·  {Format.duration(self.project.duration)}")
        i = self.selected
        if i >= 0:
            c = self.project.clips[i]
            self.clip_info.setText(f"{c.label or 'Clip'}  ·  {Format.duration(c.start)} - {Format.duration(c.end)}  "
                                   f"({Format.duration(c.duration)})")
        else:
            self.clip_info.setText("Drag clips to reorder them, drag their edges to trim")
        self.time_label.setText(f"{Format.duration(self.seq.position())} / {Format.duration(self.project.duration)}")

    def _update_buttons(self) -> None:
        has_clips = bool(self.project.clips)
        for b in (self.play_btn, self.prev_btn, self.next_btn, self.split_btn):
            b.setEnabled(has_clips)
        for b in (self.dup_btn, self.del_btn):
            b.setEnabled(self.selected >= 0)
        self.export_btn.setEnabled(has_clips and not self._exporting)
        item = self.sources.currentItem()
        self.add_btn.setEnabled(bool(item and item.data(ROLE_PATH)))
        self._update_labels()

    # ================================================================ export

    def export(self) -> None:
        if not self.project.clips or self._exporting:
            return
        ffmpeg = self._ffmpeg()
        if ffmpeg is None:
            return
        self.save()
        self.seq.pause()
        exporter = ProjectExporter(ffmpeg)
        out = self.win.clips_dir() / exporter.output_name(self.project)
        encoder = self.win.engine.encoder or "x264"
        project = EditProject.from_dict(self.project.to_dict())  # a snapshot: keep editing while it renders
        self._exporting = True
        self._update_buttons()
        self.progress.setValue(0)
        self.progress.setVisible(True)
        report = lambda frac, text: self.sig.progress.emit(int(frac * 100), text)  # noqa: E731

        def work():
            try:
                path = exporter.export(project, out, encoder, report)
                self.sig.done.emit(True, f"Saved {path.name}", path)
            except Exception as exc:
                log.error("Editor export failed: %s", exc)
                self.sig.done.emit(False, str(exc), None)
        threading.Thread(target=work, name="editor-export", daemon=True).start()

    def _on_progress(self, percent: int, text: str) -> None:
        self.progress.setValue(percent)
        self.say(text)

    def _on_done(self, ok: bool, text: str, path) -> None:
        self._exporting = False
        self.progress.setVisible(False)
        self.say(("Done: " if ok else "Export failed: ") + text, ok=ok)
        self._update_buttons()
        self.win.clips_changed()
        if ok and path is not None:
            Shell.reveal(path)

    def say(self, text: str, ok: bool = True) -> None:
        self.status.setText(text)
        self.status.setStyleSheet("" if ok else f"color: {Palette.LOSE};")

    # ================================================================ lifecycle

    def shortcut(self, key: str) -> None:
        """Keyboard shortcuts while this page is showing (wired by the main window)."""
        if self.name_edit.hasFocus():
            return
        {"Space": self.toggle_play, "S": self.split, "Delete": self.delete_clip, "Ctrl+D": self.duplicate,
         "Left": lambda: self.seek(max(0.0, self.seq.position() - 5)),
         "Right": lambda: self.seek(min(self.project.duration, self.seq.position() + 5)),
         "N": lambda: self.jump_clip(+1), "P": lambda: self.jump_clip(-1)}.get(key, lambda: None)()

    def release_file(self, video: Path) -> None:
        """Before a recording is deleted: let go of it if the preview has it open."""
        if self.seq._source == str(video):
            self.seq.unload()

    def shutdown(self) -> None:
        self.save()
        self.seq.unload()
        self.frames.shutdown()
