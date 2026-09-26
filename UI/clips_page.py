"""Clips page: thumbnail gallery of exported clips."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import threading

from PyQt6.QtCore import QFile, QObject, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QLabel, QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox, QVBoxLayout, QWidget

from Capture.ffmpeg import FFmpeg
from Core.discord import ClipTooLongError, DiscordFitter
from Theme.palette import Palette
from UI.icons import Icons
from UI.shell import Shell
from UI.widgets import IconTextButton, PageHeader

if TYPE_CHECKING:
    from UI.main_window import MainWindow


class _FitSignals(QObject):
    done = pyqtSignal(bool, str, object)  # ok, message, path


class ClipsPage(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self._items: dict[str, QListWidgetItem] = {}
        self._fit_sig = _FitSignals()
        self._fit_sig.done.connect(self._on_fit_done)
        self.count_label = QLabel()
        self.count_label.setObjectName("Muted")
        open_btn = IconTextButton("folder", "Open clips folder")
        open_btn.clicked.connect(lambda: Shell.open_path(win.clips_dir()))
        self.fit_btn = IconTextButton("share", "Fit for Discord", primary=True)
        self.fit_btn.setToolTip("Make a copy of the selected clip that fits your Discord upload limit, "
                                "and copy it to the clipboard")
        self.fit_btn.clicked.connect(lambda: self._fit(self._selected_path()))
        self.status = QLabel()
        self.status.setObjectName("Muted")

        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setIconSize(QSize(240, 135))
        self.list.setGridSize(QSize(262, 200))
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setWordWrap(True)
        self.list.setSpacing(6)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.itemDoubleClicked.connect(lambda item: Shell.open_path(Path(item.data(Qt.ItemDataRole.UserRole))))
        self.list.customContextMenuRequested.connect(self._menu)
        hint = QLabel("Double-click to play · gold = too big for Discord: select it and press Fit for Discord")
        hint.setObjectName("Muted")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.addWidget(PageHeader("Clips", self.count_label, self.fit_btn, open_btn))
        root.addWidget(hint)
        root.addWidget(self.status)
        root.addWidget(self.list, 1)
        win.thumbs.ready.connect(self._on_thumb)

    def refresh(self) -> None:
        files = sorted(self.win.clips_dir().glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        self.list.clear()
        self._items.clear()
        limit_mb = self.win.cfg.clips.discord_limit_mb
        for f in files:
            name = f.stem.replace("_", " ")
            size_mb = f.stat().st_size / 1e6
            fits = f.stat().st_size <= DiscordFitter.limit_bytes(limit_mb)
            label = name if len(name) <= 36 else "..." + name[-33:]  # the end holds the highlight name
            item = QListWidgetItem(f"{label}\n{size_mb:.1f} MB" + ("" if fits else "  - too big for Discord"))
            if not fits:
                item.setForeground(QColor(Palette.GOLD))
            item.setToolTip(f"{f.name}\n{size_mb:.1f} MB - "
                            + (f"fits Discord ({limit_mb} MB)" if fits else f"too big for Discord ({limit_mb} MB)"))

            item.setData(Qt.ItemDataRole.UserRole, str(f))
            pix = self.win.thumbs.get(f, 2.0)
            item.setIcon(QIcon(pix) if pix is not None else Icons.icon("play", Palette.TEXT_DISABLED, 48, hover=None))
            self.list.addItem(item)
            self._items[str(f)] = item
        self.count_label.setText(f"{len(files)} clip{'s' if len(files) != 1 else ''}")
        self.list.viewport().update()

    def _on_thumb(self, key: str) -> None:
        item = self._items.get(key)
        if item is not None:
            pix = self.win.thumbs.get(Path(key), 2.0)
            if pix is not None:
                item.setIcon(QIcon(pix))

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        menu.addAction(Icons.icon("play", Palette.TEXT, 16), "Play", lambda: Shell.open_path(path))
        menu.addAction(Icons.icon("copy", Palette.TEXT, 16), "Copy (paste into Discord)",
                       lambda: Shell.copy_file_to_clipboard(path))
        menu.addAction(Icons.icon("share", Palette.ACCENT, 16),
                       f"Fit for Discord ({self.win.cfg.clips.discord_limit_mb} MB) + copy", lambda: self._fit(path))
        menu.addAction(Icons.icon("folder", Palette.TEXT, 16), "Show in folder", lambda: Shell.reveal(path))
        menu.addSeparator()
        menu.addAction(Icons.icon("trash", Palette.LOSE, 16), "Delete...", lambda: self._delete(path))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _delete(self, path: Path) -> None:
        if QMessageBox.question(self, "Delete clip", f"Move to the Recycle Bin?\n\n{path.name}") \
                != QMessageBox.StandardButton.Yes:
            return
        QFile.moveToTrash(str(path))
        self.win.thumbs.forget(path)
        self.refresh()

    # ---------- Discord ----------

    def _selected_path(self) -> Path | None:
        items = self.list.selectedItems()
        return Path(items[0].data(Qt.ItemDataRole.UserRole)) if items else None

    def _fit(self, path: Path | None) -> None:
        if path is None:
            self.status.setText("Select a clip first")
            return
        ffmpeg = self.win.engine.ffmpeg or FFmpeg.locate(self.win.cfg.ffmpeg_path)
        if ffmpeg is None:
            self.status.setText("ffmpeg not found - run Tools\\get_ffmpeg.py")
            return
        limit = self.win.cfg.clips.discord_limit_mb
        fitter = DiscordFitter(ffmpeg, self.win.engine.encoder or "x264")
        self.fit_btn.setEnabled(False)
        self.status.setText(f"Fitting {path.name} into {limit} MB...")

        def work():
            try:
                out = fitter.fit(path, limit)
                note = "already fits" if out == path else f"made {out.name}"
                self._fit_sig.done.emit(True, f"{note} ({out.stat().st_size / 1e6:.1f} MB) - copied, paste it "
                                              f"into Discord with Ctrl+V", out)
            except ClipTooLongError as exc:
                self._fit_sig.done.emit(False, str(exc), None)
            except Exception as exc:
                self._fit_sig.done.emit(False, f"Couldn't shrink the clip: {exc}", None)
        threading.Thread(target=work, name="discord-fit", daemon=True).start()

    def _on_fit_done(self, ok: bool, message: str, path) -> None:
        self.fit_btn.setEnabled(True)
        self.status.setText(message)
        self.status.setStyleSheet(f"color: {Palette.WIN if ok else Palette.LOSE};")
        if ok and path is not None:
            Shell.copy_file_to_clipboard(path)
            self.refresh()
