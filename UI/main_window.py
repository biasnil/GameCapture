"""Main window: icon sidebar + pages, live status header, tray icon, engine lifecycle."""
from __future__ import annotations

import logging
import os
from datetime import datetime
import sys
import threading
from pathlib import Path

from PyQt6.QtCore import QFile, QFileSystemWatcher, QObject, QProcess, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
                             QMessageBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from Capture.ffmpeg import FFmpeg
from Core.config import AppConfig
from Core.engine import Engine
from Core.formatting import Format
from Core.library import RecordingEntry, RecordingLibrary
from Core.storage import StorageManager
from Theme.palette import Palette
from UI.clips_page import ClipsPage
from UI.favorites_page import FavoritesPage
from UI.icons import Icons
from UI.log_page import LogPage
from UI.sessions_page import SessionsPage
from UI.settings_page import SettingsPage
from UI.shell import Shell
from UI.thumbs import ThumbnailCache
from Games.registry import RecordingModes
from UI.widgets import IconTextButton, NavButton, SegmentedControl

log = logging.getLogger("gamecapture.ui")


class _EngineSignals(QObject):
    ready = pyqtSignal(bool)
    quit_requested = pyqtSignal()
    video_plan = pyqtSignal()


class MainWindow(QMainWindow):
    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.engine = Engine(cfg)
        self.sig = _EngineSignals()
        self.output_dir = cfg.recording.resolved_output_dir()
        self.library = RecordingLibrary(self.output_dir)
        self.thumbs = ThumbnailCache(self.output_dir / ".thumbs", FFmpeg.locate(cfg.ffmpeg_path))
        self.entries: list[RecordingEntry] = []
        self._engine_started = False
        self._quitting = False
        self._was_recording = False
        self._state = ""
        self._tray_hint_shown = False
        self._was_capturing = False
        self._notify_saved_pending = False

        self.setWindowTitle("GameCapture")
        self.setWindowIcon(Icons.app_icon())
        self.resize(1360, 860)
        self._build_ui()
        self._build_tray()
        self._wire()
        self.log_page.attach()

        self.refresh_library()
        self._set_state("offline", "Starting engine...")
        threading.Thread(target=self._start_engine, name="engine-start", daemon=True).start()

    # ================================================================ UI

    def _build_ui(self) -> None:
        self.stack = QStackedWidget()
        self.sessions = SessionsPage(self)
        self.favorites = FavoritesPage()
        self.clips = ClipsPage(self)
        self.settings = SettingsPage(self)
        self.log_page = LogPage()

        content = QWidget()
        content.setObjectName("Content")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addLayout(self._build_header())
        cl.addWidget(self.stack, 1)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(content, 1)
        self.setCentralWidget(central)
        self.show_page(self.sessions)

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(68)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(10, 14, 10, 14)
        side.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(Icons.app_pixmap(38))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setToolTip("GameCapture")
        logo.setStyleSheet("background: transparent;")
        side.addWidget(logo)
        side.addSpacing(10)

        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self._nav_buttons: dict[QWidget, NavButton] = {}
        pages = (("nav_sessions", "Sessions", self.sessions), ("nav_favorites", "Favorites", self.favorites),
                 ("nav_clips", "Clips", self.clips), ("nav_log", "Log", self.log_page),
                 ("nav_settings", "Settings", self.settings))
        for icon, tip, page in pages:
            if page is self.log_page:
                side.addStretch()
            btn = NavButton(icon, tip)
            btn.clicked.connect(lambda _=False, p=page: self.show_page(p))
            self.nav.addButton(btn)
            self._nav_buttons[page] = btn
            self.stack.addWidget(page)
            side.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        return sidebar

    def _build_header(self) -> QHBoxLayout:
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(12, 12)
        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-weight: 600;")
        self.rec_btn = IconTextButton("record", "Record", color=Palette.REC_TEXT)
        self.rec_btn.setObjectName("Rec")
        self.rec_btn.setEnabled(False)
        self.rec_btn.setMinimumWidth(110)
        self.rec_btn.setToolTip(f"Manual record ({Format.hotkey(self.cfg.hotkeys.toggle)})")
        self._rec_icon = Icons.icon("record", Palette.REC_TEXT, 16, hover=None)
        self._stop_icon = Icons.icon("stop", Palette.REC_TEXT, 16, hover=None)
        self.bookmark_btn = IconTextButton("bookmark", "Bookmark")
        self.bookmark_btn.setToolTip(f"Mark this moment as a highlight ({Format.hotkey(self.cfg.hotkeys.bookmark)}) "
                                     "- works in every game")
        self.bookmark_btn.setEnabled(False)
        self.bookmark_btn.clicked.connect(self._bookmark)
        folder_btn = IconTextButton("folder", "Recordings")
        folder_btn.clicked.connect(lambda: Shell.open_path(self.output_dir))
        self.mode_switch = SegmentedControl(list(RecordingModes.OPTIONS), compact=True)
        self.mode_switch.set_tooltips(RecordingModes.TIPS)
        self.mode_switch.set_value(self.cfg.game("league").mode)
        self.mode_switch.changed.connect(self.set_mode)
        mode_label = QLabel("Mode")
        mode_label.setObjectName("Muted")
        header = QHBoxLayout()
        header.setContentsMargins(16, 10, 16, 0)
        header.setSpacing(8)
        header.addWidget(self.status_dot)
        header.addWidget(self.status_label)
        header.addStretch()
        header.addWidget(mode_label)
        header.addWidget(self.mode_switch)
        header.addSpacing(10)
        header.addWidget(folder_btn)
        header.addWidget(self.bookmark_btn)
        header.addWidget(self.rec_btn)
        return header

    def _build_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(Icons.status_icon(Palette.STATE["offline"]), self)
        menu = QMenu()
        show_act = QAction(Icons.icon("nav_sessions", Palette.TEXT, 16), "Show GameCapture", self)
        show_act.triggered.connect(self._show_window)
        self.tray_rec_act = QAction(Icons.icon("record", Palette.REC, 16), "Start recording", self)
        self.tray_rec_act.triggered.connect(self._toggle_recording)
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self.quit_app)
        for a in (show_act, self.tray_rec_act):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def _wire(self) -> None:
        self.sig.ready.connect(self._on_engine_ready)
        self.sig.quit_requested.connect(self.quit_app)
        self.sig.video_plan.connect(self.settings.on_video_plan)
        self.rec_btn.clicked.connect(self._toggle_recording)
        self.favorites.openRequested.connect(self._open_favorite)

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start(500)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.refresh_library)
        self.fs_watcher = QFileSystemWatcher([str(self.output_dir)], self)
        self.fs_watcher.directoryChanged.connect(lambda _: self.refresh_timer.start(1500))

        p = self.sessions.player
        for key, fn in (("Space", p.toggle_play), ("Left", lambda: p.skip(-5)), ("Right", lambda: p.skip(5)),
                        ("N", lambda: p.jump_highlight(+1)), ("P", lambda: p.jump_highlight(-1))):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda fn=fn: fn() if self.stack.currentWidget() is self.sessions else None)

    def show_page(self, page: QWidget) -> None:
        if self.stack.currentWidget() is self.settings and page is not self.settings:
            self.enforce_storage()  # a new limit takes effect when you leave Settings
        self.stack.setCurrentWidget(page)
        self._nav_buttons[page].setChecked(True)
        if page is self.clips:
            self.clips.refresh()
        elif page is self.favorites:
            self.favorites.refresh(self.entries)
        elif page is self.settings:
            self.settings.load()

    # ================================================================ library

    def clips_dir(self) -> Path:
        return self.cfg.clips.resolved_dir(self.output_dir)

    def refresh_library(self) -> None:
        rec = self.engine.recorder
        entries = self.library.scan(exclude=rec.busy_files if rec else ())
        cur = self.sessions.current
        if cur is not None:  # keep the open entry's object so edits (favourites) stay in sync
            entries = [cur if e.video == cur.video else e for e in entries]
        self.entries = entries
        self.sessions.set_entries(entries)
        if self._notify_saved_pending:
            self._notify_saved_pending = False
            QTimer.singleShot(0, lambda: self.enforce_storage(ask=False))  # a new recording landed
            newest = entries[0] if entries else None
            if newest is not None and (datetime.now() - newest.when).total_seconds() < 180:
                self._notify_saved(newest)
            elif self.cfg.game("league").mode == "highlights":
                self._notify("Match over", "No highlights this time - nothing was kept")
        if self.stack.currentWidget() is self.favorites:
            self.favorites.refresh(entries)

    # ---------- storage limit ----------

    def storage_manager(self) -> StorageManager:
        return StorageManager(self.output_dir, self.clips_dir(), self.cfg.storage)

    def storage_exclusions(self) -> set[Path]:
        """Never auto-delete what's being recorded/converted or what's open in the player."""
        rec = self.engine.recorder
        busy = set(rec.busy_files) if rec else set()
        if self.sessions.current is not None:
            busy.add(self.sessions.current.video)
        return busy

    def enforce_storage(self, ask: bool = True) -> None:
        """Apply the auto-delete limit. ask=False only right after a new recording is saved
        (that's the point of the limit); any other time you confirm the list first."""
        if not self.cfg.storage.limit_enabled:
            return
        rec = self.engine.recorder
        if rec is not None and (rec.is_recording or rec.is_finalizing):
            return  # never while a recording is being written; runs again after it's saved
        manager = self.storage_manager()
        exclude = self.storage_exclusions()
        victims = manager.plan_cleanup(exclude)
        if not victims:
            return
        if ask:
            size = sum(manager.entry_bytes(e) for e in victims) / 1024 ** 3
            names = "\n".join(f"  {e.title} - {e.when:%d %b %H:%M} ({e.size_mb / 1024:.1f} GB)" for e in victims[:12])
            more = f"\n  ... and {len(victims) - 12} more" if len(victims) > 12 else ""
            answer = QMessageBox.question(
                self, "Storage limit",
                f"Recordings are over your {self.cfg.storage.limit_gb:g} GB limit.\n\n"
                f"Permanently delete these {len(victims)} oldest recording(s) ({size:.1f} GB)?\n\n{names}{more}\n\n"
                "Choose No to keep them - you can raise the limit in Settings > Storage.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        deleted = manager.enforce(exclude, self.output_dir / ".thumbs")
        if deleted:
            for video in deleted:
                self.thumbs.forget(video)
            self._notify("Storage limit", f"Deleted {len(deleted)} old recording{'s' if len(deleted) != 1 else ''} "
                                          f"to stay under {self.cfg.storage.limit_gb:g} GB")
            self.refresh_library()

    def favorites_changed(self) -> None:
        self.favorites.refresh(self.entries)

    def clips_changed(self) -> None:
        if self.stack.currentWidget() is self.clips:
            self.clips.refresh()

    def _open_favorite(self, video: str, video_time: float) -> None:
        self.show_page(self.sessions)
        self.sessions.open_at(video, video_time)

    def delete_entry(self, entry: RecordingEntry) -> None:
        answer = QMessageBox.question(self, "Delete recording",
                                      f"Move this recording to the Recycle Bin?\n\n{entry.video.name}")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.sessions.current is entry:
            self.sessions.clear()
        for path in (entry.video, entry.video.with_suffix(".json")):
            if path.exists():
                result = QFile.moveToTrash(str(path))
                if not (result[0] if isinstance(result, tuple) else result):
                    log.error("Could not move %s to the Recycle Bin", path.name)
        self.thumbs.forget(entry.video)
        self.refresh_library()

    # ================================================================ engine + status

    def _start_engine(self) -> None:
        self.sig.ready.emit(self.engine.start(on_quit=self.sig.quit_requested.emit))

    def _on_engine_ready(self, ok: bool) -> None:
        self._engine_started = True
        if ok and self.thumbs.ffmpeg is None:
            self.thumbs.ffmpeg = self.engine.ffmpeg
        if not ok:
            self.show_page(self.log_page)
        self._update_status()
        self.enforce_storage()

    def _update_status(self) -> None:
        if not self._engine_started:
            return
        self.engine.tick()
        st = self.engine.status()
        if not st["ready"]:
            self._set_state("offline", "Recorder unavailable - see Log")
            self.rec_btn.setEnabled(False)
            return
        self.rec_btn.setEnabled(True)
        match = f"{st['champion']} · {st['kda']} · {st['highlights']} highlights" if st["in_match"] else ""
        if st["session_matches"] is not None:
            played = f"League session · {st['session_matches']} match{'es' if st['session_matches'] != 1 else ''} done"
            match = f"{played}   ·   {match}" if match else played
        if st["finalizing"]:
            self._set_state("match", "Saving recording...")
        elif st["recording"]:
            self._set_state("recording", f"REC {Format.duration(st['elapsed'])}" + (f"   ·   {match}" if match else ""))
        elif st["in_match"]:
            self._set_state("match", f"In match, not recording   ·   {match}")
        elif st["error"]:
            self._set_state("match", f"Recording failed to start - see Log  ({st['error'][:90]})")
        else:
            self._set_state("ready", "Ready · waiting for a match" if st["auto"]
                            else f"Ready · auto-record off ({Format.hotkey(self.cfg.hotkeys.toggle)} to record)")
        self.rec_btn.setText("Stop" if st["recording"] else "Record")
        self.bookmark_btn.setEnabled(st["recording"])
        if st["recording"] and st["bookmarks"]:
            self.status_label.setText(f"{self.status_label.text()}   ·   {st['bookmarks']} bookmark"
                                      f"{'s' if st['bookmarks'] != 1 else ''}")
        self.rec_btn.setIcon(self._stop_icon if st["recording"] else self._rec_icon)
        if self.tray:
            self.tray_rec_act.setText("Stop recording" if st["recording"] else "Start recording")
        busy = st["recording"] or st["finalizing"]
        if self._was_recording and not busy:
            self._notify_saved_pending = self.cfg.gui.notify_saved
            self.refresh_timer.start(500)  # the finished file (and its highlights) just landed
        self._was_recording = busy
        if st["recording"] and not self._was_capturing and self.cfg.gui.notify_start:
            what = ("League session - until you close League" if st["session_matches"] is not None
                    else f"{st['champion']}" if st["in_match"] else "Manual recording")
            self._notify("Recording started", what)
        self._was_capturing = st["recording"]

    def _set_state(self, state: str, text: str) -> None:
        self.status_label.setText(text)
        if self.tray:
            self.tray.setToolTip(f"GameCapture - {text}")
        if state == self._state:
            return
        self._state = state
        color = Palette.STATE[state]
        self.status_dot.setStyleSheet(f"background: {color}; border-radius: 6px;")
        if self.tray:
            self.tray.setIcon(Icons.status_icon(color))

    def _toggle_recording(self) -> None:
        rec = self.engine.recorder
        if rec is None:
            return
        self.rec_btn.setEnabled(False)  # the status timer re-enables it

        def work():
            try:
                self.engine.toggle_manual()
            except Exception as exc:
                log.error("Record toggle failed: %s", exc)
        threading.Thread(target=work, daemon=True).start()

    def _bookmark(self) -> None:
        t = self.engine.bookmark()
        if t is not None:
            log.info("Bookmark at %s", Format.duration(t))

    def set_mode(self, mode: str) -> None:
        """Recording mode switch (header or Settings). Applies from the next match / session."""
        if mode not in RecordingModes.TIPS or mode == self.cfg.game("league").mode:
            return
        self.cfg.game("league").mode = mode
        self.mode_switch.set_value(mode)
        self.settings.changed()
        for key in ("game:league", "game:tft"):
            section = self.settings.sections.get(key)
            if section is not None:
                section.load()
        log.info("Recording mode: %s", RecordingModes.TIPS[mode])

    def replan_video(self) -> None:
        """Capture resolution/monitor changed: re-probe resizing in the background, then update Settings."""
        pipeline = self.engine.pipeline
        if pipeline is None:
            return

        def work():
            try:
                pipeline.plan(self.cfg.capture)
            finally:
                self.sig.video_plan.emit()
        threading.Thread(target=work, name="video-plan", daemon=True).start()

    # ================================================================ notifications

    def _notify(self, title: str, text: str) -> None:
        if self.tray is not None:
            self.tray.showMessage(title, text, Icons.app_icon(), 4000)

    def _notify_saved(self, entry: RecordingEntry) -> None:
        n = len(entry.my_markers)
        parts = [entry.title, entry.result, entry.kda, f"{n} highlight{'s' if n != 1 else ''}" if entry.meta else ""]
        title = "Highlights saved" if entry.game.get("highlights_only") else "Recording saved"
        self._notify(title, "  ·  ".join(p for p in parts if p))

    # ================================================================ window / tray / quit

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        if not self._quitting and self.tray and self.cfg.gui.minimize_to_tray:
            event.ignore()
            self.hide()
            self.sessions.player.player.pause()
            if not self._tray_hint_shown:
                self.tray.showMessage("GameCapture", "Still running in the tray - matches keep recording.",
                                      Icons.app_icon(), 3000)
                self._tray_hint_shown = True
            return
        event.accept()
        self.quit_app()

    def restart_app(self) -> None:
        """Start a fresh copy of GameCapture, then close this one (finishing any recording first)."""
        QProcess.startDetached(sys.executable, sys.argv, os.getcwd())
        self.quit_app()

    def quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.status_timer.stop()
        self.sessions.player.unload()
        rec = self.engine.recorder
        if rec is not None and rec.is_recording:
            self._show_window()
            self.status_label.setText("Saving recording before exit...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            self.engine.shutdown()
        finally:
            QApplication.restoreOverrideCursor()
            self.log_page.detach()
            self.thumbs.shutdown()
            if self.tray:
                self.tray.hide()
            QApplication.quit()
