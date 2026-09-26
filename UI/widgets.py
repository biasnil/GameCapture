"""Small reusable widgets."""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton,
                             QSizePolicy, QToolButton, QToolTip, QVBoxLayout, QWidget)

from Theme.palette import Palette
from UI.icons import Icons  # noqa: E402


class IconButton(QToolButton):
    """Flat icon-only button (star, folder, trash...)."""

    def __init__(self, icon: str, tip: str, size: int = 18, color: str = Palette.TEXT_MUTED,
                 checkable: bool = False, checked_icon: str | None = None, checked_color: str | None = None):
        super().__init__()
        self.setObjectName("IconBtn")
        self.setIcon(Icons.icon(icon, color, size, checked=checked_color, checked_name=checked_icon))
        self.setIconSize(QSize(size, size))
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class IconTextButton(QPushButton):
    """Normal button with an SVG icon in front of the text."""

    def __init__(self, icon: str, text: str, color: str = Palette.TEXT, primary: bool = False, size: int = 16):
        super().__init__(text)
        if primary:
            self.setObjectName("Primary")
            color = Palette.TEXT_ON_ACCENT
        self.setIcon(Icons.icon(icon, color, size, hover=None))
        self.setIconSize(QSize(size, size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class NavButton(QToolButton):
    """Sidebar button: muted icon, accent when its page is showing."""

    def __init__(self, icon: str, tip: str) -> None:
        super().__init__()
        self.setIcon(Icons.icon(icon, Palette.TEXT_MUTED, 22, checked=Palette.ACCENT))
        self.setIconSize(QSize(22, 22))
        self.setToolTip(tip)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class PageHeader(QWidget):
    """Page title on the left, any widgets on the right."""

    def __init__(self, title: str, *right: QWidget) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setObjectName("PageTitle")
        row.addWidget(label)
        self.left = row
        row.addStretch()
        for w in right:
            row.addWidget(w)


class ToggleSwitch(QAbstractButton):
    """iOS-style on/off switch. Use toggled(bool) / setChecked like a checkbox."""

    def __init__(self, checked: bool = False) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on, enabled = self.isChecked(), self.isEnabled()
        track = QColor(Palette.ACCENT if on else Palette.TRACK)
        if not enabled:
            track.setAlpha(90)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 11, 11)
        p.setBrush(QColor(Palette.TEXT_ON_ACCENT if on else Palette.TEXT_MUTED))
        x = self.width() - 19 if on else 3
        p.drawEllipse(QRectF(x, 3, 16, 16))
        p.end()


class SegmentedControl(QWidget):
    """Row of joined buttons, one selected (like the FPS picker). Emits the chosen value."""
    changed = pyqtSignal(object)

    def __init__(self, options: list[tuple[object, str]], compact: bool = False) -> None:
        super().__init__()
        self._compact = compact
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[object, QPushButton] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        for i, (value, label) in enumerate(options):
            b = QPushButton(label)
            b.setObjectName("SegmentSmall" if compact else "Segment")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _=False, v=value: self.changed.emit(v))
            self._group.addButton(b, i)
            self._buttons[value] = b
            row.addWidget(b)

    def set_tooltips(self, tips: dict) -> None:
        for value, tip in tips.items():
            if value in self._buttons:
                self._buttons[value].setToolTip(tip)

    def value(self):
        return next((v for v, b in self._buttons.items() if b.isChecked()), None)

    def set_value(self, value) -> None:
        button = self._buttons.get(value)
        if button is not None:
            button.setChecked(True)
        else:  # value not among the options: show nothing selected
            self._group.setExclusive(False)
            for b in self._buttons.values():
                b.setChecked(False)
            self._group.setExclusive(True)


class PresetCard(QFrame):
    """Selectable card with a radio mark, title and subtitle (video quality presets)."""
    clicked = pyqtSignal(str)

    def __init__(self, key: str, title: str, subtitle: str) -> None:
        super().__init__()
        self.key = key
        self.setObjectName("PresetCard")
        self.setProperty("selected", "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.radio = QLabel()
        self.radio.setStyleSheet("background: transparent;")
        t = QLabel(title)
        t.setStyleSheet("font-weight: 600; font-size: 14px; background: transparent;")
        sub = QLabel(subtitle)
        sub.setObjectName("Muted")
        sub.setStyleSheet("background: transparent;")
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(t)
        text.addWidget(sub)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.addWidget(self.radio, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(text, 1)
        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        self.radio.setPixmap(Icons.pixmap("radio_on" if selected else "radio_off",
                                          Palette.ACCENT if selected else Palette.TEXT_MUTED, 18))
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)


class Banner(QFrame):
    """Coloured notice with an icon, text and an optional action button."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Banner")
        self.icon = QLabel()
        self.icon.setStyleSheet("background: transparent;")
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setStyleSheet("background: transparent;")
        self.button = QPushButton()
        self.button.setObjectName("Primary")
        self.button.hide()
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.text, 1)
        row.addWidget(self.button, 0, Qt.AlignmentFlag.AlignVCenter)

    def show_message(self, kind: str, text: str, button: str | None = None) -> None:
        """kind: 'ok' | 'warning' | 'info'."""
        icon, color = {"ok": ("check_circle", Palette.WIN), "warning": ("warning", Palette.GOLD)}.get(
            kind, ("check_circle", Palette.TEXT_MUTED))
        self.icon.setPixmap(Icons.pixmap(icon, color, 20))
        self.text.setText(text)
        self.setProperty("kind", kind)
        self.style().unpolish(self)
        self.style().polish(self)
        self.button.setVisible(bool(button))
        if button:
            self.button.setText(button)
        self.show()


class SectionHeader(QWidget):
    """Big title + muted one-line description at the top of a settings section."""

    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 6)
        col.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("PageTitle")
        s = QLabel(subtitle)
        s.setObjectName("Muted")
        col.addWidget(t)
        col.addWidget(s)


class InfoTip(QLabel):
    """Small (i) icon: the explanation shows when you hover or click it, instead of a paragraph of text."""

    def __init__(self, text: str, size: int = 15) -> None:
        super().__init__()
        self._text = text
        self.setPixmap(Icons.pixmap("info", Palette.TEXT_MUTED, size))
        self.setFixedSize(size + 4, size + 4)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setStyleSheet("background: transparent;")
        self.setToolTip(self._html())

    def _html(self) -> str:
        return f"<div style='max-width: 320px'>{self._text}</div>"

    def set_text(self, text: str) -> None:
        self._text = text
        self.setToolTip(self._html())

    def enterEvent(self, event) -> None:      # show at once, no hover delay
        QToolTip.showText(self.mapToGlobal(self.rect().bottomLeft()), self._html(), self)
        super().enterEvent(event)

    def mousePressEvent(self, event) -> None:
        QToolTip.showText(self.mapToGlobal(self.rect().bottomLeft()), self._html(), self)


class SettingRow(QWidget):
    """Label on the left, control on the right - one line of a settings form.
    A short hint shows under the label; a longer explanation goes behind an (i) icon."""
    SHORT_HINT = 48

    def __init__(self, label: str, control: QWidget, hint: str = "") -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 4, 0, 4)
        text = QVBoxLayout()
        text.setSpacing(0)
        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(QLabel(label))
        if hint and len(hint) > self.SHORT_HINT:
            head.addWidget(InfoTip(hint))
        head.addStretch()
        text.addLayout(head)
        if hint and len(hint) <= self.SHORT_HINT:
            h = QLabel(hint)
            h.setObjectName("Muted")
            text.addWidget(h)
        row.addLayout(text, 1)
        row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)


class UsageBar(QWidget):
    """Horizontal bar split into coloured segments (e.g. recordings / clips / other / free),
    with an optional dashed marker (e.g. the auto-delete limit)."""

    def __init__(self, height: int = 18) -> None:
        super().__init__()
        self.setFixedHeight(height + 6)
        self._bar_h = height
        self.segments: list[tuple[float, str]] = []   # (value, colour)
        self.total = 0.0
        self.marker: float | None = None               # value from the left edge

    def set_data(self, segments: list[tuple[float, str]], total: float, marker: float | None = None) -> None:
        self.segments, self.total, self.marker = segments, total, marker
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h, top = self.width(), self._bar_h, 3
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(Palette.BG_PANEL))
        p.drawRoundedRect(QRectF(0, top, w, h), 6, 6)
        if self.total > 0:
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(0, top, w, h), 6, 6)
            p.setClipPath(clip)
            x = 0.0
            for value, color in self.segments:
                seg_w = w * value / self.total
                if seg_w > 0:
                    p.fillRect(QRectF(x, top, max(seg_w, 2.0), h), QColor(color))
                x += seg_w
            p.restore()
            if self.marker is not None and 0 < self.marker < self.total:
                mx = w * self.marker / self.total
                pen = QPen(QColor(Palette.TEXT), 2, Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawLine(QPointF(mx, 0), QPointF(mx, top + h + 3))
        p.end()


class LegendItem(QWidget):
    """Colour swatch + 'name  value', for UsageBar legends."""

    def __init__(self, color: str, name: str, dashed: bool = False) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        swatch = QLabel()
        swatch.setFixedSize(12, 12)
        style = f"border: 2px dashed {color}; background: transparent;" if dashed else f"background: {color};"
        swatch.setStyleSheet(style + " border-radius: 3px;")
        self._name = name
        self.text = QLabel(name)
        self.text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.addWidget(swatch)
        row.addWidget(self.text, 1)

    def set_value(self, value: str) -> None:
        self.text.setText(f"{self._name}  <span style='color:{Palette.TEXT_MUTED}'>{value}</span>")


class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps onto the next line (like words in a paragraph)."""

    def __init__(self, parent=None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect, apply: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() and line > 0:
                x, y, line = rect.x(), y + line + self._spacing, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line = max(line, hint.height())
        return y + line - rect.y()


class FilterChip(QPushButton):
    """Rounded on/off pill with an icon and a count - e.g. the highlight type filters."""
    soloRequested = pyqtSignal()   # double-click: show only this one

    def __init__(self, icon, text: str, tip: str = "") -> None:
        super().__init__(text)
        self.setObjectName("Chip")
        self.setCheckable(True)
        self.setChecked(True)
        self.setIcon(icon)
        self.setIconSize(QSize(14, 14))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tip:
            self.setToolTip(tip)

    def mouseDoubleClickEvent(self, event) -> None:
        self.soloRequested.emit()
