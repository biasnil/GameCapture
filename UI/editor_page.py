"""Video editor page (Outplayed-style): project videos on the left, preview in the middle,
a clip timeline underneath with split / duplicate / delete, and Export video.

Send highlights here from Sessions (Edit button), or import any video. Projects save themselves
to the app data folder as you edit."""
from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar, QSlider,
                             QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from Capture.ffmpeg import FFmpeg
from Core.editor import EditProject, ProjectExporter, ProjectStore
from Core.formatting import Format
from Core.paths import Paths
from Core.sidecar import Sidecar
from Theme.palette import Palette
from UI.dual_player import DualPlayer
from UI.edit_timeline import EditTimeline, TimelineView
from UI.elements import ElementOverlay, ElementRenderer, ElementsPanel
from UI.frames import FrameCache
from UI.icons import Icons
from UI.shell import Shell
from UI.widgets import IconButton, IconTextButton, InfoTip, SegmentedControl, ToggleSwitch

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
    """Plays the timeline clip after clip, switching source files as needed.

    A few seconds before a clip ends, the next one is prepared in a hidden second player
    (UI/dual_player.py) when it starts somewhere else - so cuts play without a hitch."""
    positionChanged = pyqtSignal(float)    # timeline seconds
    playingChanged = pyqtSignal(bool)
    PRELOAD_S = 4.0                         # prepare the next clip this long before the cut

    def __init__(self, allowed=lambda: True) -> None:
        super().__init__()
        self.dual = DualPlayer(allowed)
        self.view = self.dual.view
        self.project: EditProject | None = None
        self.index = -1
        self._want_play = False
        self.dual.positionChanged.connect(self._on_position)
        self.dual.mediaStatusChanged.connect(self._on_status)
        self.dual.playbackStateChanged.connect(
            lambda s: self.playingChanged.emit(s == QMediaPlayer.PlaybackState.PlayingState))

    @property
    def playing(self) -> bool:
        return self.dual.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def set_project(self, project: EditProject | None) -> None:
        self.project = project
        self.unload()

    def unload(self) -> None:
        self.dual.unload()
        self.index = -1

    def holds(self, path: str) -> bool:
        return self.index >= 0 and self.dual.path == path

    def position(self) -> float:
        if not self.project or not 0 <= self.index < len(self.project.clips):
            return 0.0
        c = self.project.clips[self.index]
        inside = min(max(self.dual.position() / 1000 - c.start, 0.0), c.duration)
        return self.project.clip_offset(self.index) + inside

    def seek(self, t: float) -> None:
        hit = self.project.locate(t) if self.project else None
        if hit is None:
            self.unload()
            self.positionChanged.emit(0.0)
            return
        self.index, src_t = hit
        self._show(self.project.clips[self.index].source, src_t)
        self.positionChanged.emit(max(0.0, t))

    def _show(self, source: str, src_t: float) -> None:
        """Go to a spot: instant if it was prepared, else open / seek the file."""
        if self.dual.take(source, src_t, play=self._want_play):
            return
        if source != self.dual.path:
            self.dual.load(source, at=src_t)
        else:
            self.dual.setPosition(int(src_t * 1000))
        if self._want_play:
            self.dual.play()

    def toggle(self) -> None:
        if self.playing:
            self.pause()
            return
        if not self.project or not self.project.clips:
            return
        if self.index < 0 or self.position() >= self.project.duration - 0.05:
            self.seek(0.0)
        self._want_play = True
        self.dual.play()

    def pause(self) -> None:
        self._want_play = False
        self.dual.pause()
        self.dual.drop_standby()

    def _on_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._want_play:
            self._advance()

    def _on_position(self, ms: int) -> None:
        if not self.project or not 0 <= self.index < len(self.project.clips) or self.dual.loading:
            return
        c = self.project.clips[self.index]
        if self._want_play:
            if ms / 1000 >= c.end - 0.02:
                self._advance()
                return
            if c.end - ms / 1000 < self.PRELOAD_S:
                self._prepare(self.index + 1)
        self.positionChanged.emit(self.position())

    def _jumps(self, nxt: int) -> bool:
        """Does clip `nxt` start somewhere other than where clip nxt-1 ends?"""
        cur, c = self.project.clips[nxt - 1], self.project.clips[nxt]
        return not (c.source == cur.source and abs(c.start - cur.end) < 0.05)

    def _prepare(self, nxt: int) -> None:
        if 0 < nxt < len(self.project.clips) and self._jumps(nxt):
            c = self.project.clips[nxt]
            self.dual.prepare(c.source, c.start)

    def _advance(self) -> None:
        nxt = self.index + 1
        if nxt >= len(self.project.clips):
            self._want_play = False
            self.dual.pause()
            self.positionChanged.emit(self.project.duration)
            return
        jumps = self._jumps(nxt)
        self.index = nxt
        if jumps:
            c = self.project.clips[nxt]
            self._show(c.source, c.start)


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
        self._undo: list[dict] = []          # project snapshots before each edit
        self._redo: list[dict] = []
        self._drag_snapshot: dict | None = None
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
        self.seq = SequencePlayer(allowed=self.win.preload_allowed)
        self.video = self.seq.view
        self.video.setMinimumHeight(240)
        self.overlay = ElementOverlay(self.video, self.win)   # text / images over the preview
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
        self.undo_btn = IconButton("undo", "Undo (Ctrl+Z)", size=18)
        self.redo_btn = IconButton("redo", "Redo (Ctrl+Y)", size=18)
        self.hl_btn = IconButton("bolt", "Keep only the highlights of the selected clip - one clip each, "
                                         "the parts in between are cut out (H)", size=18)
        self.split_btn = IconButton("scissors", "Split the clip at the playhead (S)", size=18)
        self.dup_btn = IconButton("copy", "Duplicate the selected clip (Ctrl+D)", size=18)
        self.del_btn = IconButton("trash", "Remove the selected clip from the timeline (Delete)", size=18)
        self.clip_info = QLabel()
        self.clip_info.setObjectName("Muted")
        self.total_label = QLabel()
        self.total_label.setObjectName("Muted")
        self.zoom_out_btn = IconButton("zoom_out", "Zoom out (Ctrl + mouse wheel)", size=18)
        self.zoom_in_btn = IconButton("zoom_in", "Zoom in (Ctrl + mouse wheel)", size=18)
        self.zoom_fit_btn = IconButton("zoom_fit", "Fit the whole timeline", size=18)
        tools = QHBoxLayout()
        tools.addWidget(self.clip_info)
        tools.addWidget(InfoTip("Click a clip to select it and jump there. Drag a clip to move it, drag its edges to "
                                "trim. Deleting or trimming closes the gap.<br><br>The thin track above the clips "
                                "holds text and images.<br><br>Ctrl + mouse wheel zooms, the wheel scrolls."))
        tools.addStretch()
        for b in (self.zoom_out_btn, self.zoom_fit_btn, self.zoom_in_btn):
            tools.addWidget(b)
        tools.addSpacing(14)
        for b in (self.undo_btn, self.redo_btn):
            tools.addWidget(b)
        tools.addSpacing(14)
        for b in (self.hl_btn, self.split_btn, self.dup_btn, self.del_btn):
            tools.addWidget(b)
        tools.addSpacing(10)
        tools.addWidget(self.total_label)
        self.timeline = EditTimeline(self.frames)
        self.timeline_view = TimelineView(self.timeline)

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
        root.addWidget(self.timeline_view)
        root.addLayout(status_row)

    def _build_side_panel(self) -> QWidget:
        self.side_tabs = SegmentedControl([("videos", "Videos"), ("elements", "Elements"), ("export", "Export")],
                                          compact=True)
        self.side_tabs.set_value("videos")
        self.side_stack = QStackedWidget()

        # --- project videos
        self.sources = QListWidget()
        self.sources.setIconSize(QSize(112, 63))
        self.sources.setViewMode(QListView.ViewMode.ListMode)
        self.sources.setWordWrap(True)
        self.sources.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sources.setToolTip("Double-click: add its highlights as clips (or the whole video if it has none)")
        import_btn = IconTextButton("plus", "Import videos")
        import_btn.clicked.connect(self.import_videos)
        self.add_hl_btn = IconTextButton("bolt", "Highlights")
        self.add_hl_btn.setToolTip("Each highlight of the selected video becomes its own clip on the timeline")
        self.add_hl_btn.clicked.connect(lambda: self._add_selected_source(highlights=True))
        self.add_btn = IconTextButton("reel", "Whole video")
        self.add_btn.setToolTip("Add the selected video to the end of the timeline as one clip")
        self.add_btn.clicked.connect(lambda: self._add_selected_source(highlights=False))
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_hl_btn)
        buttons.addWidget(self.add_btn)
        videos = QWidget()
        vl = QVBoxLayout(videos)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.sources, 1)
        import_row = QHBoxLayout()
        import_row.addWidget(import_btn, 1)
        import_row.addWidget(InfoTip("Double-click a video to put each of its highlights on the timeline as its "
                                     "own clip. <b>Whole video</b> adds it as one clip.<br><br>From Sessions: tick "
                                     "highlights and press <b>Edit</b>."))
        vl.addLayout(import_row)
        vl.addLayout(buttons)


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
        export = QWidget()
        el = QVBoxLayout(export)
        el.setContentsMargins(0, 6, 0, 0)
        el.addLayout(form)
        where = QLabel("Saves to your clips folder")
        where.setObjectName("Muted")
        el.addWidget(where)
        el.addStretch()

        self.elements_panel = ElementsPanel(self)
        self.side_stack.addWidget(videos)
        self.side_stack.addWidget(self.elements_panel)
        self.side_stack.addWidget(export)
        panel = QWidget()
        panel.setFixedWidth(280)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(self.side_tabs)
        pl.addWidget(self.side_stack, 1)
        return panel

    def _wire(self) -> None:
        self.side_tabs.changed.connect(self.show_tab)
        self.zoom_in_btn.clicked.connect(lambda: self.timeline_view.zoom_by(1.5))
        self.zoom_out_btn.clicked.connect(lambda: self.timeline_view.zoom_by(1 / 1.5))
        self.zoom_fit_btn.clicked.connect(lambda: self.timeline_view.set_zoom(1.0))
        self.timeline.elementSelected.connect(self.select_element)
        self.overlay.selected.connect(self.select_element)
        self.overlay.editStarted.connect(self._begin_drag_edit)
        self.overlay.edited.connect(lambda: (self._end_drag_edit(), self.elements_changed()))
        self.win.installEventFilter(self)
        self.video.installEventFilter(self)
        self.name_edit.editingFinished.connect(self._rename)
        self.projects.activated.connect(self._on_project_picked)
        self.export_btn.clicked.connect(self.export)
        self.sources.itemDoubleClicked.connect(lambda item: self.add_source_to_timeline(item.data(ROLE_PATH),
                                                                                        highlights=True))
        self.sources.customContextMenuRequested.connect(self._source_menu)
        self.sources.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.play_btn.clicked.connect(self.toggle_play)
        self.prev_btn.clicked.connect(lambda: self.jump_clip(-1))
        self.next_btn.clicked.connect(lambda: self.jump_clip(+1))
        self.volume.valueChanged.connect(lambda v: self.seq.dual.set_volume(v / 100))
        self.seq.positionChanged.connect(self._on_position)
        self.seq.playingChanged.connect(lambda on: self.play_btn.setIcon(self._pause_icon if on else self._play_icon))
        self.timeline.seekRequested.connect(self.seek)
        self.timeline.selectionChanged.connect(lambda _: self._update_buttons())
        self.timeline.editStarted.connect(self._begin_drag_edit)
        self.timeline.edited.connect(self._end_drag_edit)
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn.clicked.connect(self.redo)
        self.hl_btn.clicked.connect(self.keep_highlights)
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

    # ================================================================ undo / redo

    HISTORY = 100

    def checkpoint(self) -> None:
        """Call before changing the project: remembers it for Undo."""
        self._once_key = None
        self._undo.append(self.project.to_dict())
        del self._undo[:-self.HISTORY]
        self._redo.clear()

    def checkpoint_once(self, key: str) -> None:
        """One Undo step for a run of small edits to the same thing (a slider drag, typing)."""
        if key != getattr(self, "_once_key", None):
            self.checkpoint()
            self._once_key = key

    def _begin_drag_edit(self) -> None:
        self._drag_snapshot = self.project.to_dict()

    def _end_drag_edit(self) -> None:
        if self._drag_snapshot is not None:
            self._undo.append(self._drag_snapshot)
            del self._undo[:-self.HISTORY]
            self._redo.clear()
            self._drag_snapshot = None
        self.changed()

    def undo(self) -> None:
        self._restore(self._undo, self._redo, "Undone")

    def redo(self) -> None:
        self._restore(self._redo, self._undo, "Redone")

    def _restore(self, source: list[dict], target: list[dict], word: str) -> None:
        if not source:
            return
        t = self.seq.position()
        selected = self.selected
        target.append(self.project.to_dict())
        self.seq.pause()
        self.project = EditProject.from_dict(source.pop())
        self.seq.set_project(self.project)
        self.timeline.set_project(self.project)
        self.timeline.set_selected(min(selected, len(self.project.clips) - 1))
        element = self.overlay.current
        self.overlay.set_project(self.project)
        self.select_element(element if self.project.element(element) else "")
        self._fill_sources()
        self.changed()
        self.seek(min(t, self.project.duration))
        self.say(word)

    def open_project(self, project: EditProject, save: bool = True) -> None:
        if save:
            self.save()
        self.seq.pause()
        self.project = project
        self._undo.clear()
        self._redo.clear()
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
        self.overlay.set_project(project)
        self.select_element("")
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
        if paths:
            self.checkpoint()
        added = [p for p in (self.add_source(Path(p)) for p in paths) if p]
        if added:
            self.say(f"Imported {len(added)} video{'s' if len(added) != 1 else ''} - double-click one to put "
                     "its highlights on the timeline")

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
        data = Sidecar.read(path) or {}
        game = data.get("game") or {}
        if markers is None:
            markers = data.get("markers", [])
            label = label or game.get("title") or game.get("champion") or ""
        self.project.add_source(path, duration, label, markers, game.get("cuts", []))
        self._fill_sources()
        self.changed()
        return str(path)

    def add_source_to_timeline(self, path: str | None, highlights: bool = False) -> None:
        """Whole video as one clip, or (highlights=True) each highlight as its own clip -
        falling back to the whole video when it has no highlights."""
        src = self.project.source(path) if path else None
        if src is None:
            return
        self.checkpoint()
        first = len(self.project.clips)
        if highlights:
            n = self.project.add_highlights(src.path, *self._padding())
            if n:
                self._after_timeline_add(first)
                self.say(f"Added {n} highlight clip{'s' if n != 1 else ''} from {src.name}")
                return
        if self.project.add_clip(src.path, 0.0, src.duration, src.name) is not None:
            self._after_timeline_add(first)
        else:
            self._undo.pop()

    def _add_selected_source(self, highlights: bool) -> None:
        item = self.sources.currentItem()
        if item is not None:
            self.add_source_to_timeline(item.data(ROLE_PATH), highlights=highlights)

    def _padding(self) -> tuple[float, float]:
        return self.cfg.clips.pre_seconds, self.cfg.clips.post_seconds

    def add_segments(self, entry: "RecordingEntry", segments: list["Segment"]) -> int:
        """From Sessions: the ticked highlights of a recording become clips at the end of the timeline."""
        self.checkpoint()
        if self.add_source(entry.video, entry.title, entry.markers) is None:
            self._undo.pop()
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
        menu.addAction("Add highlights as clips", lambda: self.add_source_to_timeline(path, highlights=True))
        menu.addAction("Add whole video", lambda: self.add_source_to_timeline(path))
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
            self.checkpoint()
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
        self.checkpoint()
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
        self.checkpoint()
        if self.project.split(t):
            hit = self.project.locate(t)
            self.timeline.set_selected(hit[0] if hit else -1)
            self.changed()
        else:
            self._undo.pop()
            self.say("Move the playhead inside a clip (not right at its edge) to split it", ok=False)

    def keep_highlights(self) -> None:
        """Ripple edit: the selected clip becomes one clip per highlight; the rest is cut out."""
        i = self.selected
        if i < 0:
            self.say("Select a clip first", ok=False)
            return
        self.checkpoint()
        n = self.project.keep_highlights(i, *self._padding())
        if not n:
            self._undo.pop()
            self.say("No highlights in this clip", ok=False)
            return
        self.timeline.set_selected(i)
        self.changed()
        self.seek(self.project.clip_offset(i))
        self.say(f"Kept {n} highlight clip{'s' if n != 1 else ''} - Ctrl+Z to undo")

    def duplicate(self) -> None:
        if self.selected >= 0:
            self.checkpoint()
            self.timeline.set_selected(self.project.duplicate(self.selected))
            self.changed()

    def delete_clip(self) -> None:
        i = self.selected
        if i < 0:
            return
        t = self.project.clip_offset(i)
        self.checkpoint()
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
        size = self.seq.dual.video_size()
        if size.isValid() and size != self.overlay.video_size:
            self.overlay.video_size = size
            self.overlay.update()
        self.overlay.set_position(t)
        if self.seq.playing:
            self.timeline_view.follow()
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
            self.clip_info.setText("No clip selected")
        self.time_label.setText(f"{Format.duration(self.seq.position())} / {Format.duration(self.project.duration)}")

    def _update_buttons(self) -> None:
        has_clips = bool(self.project.clips)
        for b in (self.play_btn, self.prev_btn, self.next_btn, self.split_btn):
            b.setEnabled(has_clips)
        for b in (self.dup_btn, self.del_btn, self.hl_btn):
            b.setEnabled(self.selected >= 0)
        self.undo_btn.setEnabled(bool(self._undo))
        self.redo_btn.setEnabled(bool(self._redo))
        self.export_btn.setEnabled(has_clips and not self._exporting)
        item = self.sources.currentItem()
        for b in (self.add_btn, self.add_hl_btn):
            b.setEnabled(bool(item and item.data(ROLE_PATH)))
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
            folder = ElementRenderer.temp_folder()
            try:
                path = exporter.export(project, out, encoder, report,
                                       render_overlays=lambda w, h: ElementRenderer.overlays(
                                           project, w, h, folder, project.export.fps))
                self.sig.done.emit(True, f"Saved {path.name}", path)
            except Exception as exc:
                log.error("Editor export failed: %s", exc)
                self.sig.done.emit(False, str(exc), None)
            finally:
                shutil.rmtree(folder, ignore_errors=True)
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
        {"Space": self.toggle_play, "S": self.split, "Delete": self._delete, "Ctrl+D": self.duplicate,
         "H": self.keep_highlights, "Ctrl+Z": self.undo, "Ctrl+Y": self.redo, "Ctrl+Shift+Z": self.redo,
         "Left": lambda: self.seek(max(0.0, self.seq.position() - 5)),
         "Right": lambda: self.seek(min(self.project.duration, self.seq.position() + 5)),
         "N": lambda: self.jump_clip(+1), "P": lambda: self.jump_clip(-1)}.get(key, lambda: None)()

    def _delete(self) -> None:
        """Delete key: the selected text / image if there is one, else the selected clip."""
        if self.overlay.current:
            self.elements_panel.delete()
        else:
            self.delete_clip()

    # ================================================================ elements

    def show_tab(self, tab: str) -> None:
        self.side_tabs.set_value(tab)
        self.side_stack.setCurrentIndex({"videos": 0, "elements": 1, "export": 2}[tab])

    def select_element(self, element_id: str) -> None:
        self.overlay.select(element_id)
        self.timeline.set_selected_element(element_id)
        self.elements_panel.refresh()
        if element_id:
            self.show_tab("elements")
        self.overlay.refresh()

    def elements_changed(self, rebuild: bool = True) -> None:
        self.timeline.update()
        self.overlay.refresh()
        if rebuild:
            self.elements_panel.refresh()
        self.changed()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.overlay.wanted = True
        self.overlay.refresh()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.overlay.wanted = False
        self.overlay.refresh()

    def eventFilter(self, obj, event) -> bool:
        """Keep the elements layer glued to the preview when the window moves, resizes or minimises."""
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide,
                            QEvent.Type.WindowStateChange):
            self.overlay.refresh()
        return False

    def release_file(self, video: Path) -> None:
        """Before a recording is deleted: let go of it if the preview has it open."""
        if self.seq.holds(str(video)) or self.seq.dual.holds(str(video)):
            self.seq.unload()

    def shutdown(self) -> None:
        self.overlay.wanted = False
        self.overlay.hide()
        self.save()
        self.seq.unload()
        self.frames.shutdown()
