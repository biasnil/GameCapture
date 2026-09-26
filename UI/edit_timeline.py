"""Editor timeline: the project's clips as filmstrips with their highlight icons, over a time ruler.

Click a clip to select it (and seek there), drag its middle to move it, drag its edges to trim it.
Click or drag the ruler to seek. The whole project always fits the width.

The clips and ruler are drawn once into a cached pixmap; during playback only the playhead moves."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QToolTip, QWidget

from Core.editor import EditProject
from Core.formatting import Format
from Theme.palette import MarkerStyle, Palette
from UI.icons import Icons


class EditTimeline(QWidget):
    seekRequested = pyqtSignal(float)     # timeline seconds
    selectionChanged = pyqtSignal(int)    # clip index, -1 = none
    edited = pyqtSignal()                 # clips were trimmed or moved
    MARGIN = 14
    GAP = 3          # px between clips
    TRACK_TOP = 8
    TRACK_H = 62
    HANDLE = 7       # px: edge grab zone for trimming
    ICON = 16

    def __init__(self, frames, parent=None) -> None:
        super().__init__(parent)
        self.frames = frames
        self.project: EditProject | None = None
        self.position = 0.0
        self.selected = -1
        self.setMinimumHeight(self.TRACK_TOP + self.TRACK_H + 40)
        self.setMouseTracking(True)
        self._drag: dict | None = None     # {"kind": "seek" | "move" | "trim_start" | "trim_end", ...}
        self._label_font = QFont()
        self._label_font.setPixelSize(11)
        self._name_font = QFont()
        self._name_font.setPixelSize(11)
        self._name_font.setBold(True)
        self._static: QPixmap | None = None
        self._static_key: tuple | None = None
        frames.ready.connect(self.update)

    # ---------- data ----------

    def set_project(self, project: EditProject | None) -> None:
        self.project = project
        self.selected = -1
        self._drag = None
        self.update()

    def set_position(self, seconds: float) -> None:
        old = self._x(self.position)
        self.position = seconds
        new = self._x(seconds)
        if int(old) != int(new):  # only repaint the strip the playhead moved across
            self.update(QRect(int(min(old, new)) - 8, 0, int(abs(new - old)) + 17, self.height()))

    def set_selected(self, index: int) -> None:
        self.selected = index
        self.update()

    # ---------- geometry ----------

    @property
    def _ruler_y(self) -> float:
        return self.TRACK_TOP + self.TRACK_H + 8

    def _scale(self) -> float:
        """Pixels per second (frozen while dragging, so the clip under the mouse doesn't slide)."""
        if self._drag and "scale" in self._drag:
            return self._drag["scale"]
        dur = self.project.duration if self.project else 0
        gaps = self.GAP * max(0, len(self.project.clips) - 1) if self.project else 0
        return (self.width() - 2 * self.MARGIN - gaps) / dur if dur > 0 else 0.0

    def _clip_rects(self) -> list[QRectF]:
        rects, x, s = [], float(self.MARGIN), self._scale()
        for c in self.project.clips if self.project else []:
            w = max(2.0, c.duration * s)
            rects.append(QRectF(x, self.TRACK_TOP, w, self.TRACK_H))
            x += w + self.GAP
        return rects

    def _x(self, t: float) -> float:
        """Timeline time -> x, accounting for the gaps between clips."""
        if not self.project or not self.project.clips:
            return float(self.MARGIN)
        s, x, offset = self._scale(), float(self.MARGIN), 0.0
        for c in self.project.clips:
            if t <= offset + c.duration:
                return x + (t - offset) * s
            x += c.duration * s + self.GAP
            offset += c.duration
        return x - self.GAP

    def _t(self, x: float) -> float:
        if not self.project or not self.project.clips:
            return 0.0
        offset = 0.0
        for c, r in zip(self.project.clips, self._clip_rects()):
            if x < r.right() + self.GAP / 2:
                return offset + min(max((x - r.left()) / r.width(), 0.0), 1.0) * c.duration
            offset += c.duration
        return self.project.duration

    def _hit(self, pos: QPointF) -> tuple[int, str]:
        """(clip index, 'start' | 'end' | 'body') under the mouse, or (-1, '')."""
        if not self.TRACK_TOP <= pos.y() <= self.TRACK_TOP + self.TRACK_H:
            return -1, ""
        for i, r in enumerate(self._clip_rects()):
            if r.left() - self.HANDLE / 2 <= pos.x() <= r.right() + self.HANDLE / 2:
                if abs(pos.x() - r.left()) <= self.HANDLE and r.width() > 3 * self.HANDLE:
                    return i, "start"
                if abs(pos.x() - r.right()) <= self.HANDLE:
                    return i, "end"
                return i, "body"
        return -1, ""

    def _drop_index(self, x: float) -> int:
        rects = self._clip_rects()
        for i, r in enumerate(rects):
            if x < r.center().x():
                return i
        return len(rects)

    # ---------- painting ----------

    def _static_layer(self) -> QPixmap:
        """Clips (filmstrips, icons, names, trim handles) and the ruler - redrawn only when they change."""
        clips = tuple((c.id, c.start, c.end, c.label) for c in self.project.clips) if self.project else ()
        key = (self.width(), self.height(), self.devicePixelRatioF(), id(self.project), clips, self.selected,
               self.frames.generation, self._scale())
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
        if not clips:
            p.setPen(QColor(Palette.TEXT_MUTED))
            p.drawText(QRectF(0, self.TRACK_TOP, self.width(), self.TRACK_H), Qt.AlignmentFlag.AlignCenter,
                       "Double-click a project video (or use Add to timeline) to start editing")
        else:
            for i, (clip, rect) in enumerate(zip(self.project.clips, self._clip_rects())):
                self._paint_clip(p, i, clip, rect)
            self._paint_ruler(p, ry)
        p.end()
        self._static, self._static_key = pix, key
        return pix

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self._static_layer())
        if self.project and self.project.clips:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            ry = self._ruler_y
            p.setPen(QPen(QColor(Palette.ACCENT), 2))
            p.drawLine(QPointF(self.MARGIN, ry), QPointF(self._x(self.position), ry))
            if self._drag and self._drag["kind"] == "move" and self._drag.get("moved"):
                self._paint_drop_marker(p, self._clip_rects())
            self._paint_playhead(p, ry)
        p.end()

    def _paint_clip(self, p: QPainter, i: int, clip, rect: QRectF) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, 5, 5)
        p.save()
        p.setClipPath(path)
        p.fillRect(rect, QColor(Palette.BG_CARD))
        # filmstrip: one frame per tile, taken from the clip's own time range
        tile_w = self.TRACK_H * 16 / 9
        n = max(1, int(rect.width() // tile_w) + 1)
        for k in range(n):
            x = rect.left() + k * tile_w
            t = clip.start + min((k * tile_w + tile_w / 2) / rect.width(), 1.0) * clip.duration
            pm = self.frames.get(clip.source, t)
            if pm is not None:
                p.drawPixmap(QRectF(x, rect.top(), tile_w, rect.height()), pm, QRectF(pm.rect()))
        shade = QColor(0, 0, 0, 70 if i != self.selected else 20)
        p.fillRect(rect, shade)
        # highlight icons inside the clip
        s = rect.width() / clip.duration if clip.duration else 0
        for m in self.project.markers_in(clip):
            if not (m.get("involves_me") or m.get("type") == "game_end"):
                continue
            mx = rect.left() + (m["video_time"] - clip.start) * s
            kind = m.get("type", "")
            Icons.render(p, MarkerStyle.icon(kind),
                         QRectF(mx - self.ICON / 2, rect.bottom() - self.ICON - 4, self.ICON, self.ICON),
                         MarkerStyle.color(kind))
        if rect.width() > 60:
            p.setFont(self._name_font)
            p.setPen(QColor("#ffffff"))
            name = p.fontMetrics().elidedText(clip.label or Format.duration(clip.duration),
                                              Qt.TextElideMode.ElideRight, int(rect.width() - 12))
            p.drawText(QRectF(rect.left() + 6, rect.top() + 3, rect.width() - 12, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        p.restore()
        selected = i == self.selected
        p.setPen(QPen(QColor(Palette.ACCENT if selected else Palette.BORDER), 2 if selected else 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 5, 5)
        if selected:  # trim handles
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(Palette.ACCENT))
            for hx in (rect.left(), rect.right() - 5):
                p.drawRoundedRect(QRectF(hx, rect.top() + rect.height() / 2 - 12, 5, 24), 2, 2)

    def _paint_ruler(self, p: QPainter, ry: float) -> None:
        dur = self.project.duration
        for major, minor in ((5, 1), (10, 2), (30, 10), (60, 15), (120, 30), (300, 60), (600, 120)):
            if dur / major <= 10:
                break
        p.setFont(self._label_font)
        for k in range(int(dur // minor) + 1):
            t = k * minor
            x = self._x(t)
            is_major = t % major == 0
            p.setPen(QPen(QColor(Palette.TICK_MAJOR if is_major else Palette.TICK_MINOR), 1))
            p.drawLine(QPointF(x, ry + 4), QPointF(x, ry + (10 if is_major else 7)))
            if is_major:
                p.setPen(QColor(Palette.TEXT_MUTED))
                label = f"{t // 60}m" if t and t % 60 == 0 else f"{t // 60}:{t % 60:02d}" if t >= 60 else f"0:{t:02d}"
                p.drawText(QRectF(x - 30, ry + 12, 60, 14), Qt.AlignmentFlag.AlignCenter, label)

    def _paint_drop_marker(self, p: QPainter, rects: list[QRectF]) -> None:
        idx = self._drag.get("drop", -1)
        if idx < 0:
            return
        x = rects[idx].left() - self.GAP / 2 if idx < len(rects) else rects[-1].right() + self.GAP / 2
        p.setPen(QPen(QColor(Palette.GOLD), 3))
        p.drawLine(QPointF(x, self.TRACK_TOP - 4), QPointF(x, self.TRACK_TOP + self.TRACK_H + 4))

    def _paint_playhead(self, p: QPainter, ry: float) -> None:
        x = self._x(self.position)
        p.setPen(QPen(QColor(Palette.REC), 2))
        p.drawLine(QPointF(x, 2), QPointF(x, ry + 8))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(Palette.REC))
        p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 8)]))

    # ---------- mouse ----------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.project or not self.project.clips:
            return
        pos = event.position()
        i, part = self._hit(pos)
        if i < 0:
            self._drag = {"kind": "seek"}
            self.seekRequested.emit(self._t(pos.x()))
            return
        if i != self.selected:
            self.selected = i
            self.selectionChanged.emit(i)
        clip = self.project.clips[i]
        self._drag = {"kind": {"start": "trim_start", "end": "trim_end"}.get(part, "move"), "index": i,
                      "x": pos.x(), "scale": self._scale(), "start": clip.start, "end": clip.end, "moved": False}
        if part == "body":
            self.seekRequested.emit(self._t(pos.x()))
        self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        d = self._drag
        if d is None:
            self._hover(event)
            return
        if d["kind"] == "seek":
            self.seekRequested.emit(self._t(pos.x()))
            return
        dx = pos.x() - d["x"]
        if abs(dx) > 3:
            d["moved"] = True
        i, scale = d["index"], d["scale"] or 1.0
        if d["kind"] == "trim_start":
            self.project.trim(i, start=d["start"] + dx / scale)
            self.seekRequested.emit(self.project.clip_offset(i))
        elif d["kind"] == "trim_end":
            self.project.trim(i, end=d["end"] + dx / scale)
            self.seekRequested.emit(self.project.clip_offset(i) + self.project.clips[i].duration - 0.05)
        elif d["moved"]:
            d["drop"] = self._drop_index(pos.x())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()

    def mouseReleaseEvent(self, _event) -> None:
        d, self._drag = self._drag, None
        if d is None or d["kind"] == "seek":
            return
        if d["kind"] == "move" and d.get("moved") and d.get("drop", -1) >= 0:
            i, drop = d["index"], d["drop"]
            to = drop - 1 if drop > i else drop
            if to != i:
                self.selected = self.project.move(i, to)
                self.selectionChanged.emit(self.selected)
                self.seekRequested.emit(self.project.clip_offset(self.selected))
                self.edited.emit()
        elif d["kind"] in ("trim_start", "trim_end") and d.get("moved"):
            self.edited.emit()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _hover(self, event) -> None:
        i, part = self._hit(event.position())
        if part in ("start", "end"):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif part == "body":
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        if i >= 0:
            c = self.project.clips[i]
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"{c.label or 'Clip'}\n{Format.duration(c.start)} - {Format.duration(c.end)} of the "
                              f"video ({Format.duration(c.duration)})\nDrag to move, drag the edges to trim", self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, _event) -> None:
        QToolTip.hideText()

