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
from PyQt6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QColorDialog, QDoubleSpinBox, QFileDialog, QFontComboBox, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSlider, QVBoxLayout,
                             QWidget)

from Core.editor import MIN_CLIP, EditProject, Element, Overlay
from Core.formatting import Format
from Theme.palette import Palette
from UI.icons import Icons
from UI.widgets import IconButton, IconTextButton, InfoTip

if TYPE_CHECKING:
    from UI.editor_page import EditorPage

IMAGE_FILTER = "Images and animations (*.png *.apng *.jpg *.jpeg *.webp *.gif *.bmp);;All files (*)"


# ======================================================================== rendering

class ElementRenderer:
    """Draws elements. Animated images (GIF, animated WebP / PNG) are decoded once into frames
    with their delays; which frame shows depends on how long the element has been on screen."""
    _cache: dict[tuple, QImage] = {}
    _anims: dict[str, list[tuple[QImage, int]]] = {}   # path -> [(frame, delay ms)]
    MAX_FRAMES = 600
    EXPORT_FPS = 30                                     # animated images are exported at up to this rate

    # ---------- animated images ----------

    @classmethod
    def frames(cls, path: str) -> list[tuple[QImage, int]]:
        """All frames of an image with their delays - one frame for a still image, [] if unreadable."""
        if path not in cls._anims:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            frames: list[tuple[QImage, int]] = []
            while len(frames) < cls.MAX_FRAMES:
                img = reader.read()
                if img.isNull():
                    break
                delay = reader.nextImageDelay()
                frames.append((img, delay if delay > 10 else 100))  # 0 / tiny delays: browsers use 100 ms
                if not reader.supportsAnimation() or not reader.canRead():
                    break
            cls._anims[path] = frames
        return cls._anims[path]

    @classmethod
    def is_animated(cls, e: Element) -> bool:
        return e.kind == "image" and len(cls.frames(e.path)) > 1

    @classmethod
    def loop_ms(cls, e: Element) -> int:
        return sum(d for _f, d in cls.frames(e.path)) or 1

    @classmethod
    def frame_index(cls, e: Element, t: float) -> int:
        """Which frame is showing `t` seconds after the element appeared (the animation loops)."""
        frames = cls.frames(e.path)
        if len(frames) < 2:
            return 0
        ms = int(max(0.0, t) * 1000) % cls.loop_ms(e)
        for i, (_img, delay) in enumerate(frames):
            if ms < delay:
                return i
            ms -= delay
        return len(frames) - 1

    # ---------- drawing ----------

    @classmethod
    def image(cls, e: Element, frame_w: int, frame_h: int, t: float = 0.0) -> QImage:
        """The element drawn at its size for a frame_w x frame_h video (transparent background).
        t: seconds since the element appeared - picks the frame of an animated image."""
        frame = cls.frame_index(e, t) if e.kind == "image" else 0
        key = (e.kind, e.text, e.path, e.color, round(e.size, 4), round(e.opacity, 3), e.font, e.bold, e.italic,
               frame, frame_w, frame_h)
        if key not in cls._cache:
            if len(cls._cache) > 400:
                cls._cache.clear()
            cls._cache[key] = cls._text(e, frame_h) if e.kind == "text" else cls._picture(e, frame_w, frame)
        return cls._cache[key]

    @staticmethod
    def font(e: Element, px: int) -> QFont:
        font = QFont(e.font) if e.font else QFont()
        font.setPixelSize(px)
        font.setBold(e.bold)
        font.setItalic(e.italic)
        return font

    @classmethod
    def _text(cls, e: Element, frame_h: int) -> QImage:
        px = max(8, int(e.size * frame_h))
        path = QPainterPath()
        path.addText(0, 0, cls.font(e, px), e.text or "Text")
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

    @classmethod
    def _picture(cls, e: Element, frame_w: int, frame: int = 0) -> QImage:
        frames = cls.frames(e.path)
        width = max(1, int(e.size * frame_w))
        if not frames:   # missing file: a visible placeholder instead of nothing
            img = QImage(width, max(1, width // 2), QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(QColor(229, 72, 77, 150))
            return img
        src = frames[min(frame, len(frames) - 1)][0]
        scaled = src.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        img = QImage(scaled.size(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setOpacity(e.opacity)
        p.drawImage(0, 0, scaled)
        p.end()
        return img

    @classmethod
    def rect(cls, e: Element, video: QRectF, t: float = 0.0) -> QRectF:
        """Where the element lands inside the displayed video rectangle."""
        img = cls.image(e, max(1, int(video.width())), max(1, int(video.height())), t)
        cx, cy = video.left() + e.x * video.width(), video.top() + e.y * video.height()
        return QRectF(cx - img.width() / 2, cy - img.height() / 2, img.width(), img.height())

    @classmethod
    def overlays(cls, project: EditProject, width: int, height: int, folder: Path,
                 fps: float = 30) -> list[Overlay]:
        """Every element as PNG(s) at the export size, for ProjectExporter (safe in a worker thread).
        An animated image becomes one loop of frames sampled at up to EXPORT_FPS."""
        out = []
        for i, e in enumerate(project.elements):
            if e.duration <= 0:
                continue
            first = cls.image(e, width, height)
            x, y = int(e.x * width - first.width() / 2), int(e.y * height - first.height() / 2)
            if cls.is_animated(e):
                rate = min(float(fps or cls.EXPORT_FPS), cls.EXPORT_FPS)
                count = max(1, round(cls.loop_ms(e) / 1000 * rate))
                for n in range(count):
                    cls.image(e, width, height, n / rate).save(str(folder / f"element_{i:02d}_{n:04d}.png"), "PNG")
                out.append(Overlay(str(folder / f"element_{i:02d}_%04d.png"), x, y, e.start, e.end, rate))
            else:
                png = folder / f"element_{i:02d}.png"
                first.save(str(png), "PNG")
                out.append(Overlay(str(png), x, y, e.start, e.end))
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
        now = self.project.elements_at(t)
        if before != {e.id for e in now}:
            self.refresh()
        elif any(ElementRenderer.is_animated(e) for e in now):
            self.update()    # next frame of a GIF / animated WebP

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
            t = self.position - e.start
            r = ElementRenderer.rect(e, video, t)
            if not e.visible_at(self.position):
                p.setOpacity(0.35)
            p.drawImage(r.topLeft(), ElementRenderer.image(e, int(video.width()), int(video.height()), t))
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
            r = ElementRenderer.rect(e, video, self.position - e.start)
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
        self.font_box = QFontComboBox()      # every font installed in Windows, shown in its own style
        self.font_box.setEditable(True)       # type to search
        self.font_box.setMaxVisibleItems(16)
        self.font_box.currentFontChanged.connect(lambda f: self._set("font", f.family()))
        self.bold = self._toggle("B", "Bold", "font-weight: 700;")
        self.bold.toggled.connect(lambda on: self._set("bold", on))
        self.italic = self._toggle("I", "Italic", "font-style: italic;")
        self.italic.toggled.connect(lambda on: self._set("italic", on))
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
        self.font_row = QLabel("Font")
        form.addRow(self.font_row, self.font_box)
        style = QHBoxLayout()
        style.setSpacing(4)
        for w in (self.color, self.bold, self.italic):
            style.addWidget(w)
        style.addStretch()
        self.style_box = QWidget()
        self.style_box.setLayout(style)
        style.setContentsMargins(0, 0, 0, 0)
        self.color_row = QLabel("Style")
        form.addRow(self.color_row, self.style_box)
        self.anim_note = QLabel()
        self.anim_note.setObjectName("Muted")
        form.addRow("", self.anim_note)
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
    def _toggle(text: str, tip: str, css: str) -> QPushButton:
        b = QPushButton(text)
        b.setCheckable(True)
        b.setFixedSize(30, 26)
        b.setToolTip(tip)
        b.setStyleSheet(f"QPushButton {{ {css} padding: 0; font-size: 14px; }} QPushButton:checked {{ background: #1f2a2e; "
                        f"border: 1px solid {Palette.ACCENT}; color: {Palette.ACCENT}; }}")
        return b

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
            for w in (self.text, self.text_row, self.font_box, self.font_row, self.style_box, self.color_row):
                w.setVisible(is_text)
            if is_text:
                self.font_box.setCurrentFont(QFont(e.font) if e.font else QFont())
                self.bold.setChecked(e.bold)
                self.italic.setChecked(e.italic)
            animated = ElementRenderer.is_animated(e)
            self.anim_note.setVisible(animated)
            if animated:
                frames = ElementRenderer.frames(e.path)
                self.anim_note.setText(f"Animated · {len(frames)} frames · {ElementRenderer.loop_ms(e) / 1000:.1f} s loop")
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
