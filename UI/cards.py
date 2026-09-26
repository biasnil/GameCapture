"""Horizontal strip of match cards (thumbnail, champion, result, KDA)."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from Core.library import RecordingEntry
from Theme.palette import Palette
from UI.icons import Icons
from UI.thumbs import ThumbnailCache


class MatchCard(QFrame):
    clicked = pyqtSignal(object)
    THUMB_W, THUMB_H = 104, 58

    def __init__(self, entry: RecordingEntry, parent=None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("Card")
        self.setFixedSize(262, 76)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("selected", "false")

        self.thumb = QLabel()
        self.thumb.setFixedSize(self.THUMB_W, self.THUMB_H)
        self.thumb.setStyleSheet(f"background: {Palette.BG_BASE}; border-radius: 5px;")
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel(entry.title)
        title.setStyleSheet("font-weight: 600; font-size: 14px; background: transparent;")
        top = QHBoxLayout()
        top.setSpacing(4)
        top.addWidget(title)
        top.addStretch()
        if entry.result == "Win":
            trophy = QLabel()
            trophy.setPixmap(Icons.pixmap("trophy", Palette.GOLD, 15))
            trophy.setStyleSheet("background: transparent;")
            top.addWidget(trophy)

        line2 = QLabel(self._line2(entry))
        line3 = QLabel(self._line3(entry))
        line3.setObjectName("Muted")
        for lbl in (line2, line3):
            lbl.setStyleSheet("background: transparent; font-size: 12px;")

        text = QVBoxLayout()
        text.setSpacing(1)
        text.addLayout(top)
        text.addWidget(line2)
        text.addWidget(line3)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 8, 10, 8)
        row.addWidget(self.thumb)
        row.addLayout(text, 1)
        self.setToolTip(f"{entry.game_name}\n{entry.video.name}\n{entry.when:%A %d %B %Y, %H:%M}")

    @staticmethod
    def _line2(e: RecordingEntry) -> str:
        if not e.mode_name:
            return f"<span style='color:{Palette.TEXT_MUTED}'>{e.when:%d %b %H:%M}</span>"
        color = {"Win": Palette.WIN, "Lose": Palette.LOSE}.get(e.result, Palette.TEXT)
        return (f"<span style='color:{color}'>{e.result or '—'}</span>"
                f"<span style='color:{Palette.TEXT_MUTED}'> · {e.mode_name}</span>")

    @staticmethod
    def _line3(e: RecordingEntry) -> str:
        if not e.meta:
            return f"{e.size_mb:,.0f} MB"
        n = len(e.my_markers)
        highlights = f"{n} highlight{'s' if n != 1 else ''}"
        if e.kind == "session":
            games = len(e.game.get("matches", []))
            return f"{games} match{'es' if games != 1 else ''}  ·  {highlights}"
        return f"{e.kda or '—'}  ·  {highlights}"

    def set_thumb(self, pix: QPixmap | None) -> None:
        if pix is None:
            self.thumb.setPixmap(Icons.pixmap("play", Palette.TEXT_DISABLED, 22))
            return
        self.thumb.setPixmap(pix.scaled(self.THUMB_W, self.THUMB_H, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                        Qt.TransformationMode.SmoothTransformation)
                             .copy(0, 0, self.THUMB_W, self.THUMB_H))

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.entry)


class CardStrip(QScrollArea):
    cardClicked = pyqtSignal(object)

    def __init__(self, thumbs: ThumbnailCache, parent=None) -> None:
        super().__init__(parent)
        self.thumbs = thumbs
        self.cards: dict[str, MatchCard] = {}
        self.setWidgetResizable(True)
        self.setFixedHeight(96)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self._row = QHBoxLayout(inner)
        self._row.setContentsMargins(0, 2, 0, 2)
        self._row.setSpacing(8)
        self._row.addStretch()
        self.setWidget(inner)
        self._empty = QLabel("No recordings yet - play a match and it will show up here.", inner)
        self._empty.setObjectName("Muted")
        thumbs.ready.connect(self._on_thumb)

    def set_entries(self, entries: list[RecordingEntry], selected: RecordingEntry | None) -> None:
        while self._row.count():
            widget = self._row.takeAt(0).widget()
            if widget is not None and widget is not self._empty:
                widget.hide()          # removed from the layout != invisible: hide it now,
                widget.deleteLater()   # delete it on the next event-loop pass
        self.cards.clear()
        if not entries:
            self._row.addWidget(self._empty)
        self._empty.setVisible(not entries)  # the empty-state text must never sit under real cards
        for e in entries:
            card = MatchCard(e)
            card.set_thumb(self.thumbs.get(e.video, e.thumb_time))
            card.set_selected(selected is not None and e.video == selected.video)
            card.clicked.connect(self.cardClicked.emit)
            self.cards[str(e.video)] = card
            self._row.addWidget(card)
        self._row.addStretch()

    def mark_selected(self, entry: RecordingEntry | None) -> None:
        for key, card in self.cards.items():
            card.set_selected(entry is not None and key == str(entry.video))

    def _on_thumb(self, key: str) -> None:
        card = self.cards.get(key)
        if card is not None:
            card.set_thumb(self.thumbs.get(card.entry.video, card.entry.thumb_time))

    def wheelEvent(self, event) -> None:  # mouse wheel scrolls sideways
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - event.angleDelta().y())