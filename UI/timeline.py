"""Outplayed-style timeline: SVG event icons above a minute ruler.

Your moments are icons (grouped with a count badge when close together); other players'
events are faint ticks on the ruler. Click an icon to jump there, click/drag the ruler to
seek. Shaded bands show what will be exported.

Everything except the playhead is drawn once into a cached pixmap: the player reports its position
many times a second, and redrawing every SVG icon each time steals time from video playback."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QToolTip, QWidget

from Core.formatting import Format
from Theme.palette import MarkerStyle, Palette
from UI.icons import Icons


class Timeline(QWidget):
    seekRequested = pyqtSignal(float)   # seconds
    markerClicked = pyqtSignal(dict)    # first marker of the clicked icon group
    MARGIN = 18
    CELL = 28                           # icon cell width; closer markers are grouped
    ICON = 20

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(96)
        self.setMouseTracking(True)
        self.duration = 0.0
        self.position = 0.0
        self.markers: list[dict] = []
        self.segments: list[tuple[float, float]] = []
        self.selected: dict | None = None
        self._groups: list[dict] = []
        self._hover: dict | None = None
        self._dragging = False
        self._badge_font = QFont()
        self._badge_font.setPixelSize(10)
        self._badge_font.setBold(True)
        self._label_font = QFont()
        self._label_font.setPixelSize(11)
        self._static: QPixmap | None = None
        self._static_key: tuple | None = None

    # ---------- data ----------

    def set_duration(self, seconds: float) -> None:
        self.duration = max(0.0, seconds)
        self.update()

    def set_position(self, seconds: float) -> None:
        old = self._x(self.position)
        self.position = seconds
        new = self._x(seconds)
        if int(old) != int(new):  # only repaint the strip the playhead moved across
            self.update(QRect(int(min(old, new)) - 8, 0, int(abs(new - old)) + 17, self.height()))

    def set_markers(self, markers: list[dict]) -> None:
        self.markers = markers
        self.selected = None
        self.update()

    def set_segments(self, segments: list[tuple[float, float]]) -> None:
        self.segments = segments
        self.update()

    def set_selected(self, marker: dict | None) -> None:
        self.selected = marker
        self.update()

    # ---------- geometry ----------

    @property
    def _ruler_y(self) -> float:
        return self.height() - 30

    def _x(self, t: float) -> float:
        w = self.width() - 2 * self.MARGIN
        return self.MARGIN + (t / self.duration * w if self.duration > 0 else 0)

    def _t(self, x: float) -> float:
        w = max(1, self.width() - 2 * self.MARGIN)
        return min(max((x - self.MARGIN) / w, 0.0), 1.0) * self.duration

    @staticmethod
    def _is_icon(m: dict) -> bool:
        return m.get("involves_me") or m.get("type") == "game_end"

    def _compute_groups(self) -> list[dict]:
        groups: list[dict] = []
        for m in sorted((m for m in self.markers if self._is_icon(m)), key=lambda m: m["video_time"]):
            x = self._x(m["video_time"])
            if groups and x - groups[-1]["x"] < self.CELL:
                groups[-1]["items"].append(m)
            else:
                groups.append({"x": x, "items": [m]})
        for g in groups:
            g["lead"] = max(g["items"], key=lambda m: m.get("importance", 0))
        return groups

    def _group_at(self, x: float, y: float) -> dict | None:
        if y > self._ruler_y - 4:
            return None
        return next((g for g in self._groups if abs(g["x"] - x) <= self.CELL / 2), None)

    def _tick_steps(self) -> tuple[int, int]:
        """(major, minor) tick spacing in seconds, so ~4-8 labels fit."""
        for major, minor in ((30, 10), (60, 15), (120, 30), (300, 60), (600, 60), (900, 300), (1800, 300)):
            if self.duration / major <= 8:
                return major, minor
        return 3600, 600

    @staticmethod
    def _tick_label(t: int) -> str:
        return f"{t // 60}m" if t % 60 == 0 else f"{t // 60}:{t % 60:02d}"

    # ---------- painting ----------

    def _static_layer(self) -> QPixmap:
        """Track, bands, ticks, ruler and icons - redrawn only when one of them changes."""
        key = (self.width(), self.height(), self.devicePixelRatioF(), self.duration, id(self.markers),
               len(self.markers), tuple(self.segments), id(self.selected),
               self._hover["x"] if self._hover else None)
        if self._static is not None and key == self._static_key:
            return self._static
        dpr = self.devicePixelRatioF()
        pix = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ry = self._ruler_y
        p.setPen(QPen(QColor(Palette.TRACK), 2))
        p.drawLine(QPointF(self.MARGIN, ry), QPointF(self.width() - self.MARGIN, ry))
        if self.duration:
            self._groups = self._compute_groups()
            self._paint_segments(p, ry)
            self._paint_other_ticks(p, ry)
            self._paint_ruler(p, ry)
            for g in self._groups:
                self._paint_group(p, g, ry)
        p.end()
        self._static, self._static_key = pix, key
        return pix

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self._static_layer())
        if self.duration:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            ry = self._ruler_y
            p.setPen(QPen(QColor(Palette.ACCENT), 2))
            p.drawLine(QPointF(self.MARGIN, ry), QPointF(self._x(self.position), ry))
            self._paint_playhead(p, ry)
        p.end()

    def _paint_segments(self, p: QPainter, ry: float) -> None:
        band = QColor(Palette.ACCENT)
        band.setAlpha(30)
        edge = QColor(Palette.ACCENT)
        edge.setAlpha(150)
        for start, end in self.segments:   # one band per clip, with a visible edge where clips meet
            x1, x2 = self._x(start), self._x(end)
            rect = QRectF(x1 + 1, 4, max(2.0, x2 - x1 - 2), ry + 2)
            p.fillRect(rect, band)
            p.setPen(QPen(edge, 1))
            p.drawLine(rect.topLeft(), rect.bottomLeft())
            p.drawLine(rect.topRight(), rect.bottomRight())

    def _paint_other_ticks(self, p: QPainter, ry: float) -> None:
        for m in self.markers:
            if self._is_icon(m):
                continue
            c = QColor(MarkerStyle.color(m.get("type", "")))
            c.setAlpha(120)
            p.setPen(QPen(c, 1))
            x = self._x(m["video_time"])
            p.drawLine(QPointF(x, ry - 5), QPointF(x, ry - 1))

    def _paint_ruler(self, p: QPainter, ry: float) -> None:
        major, minor = self._tick_steps()
        p.setFont(self._label_font)
        for i in range(int(self.duration // minor) + 1):
            t = i * minor
            x = self._x(t)
            is_major = t % major == 0
            p.setPen(QPen(QColor(Palette.TICK_MAJOR if is_major else Palette.TICK_MINOR), 1))
            p.drawLine(QPointF(x, ry + 4), QPointF(x, ry + (10 if is_major else 7)))
            if is_major:
                p.setPen(QColor(Palette.TEXT_MUTED))
                p.drawText(QRectF(x - 30, ry + 12, 60, 14), Qt.AlignmentFlag.AlignCenter, self._tick_label(t))

    def _paint_group(self, p: QPainter, g: dict, ry: float) -> None:
        lead, x = g["lead"], g["x"]
        kind = lead.get("type", "")
        color = MarkerStyle.color(kind)
        if self.selected is not None and any(m is self.selected for m in g["items"]):
            bg = Palette.BG_SELECTED
        elif g is self._hover:
            bg = Palette.BG_HOVER
        else:
            bg = None
        if bg:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(bg))
            p.drawRoundedRect(QRectF(x - 15, 3, 30, ry - 1), 7, 7)
        stem = QColor(color)
        stem.setAlpha(90)
        p.setPen(QPen(stem, 1))
        p.drawLine(QPointF(x, 34), QPointF(x, ry - 2))
        Icons.render(p, MarkerStyle.icon(kind), QRectF(x - self.ICON / 2, 10, self.ICON, self.ICON), color)

        badge = ""
        if kind == "multikill":
            badge = str((lead.get("event") or {}).get("KillStreak", ""))
        elif len(g["items"]) > 1:
            badge = str(len(g["items"]))
        if badge:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(x + 11, 9), 6.5, 6.5)
            p.setFont(self._badge_font)
            p.setPen(QColor(Palette.TEXT_ON_ACCENT))
            p.drawText(QRectF(x + 4.5, 2.5, 13, 13), Qt.AlignmentFlag.AlignCenter, badge)

    def _paint_playhead(self, p: QPainter, ry: float) -> None:
        x = self._x(self.position)
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.drawLine(QPointF(x, 2), QPointF(x, ry + 8))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawPolygon(QPolygonF([QPointF(x - 5, ry - 9), QPointF(x + 5, ry - 9), QPointF(x, ry - 2)]))

    # ---------- mouse ----------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.duration:
            return
        pos = event.position()
        g = self._group_at(pos.x(), pos.y())
        if g is not None:
            self.selected = g["items"][0]
            self.markerClicked.emit(g["items"][0])
            self.update()
            return
        self._dragging = True
        self.seekRequested.emit(self._t(pos.x()))

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._dragging:
            self.seekRequested.emit(self._t(pos.x()))
            return
        g = self._group_at(pos.x(), pos.y())
        if g is not self._hover:
            self._hover = g
            self.update()
        if g is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            QToolTip.showText(event.globalPosition().toPoint(),
                              "\n".join(f"{Format.duration(m['video_time'])}  {m['label']}" for m in g["items"]), self)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            QToolTip.hideText()

    def mouseReleaseEvent(self, _event) -> None:
        self._dragging = False

    def leaveEvent(self, _event) -> None:
        self._hover = None
        self.update()
