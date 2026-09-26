"""Favorites page: starred highlights from every match."""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from Core.formatting import Format
from Core.library import RecordingEntry
from UI.icons import Icons
from UI.widgets import PageHeader


class FavoritesPage(QWidget):
    openRequested = pyqtSignal(str, float)  # video path, marker video_time

    def __init__(self) -> None:
        super().__init__()
        self.count_label = QLabel()
        self.count_label.setObjectName("Muted")
        self.list = QListWidget()
        self.list.setIconSize(QSize(16, 16))
        self.list.itemDoubleClicked.connect(self._open)
        hint = QLabel("Double-click a highlight to watch it. Star highlights on the Sessions page.")
        hint.setObjectName("Muted")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.addWidget(PageHeader("Favorites", self.count_label))
        root.addWidget(hint)
        root.addWidget(self.list, 1)

    def refresh(self, entries: list[RecordingEntry]) -> None:
        self.list.clear()
        n = 0
        for e in entries:
            for m in e.markers:
                if not m.get("favorite"):
                    continue
                n += 1
                item = QListWidgetItem(Icons.marker_icon(m.get("type", "")),
                                       f"{m['label']}      {e.title} · {e.result or '—'} · "
                                       f"{e.when:%d %b %H:%M} · {Format.duration(m['video_time'])}")
                item.setData(Qt.ItemDataRole.UserRole, (str(e.video), m["video_time"]))
                self.list.addItem(item)
        if n == 0:
            item = QListWidgetItem("No favourites yet.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
        self.count_label.setText(f"{n} highlight{'s' if n != 1 else ''}")

    def _open(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            self.openRequested.emit(*data)
