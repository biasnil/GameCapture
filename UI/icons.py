"""Loads the SVG icons in Assets/, tints them to any colour and caches the results.

Every SVG uses `currentColor`, so one file serves every state (muted, hover, accent...).
The app icon (Assets/app_icon.png / .ico) is a raster image and is handled separately."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from Core.paths import Paths
from Theme.palette import MarkerStyle, Palette

log = logging.getLogger("gamecapture.ui")


class Icons:
    SCALE = 2  # render at 2x so icons stay sharp on high-DPI screens
    _EMPTY = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"/>'
    _sources: dict[str, bytes] = {}
    _renderers: dict[tuple[str, str], QSvgRenderer] = {}
    _pixmaps: dict[tuple, QPixmap] = {}

    # ---------- SVG ----------

    @classmethod
    def _source(cls, name: str) -> bytes:
        if name not in cls._sources:
            path = Paths.ASSETS / f"{name}.svg"
            try:
                cls._sources[name] = path.read_bytes()
            except OSError:
                log.warning("Missing icon: %s", path.name)
                cls._sources[name] = cls._EMPTY
        return cls._sources[name]

    @classmethod
    def renderer(cls, name: str, color: str) -> QSvgRenderer:
        key = (name, color)
        if key not in cls._renderers:
            data = cls._source(name).replace(b"currentColor", color.encode())
            cls._renderers[key] = QSvgRenderer(QByteArray(data))
        return cls._renderers[key]

    @classmethod
    def render(cls, painter: QPainter, name: str, rect: QRectF, color: str) -> None:
        """Draw an icon straight onto a painter (used by the timeline)."""
        cls.renderer(name, color).render(painter, rect)

    @classmethod
    def pixmap(cls, name: str, color: str = Palette.TEXT_MUTED, size: int = 20) -> QPixmap:
        key = (name, color, size)
        if key not in cls._pixmaps:
            pix = QPixmap(size * cls.SCALE, size * cls.SCALE)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            cls.render(p, name, QRectF(0, 0, pix.width(), pix.height()), color)
            p.end()
            pix.setDevicePixelRatio(cls.SCALE)
            cls._pixmaps[key] = pix
        return cls._pixmaps[key]

    @classmethod
    def icon(cls, name: str, color: str = Palette.TEXT_MUTED, size: int = 20, *,
             hover: str | None = Palette.TEXT, checked: str | None = None,
             checked_name: str | None = None) -> QIcon:
        """QIcon with normal / hover / disabled (and optional checked) states."""
        ic = QIcon()
        ic.addPixmap(cls.pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
        ic.addPixmap(cls.pixmap(name, Palette.TEXT_DISABLED, size), QIcon.Mode.Disabled, QIcon.State.Off)
        if hover:
            ic.addPixmap(cls.pixmap(name, hover, size), QIcon.Mode.Active, QIcon.State.Off)
        if checked or checked_name:
            on = cls.pixmap(checked_name or name, checked or color, size)
            ic.addPixmap(on, QIcon.Mode.Normal, QIcon.State.On)
            ic.addPixmap(on, QIcon.Mode.Active, QIcon.State.On)
        return ic

    @classmethod
    def marker_icon(cls, kind: str, favorite: bool = False, size: int = 16) -> QIcon:
        """A highlight's icon in its colour, with a small gold star when it's a favourite."""
        base = cls.pixmap(MarkerStyle.icon(kind), MarkerStyle.color(kind), size)
        if not favorite:
            return QIcon(base)
        pix = QPixmap(base.size())
        pix.setDevicePixelRatio(cls.SCALE)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.drawPixmap(0, 0, base)
        star = size * 0.55
        cls.render(p, "star_filled", QRectF(size - star, 0, star, star), Palette.GOLD)
        p.end()
        return QIcon(pix)

    # ---------- raster: app icon + status dots ----------

    @classmethod
    def app_pixmap(cls, size: int) -> QPixmap:
        key = ("__app__", size)
        if key not in cls._pixmaps:
            src = QPixmap(str(Paths.ASSETS / "app_icon.png"))
            pix = src.scaled(size * cls.SCALE, size * cls.SCALE, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
            pix.setDevicePixelRatio(cls.SCALE)
            cls._pixmaps[key] = pix
        return cls._pixmaps[key]

    @classmethod
    def dot(cls, color: str, size: int = 14) -> QIcon:
        pix = QPixmap(size * cls.SCALE, size * cls.SCALE)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        m = size * cls.SCALE // 6
        p.drawRoundedRect(m, m, pix.width() - 2 * m, pix.height() - 2 * m, 4 * cls.SCALE, 4 * cls.SCALE)
        p.end()
        pix.setDevicePixelRatio(cls.SCALE)
        return QIcon(pix)

    @classmethod
    def app_icon(cls) -> QIcon:
        ico = Paths.ASSETS / "app_icon.ico"
        return QIcon(str(ico if ico.exists() else Paths.ASSETS / "app_icon.png"))

    @classmethod
    def status_icon(cls, color: str) -> QIcon:
        """App icon with a coloured status dot in the corner (for the tray)."""
        pix = QPixmap(str(Paths.ASSETS / "app_icon.png")).scaled(
            64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(Palette.BG_SIDEBAR), 4))
        p.setBrush(QColor(color))
        p.drawEllipse(38, 38, 24, 24)
        p.end()
        return QIcon(pix)
