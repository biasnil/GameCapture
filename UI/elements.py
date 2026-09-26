"""Text and images on top of the video (editor "Elements").

ElementRenderer    draws one element to an image - the same code for the preview and the export,
                   so what you see is what you get
ElementOverlay     a transparent layer laid exactly over the preview video: shows the elements that are
                   on screen at the playhead; drag one to move it, drag its corner to resize it
ElementsPanel      the Elements tab: add text / image, and the selected element's properties

Why a separate layer and not widgets on the video: on Windows the video is drawn in its own native
surface, and ordinary widgets can't reliably paint over it. Drawing the video through a graphics
scene instead would make playback slower - exactly what the preview must not be."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QColorDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPushButton, QSlider, QVBoxLayout, QWidget)

from Core.editor import MIN_CLIP, EditProject, Element, Overlay
from Core.formatting import Format
from Theme.palette import Palette
from UI.icons import Icons
from UI.widgets import IconButton, IconTextButton, InfoTip

if TYPE_CHECKING:
    from UI.editor_page import EditorPage

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp);;All files (*)"


# ======================================================================== rendering

class ElementRenderer:
    _cache: dict[tuple, QImage] = {}

    @classmethod
    def image(cls, e: Element, frame_w: int, frame_h: int) -> QImage:
        """The element drawn at its size for a frame_w x frame_h video (transparent background)."""
        key = (e.kind, e.text, e.path, e.color, round(e.size, 4), round(e.opacity, 3), frame_w, frame_h)
        if key not in cls._cache:
            if len(cls._cache) > 200:
                cls._cache.clear()
            cls._cache[key] = cls._text(e, frame_h) if e.kind == "text" else cls._picture(e, frame_w)
        return cls._cache[key]

    @staticmethod
    def _text(e: Element, frame_h: int) -> QImage:
        px = max(8, int(e.size * frame_h))
        font = QFont()
        font.setPixelSize(px)
        font.setBold(True)
        path = QPainterPath()
        path.addText(0, 0, font, e.text or "Text")
        box = path.boundingRect()
        outline = max(2.0, px * 0.09)
        pad = outline * 2 + px * 0.06
        img = QImage(max(1, int(box.width() + 2 * pad)), max(1, int(box.height() + 2 * pad)),
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(e.opacity)
        p.translate(pad - box.left(), pad - box.top())
        shadow = QColor(0, 0, 0, 110)                     # soft drop shadow, then outline, then fill
        p.save()
        p.translate(px * 0.04, px * 0.05)
        p.fillPath(path, shadow)
        p.restore()
        p.strokePath(path, QPen(QColor("#000000"), outline, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
        p.fillPath(path, QColor(e.color))
        p.end()
        return img

    @staticmethod
    def _picture(e: Element, frame_w: int) -> QImage:
        src = QImage(e.path)
        width = max(1, int(e.size * frame_w))
        if src.isNull():   # missing file: a visible placeholder instead of nothing
            img = QImage(width, max(1, width // 2), QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(QColor(229, 72, 77, 150))
            return img
        scaled = src.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        img = QImage(scaled.size(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setOpacity(e.opacity)
        p.drawImage(0, 0, scaled)
        p.end()
        return img

    @classmethod
    def rect(cls, e: Element, video: QRectF) -> QRectF:
        """Where the element lands inside the displayed video rectangle."""
        img = cls.image(e, max(1, int(video.width())), max(1, int(video.height())))
        cx, cy = video.left() + e.x * video.width(), video.top() + e.y * video.height()
        return QRectF(cx - img.width() / 2, cy - img.height() / 2, img.width(), img.height())

    @classmethod
    def overlays(cls, project: EditProject, width: int, height: int, folder: Path) -> list[Overlay]:
        """Every element as a PNG at the export size, for ProjectExporter (safe in a worker thread)."""
        out = []
        for i, e in enumerate(project.elements):
            if e.duration <= 0:
                continue
            img = cls.image(e, width, height)
            png = folder / f"element_{i:02d}.png"
            img.save(str(png), "PNG")
            out.append(Overlay(str(png), int(e.x * width - img.width() / 2), int(e.y * height - img.height() / 2),
                               e.start, e.end))
        return out

    @staticmethod
    def temp_folder() -> Path:
        return Path(tempfile.mkdtemp(prefix="gamecapture_elements_"))


# ======================================================================== preview layer

class ElementOverlay(QWidget):
    """Transparent window over the preview. Empty areas let clicks through; elements can be dragged."""
    selected = pyqtSignal(str)          # element id ("" = none)
    editStarted = pyqtSignal()          # a drag began (snapshot for Undo)
    edited = pyqtSignal()               # an element was moved / resized
    HANDLE = 10

    def __init__(self, target: QWidget, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.target = target
        self.project: EditProject | None = None
        self.video_size = QSize(1920, 1080)
        self.position = 0.0
        self.current = ""                # selected element id
        self.wanted = False              # the editor page is showing
        self._drag: dict | None = None

    # ---------- state ----------

    def set_project(self, project: EditProject | None) -> None:
        self.project = project
        self.current = ""
        self.refresh()

    def set_position(self, t: float) -> None:
        if self.project is None:
            return
        before = {e.id for e in self.project.elements_at(self.position)}
        self.position = t
        if before != {e.id for e in self.project.elements_at(t)}:
            self.refresh()

    def select(self, element_id: str) -> None:
        self.current = element_id
        self.update()

    def refresh(self) -> None:
        """Follow the preview's position and size; show only when there's something to show."""
        target = self.target
        top = target.window()
        show = (self.wanted and target.isVisible() and not top.isMinimized() and top.isVisible()
                and bool(self.project and self.project.elements))
        if show:
            self.setGeometry(QRect(target.mapToGlobal(QPoint(0, 0)), target.size()))
            if not self.isVisible():
                self.show()
            self.update()
        elif self.isVisible():
            self.hide()

    def video_rect(self) -> QRectF:
        """The picture inside the preview (letterboxed to the video's shape)."""
        vw, vh = max(1, self.video_size.width()), max(1, self.video_size.height())
        w, h = self.width(), self.height()
        scale = min(w / vw, h / vh)
        return QRectF((w - vw * scale) / 2, (h - vh * scale) / 2, vw * scale, vh * scale)

    def _visible(self) -> list[Element]:
        if self.project is None:
            return []
        shown = self.project.elements_at(self.position)
        sel = self.project.element(self.current)
        if sel is not None and sel not in shown:
            shown.append(sel)            # the one you're editing stays visible (faded) so you can place it
        return shown

    # ---------- painting ----------

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        video = self.video_rect()
        for e in self._visible():
            r = ElementRenderer.rect(e, video)
            if not e.visible_at(self.position):
                p.setOpacity(0.35)
            p.drawImage(r.topLeft(), ElementRenderer.image(e, int(video.width()), int(video.height())))
            p.setOpacity(1.0)
            if e.id == self.current:
                p.setPen(QPen(QColor(Palette.ACCENT), 1.5, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r.adjusted(-3, -3, 3, 3))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(Palette.ACCENT))
                p.drawRect(self._handle(r))
        p.end()

    def _handle(self, r: QRectF) -> QRectF:
        return QRectF(r.right() + 3 - self.HANDLE / 2, r.bottom() + 3 - self.HANDLE / 2, self.HANDLE, self.HANDLE)

    # ---------- mouse ----------

    def _hit(self, pos: QPointF) -> tuple[Element | None, str]:
        video = self.video_rect()
        for e in reversed(self._visible()):     # topmost first
            r = ElementRenderer.rect(e, video)
            if e.id == self.current and self._handle(r).adjusted(-4, -4, 4, 4).contains(pos):
                return e, "resize"
            if r.contains(pos):
                return e, "move"
        return None, ""

    def mousePressEvent(self, event) -> None:
        e, part = self._hit(event.position())
        if e is None:
            self.current = ""
            self.selected.emit("")
            self.update()
            return
        if e.id != self.current:
            self.current = e.id
            self.selected.emit(e.id)
        self._drag = {"kind": part, "id": e.id, "pos": event.position(), "x": e.x, "y": e.y, "size": e.size,
                      "moved": False}
        self.editStarted.emit()
        self.update()

    def mouseMoveEvent(self, event) -> None:
        d = self._drag
        if d is None:
            _e, part = self._hit(event.position())
            self.setCursor({"move": Qt.CursorShape.SizeAllCursor,
                            "resize": Qt.CursorShape.SizeFDiagCursor}.get(part, Qt.CursorShape.ArrowCursor))
            return
        e = self.project.element(d["id"]) if self.project else None
        if e is None:
            return
        video = self.video_rect()
        delta = event.position() - d["pos"]
        d["moved"] = d["moved"] or abs(delta.x()) + abs(delta.y()) > 2
        if d["kind"] == "move":
            e.x = min(max(d["x"] + delta.x() / video.width(), 0.0), 1.0)
            e.y = min(max(d["y"] + delta.y() / video.height(), 0.0), 1.0)
        else:   # resize from the corner, around the centre
            r = ElementRenderer.rect(Element(**{**e.__dict__, "size": d["size"]}), video)
            grow = max((r.width() / 2 + delta.x()) / max(1.0, r.width() / 2),
                       (r.height() / 2 + delta.y()) / max(1.0, r.height() / 2))
            e.size = min(max(d["size"] * grow, 0.02), 1.0)
        self.update()

    def mouseReleaseEvent(self, _event) -> None:
        d, self._drag = self._drag, None
        if d is not None and d["moved"]:
            self.edited.emit()


# ======================================================================== side panel

class ElementsPanel(QWidget):
    """Elements tab: add text or an image, and edit the selected one."""

    def __init__(self, page: "EditorPage") -> None:
        super().__init__()
        self.page = page
        self._loading = False
        add_text = IconTextButton("text", "Text")
        add_text.setToolTip("Add a title or caption at the playhead")
        add_text.clicked.connect(self.add_text)
        add_image = IconTextButton("image", "Image")
        add_image.setToolTip("Add a picture, logo or sticker (PNG with transparency works best)")
        add_image.clicked.connect(self.add_image)
        adds = QHBoxLayout()
        adds.addWidget(add_text)
        adds.addWidget(add_image)
        adds.addWidget(InfoTip("Elements show on top of the video from their start to their end. Drag one on the "
                               "preview to move it, drag its corner to resize it. On the timeline they sit on "
                               "the thin track above the clips: drag to move, drag the edges to change when "
                               "they show."))

        self.list = QListWidget()
        self.list.setIconSize(QSize(18, 18))
        self.list.currentItemChanged.connect(self._on_current)

        # properties of the selected element
        self.text = QLineEdit()
        self.text.setPlaceholderText("Your text")
        self.text.textEdited.connect(lambda t: self._set("text", t))
        self.color = QPushButton()
        self.color.setFixedWidth(60)
        self.color.clicked.connect(self._pick_color)
        self.size = QSlider(Qt.Orientation.Horizontal)
        self.size.setRange(2, 100)
        self.size.valueChanged.connect(lambda v: self._set("size", v / 100))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(10, 100)
        self.opacity.valueChanged.connect(lambda v: self._set("opacity", v / 100))
        self.start = self._seconds()
        self.end = self._seconds()
        self.start.valueChanged.connect(lambda v: self._set_time(start=v))
        self.end.valueChanged.connect(lambda v: self._set_time(end=v))
        fit = QPushButton("Fit to clip")
        fit.setToolTip("Show it for the whole clip under the playhead")
        fit.clicked.connect(self._fit_to_clip)
        delete = IconButton("trash", "Delete this element")
        delete.clicked.connect(self.delete)
        times = QHBoxLayout()
        times.addWidget(self.start)
        times.addWidget(QLabel("to"))
        times.addWidget(self.end)
        form = QFormLayout()
        self.text_row = QLabel("Text")
        form.addRow(self.text_row, self.text)
        self.color_row = QLabel("Colour")
        form.addRow(self.color_row, self.color)
        form.addRow("Size", self.size)
        form.addRow("Opacity", self.opacity)
        form.addRow("Shows", times)
        actions = QHBoxLayout()
        actions.addWidget(fit)
        actions.addStretch()
        actions.addWidget(delete)
        self.props = QWidget()
        pl = QVBoxLayout(self.props)
        pl.setContentsMargins(0, 4, 0, 0)
        pl.addLayout(form)
        pl.addLayout(actions)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.addLayout(adds)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.props)
        self.refresh()

    @staticmethod
    def _seconds() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(1)
        spin.setSingleStep(0.5)
        spin.setSuffix(" s")
        spin.setRange(0, 24 * 3600)
        return spin

    # ---------- state ----------

    @property
    def project(self) -> EditProject:
        return self.page.project

    @property
    def current(self) -> Element | None:
        return self.project.element(self.page.overlay.current)

    def refresh(self) -> None:
        """Rebuild the list and the property form from the project."""
        self._loading = True
        cur = self.page.overlay.current
        self.list.clear()
        for e in self.project.elements:
            item = QListWidgetItem(Icons.icon("text" if e.kind == "text" else "image", Palette.TEXT_MUTED, 18),
                                   f"{e.name}\n{Format.duration(e.start)} - {Format.duration(e.end)}")
            item.setData(Qt.ItemDataRole.UserRole, e.id)
            self.list.addItem(item)
            if e.id == cur:
                self.list.setCurrentItem(item)
        if not self.project.elements:
            empty = QListWidgetItem("No text or images yet")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(empty)
        e = self.current
        self.props.setVisible(e is not None)
        if e is not None:
            is_text = e.kind == "text"
            for w in (self.text, self.text_row, self.color, self.color_row):
                w.setVisible(is_text)
            if self.text.text() != e.text:
                self.text.setText(e.text)
            self.color.setStyleSheet(f"background: {e.color}; border: 1px solid {Palette.BORDER};")
            self.size.setRange(2, 30 if is_text else 100)
            self.size.setValue(int(round(e.size * 100)))
            self.opacity.setValue(int(round(e.opacity * 100)))
            self.start.setValue(e.start)
            self.end.setValue(e.end)
        self._loading = False

    def _on_current(self, item, _previous) -> None:
        if self._loading or item is None:
            return
        element_id = item.data(Qt.ItemDataRole.UserRole) or ""
        self.page.select_element(element_id)

    # ---------- editing ----------

    def add_text(self) -> None:
        self._add(Element("text", text="Your text", size=0.08, y=0.15))

    def add_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Add an image", str(Path.home()), IMAGE_FILTER)
        if path:
            self._add(Element("image", path=path, size=0.25, x=0.85, y=0.2))

    def _add(self, e: Element) -> None:
        t = self.page.seq.position()
        e.start = t
        e.end = min(t + 3.0, max(self.project.duration, t + MIN_CLIP)) if self.project.duration else t + 3.0
        self.page.checkpoint()
        self.project.add_element(e)
        self.page.select_element(e.id)
        self.page.elements_changed()

    def delete(self) -> None:
        e = self.current
        if e is None:
            return
        self.page.checkpoint()
        self.project.remove_element(e.id)
        self.page.select_element("")
        self.page.elements_changed()

    def _set(self, field: str, value) -> None:
        e = self.current
        if self._loading or e is None or getattr(e, field) == value:
            return
        self.page.checkpoint_once(f"{e.id}:{field}")    # one Undo step per slider drag / typing burst
        setattr(e, field, value)
        self.page.elements_changed(rebuild=False)

    def _set_time(self, start: float | None = None, end: float | None = None) -> None:
        e = self.current
        if self._loading or e is None:
            return
        self.page.checkpoint_once(f"{e.id}:time")
        self.project.move_element(e.id, start, end)
        self.page.elements_changed()

    def _pick_color(self) -> None:
        e = self.current
        if e is None:
            return
        color = QColorDialog.getColor(QColor(e.color), self, "Text colour")
        if color.isValid():
            self.page.checkpoint()
            e.color = color.name()
            self.page.elements_changed()

    def _fit_to_clip(self) -> None:
        e = self.current
        hit = self.project.locate(self.page.seq.position())
        if e is None or hit is None:
            return
        self.page.checkpoint()
        i = hit[0]
        e.start = self.project.clip_offset(i)
        e.end = e.start + self.project.clips[i].duration
        self.page.elements_changed()
