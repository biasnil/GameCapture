"""Settings page: Outplayed-style sidebar (General + My Games) with auto-saving sections."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QStackedWidget, QVBoxLayout, QWidget

from Games.registry import GameRegistry
from Theme.palette import Palette
from UI.icons import Icons
from UI.settings_sections import (AppSection, AutoRecordSection, CaptureSection, ClipsSection, GameDetailSection,
                                  GamesSection, NotificationsSection, SettingsSection, StorageSection)
from UI.widgets import Banner

if TYPE_CHECKING:
    from UI.main_window import MainWindow

ROLE_KEY = Qt.ItemDataRole.UserRole


class SettingsPage(QWidget):
    GENERAL = (GamesSection, CaptureSection, AutoRecordSection, ClipsSection, StorageSection,
               NotificationsSection, AppSection)

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.cfg = win.cfg
        self._restart_reasons: set[str] = set()

        self.nav = QListWidget()
        self.nav.setObjectName("SettingsNav")
        self.nav.setFixedWidth(220)
        self.nav.setIconSize(QSize(18, 18))
        self.stack = QStackedWidget()
        self.sections: dict[str, SettingsSection] = {}
        for cls in self.GENERAL:
            self._add_section(cls(self))
        GameRegistry.load_custom(self.cfg.games)
        for game in GameRegistry.supported():
            self._add_section(GameDetailSection(self, game))
        self._build_nav()
        self.nav.currentItemChanged.connect(self._on_nav)

        self.restart_banner = Banner()
        self.restart_banner.button.clicked.connect(win.restart_app)
        self.restart_banner.hide()
        self.saved_note = Banner()
        self.saved_note.hide()
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self._save)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.stack, 1)
        footer = QVBoxLayout()
        footer.setContentsMargins(28, 0, 28, 12)
        footer.addWidget(self.restart_banner)
        right.addLayout(footer)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.nav)
        root.addLayout(right, 1)
        self.select("games")

    # ---------- navigation ----------

    def _add_section(self, section: SettingsSection) -> None:
        self.sections[section.key] = section
        self.stack.addWidget(section)

    @staticmethod
    def _header(text: str) -> QListWidgetItem:
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        font = QFont()
        font.setPixelSize(11)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        item.setFont(font)
        item.setForeground(Qt.GlobalColor.gray)
        item.setSizeHint(QSize(0, 34))
        return item

    def _item(self, key: str, text: str, icon) -> QListWidgetItem:
        item = QListWidgetItem(icon, text)
        item.setData(ROLE_KEY, key)
        return item

    def _build_nav(self) -> None:
        current = self.nav.currentItem().data(ROLE_KEY) if self.nav.currentItem() else None
        self.nav.blockSignals(True)
        self.nav.clear()
        self.nav.addItem(self._header("GENERAL"))
        for cls in self.GENERAL:
            self.nav.addItem(self._item(cls.key, cls.title, Icons.icon(cls.icon, Palette.TEXT_MUTED, 18)))
        self.nav.addItem(self._header("MY GAMES"))
        mine = [g for g in GameRegistry.supported() if self.cfg.game(g.id).enabled]
        for g in mine:
            self.nav.addItem(self._item(f"game:{g.id}", g.name, Icons.dot(g.color, 14)))
        if not mine:
            empty = QListWidgetItem("No games enabled")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setForeground(Qt.GlobalColor.gray)
            self.nav.addItem(empty)
        self.nav.blockSignals(False)
        if current:
            self.select(current, quiet=True)

    def refresh_my_games(self) -> None:
        self._build_nav()

    def games_changed(self, select: str | None = None) -> None:
        """A game was added, renamed or removed: rebuild its pages, the tiles, the nav and the watchers."""
        GameRegistry.load_custom(self.cfg.games)
        for key in [k for k, sec in self.sections.items()
                    if isinstance(sec, GameDetailSection) and sec.game.custom]:
            section = self.sections.pop(key)
            self.stack.removeWidget(section)
            section.deleteLater()
        for game in GameRegistry.custom():
            self._add_section(GameDetailSection(self, game))
        games = self.sections.get("games")
        if isinstance(games, GamesSection):
            games.rebuild()
        self._build_nav()
        self.win.engine.sync_game_watchers()
        if select:
            self.select(select if select in self.sections else f"game:{select}")

    def select(self, key: str, quiet: bool = False) -> None:
        section = self.sections.get(key)
        if section is None:
            return
        for i in range(self.nav.count()):
            if self.nav.item(i).data(ROLE_KEY) == key:
                self.nav.blockSignals(quiet)
                self.nav.setCurrentRow(i)
                self.nav.blockSignals(False)
                break
        self.stack.setCurrentWidget(section)
        section.load()

    def open_game(self, game_id: str) -> None:
        self.select(f"game:{game_id}")

    def _on_nav(self, item, previous) -> None:
        if previous is not None and previous.data(ROLE_KEY) == "storage":
            self.win.enforce_storage()
        if item is not None and item.data(ROLE_KEY):
            self.select(item.data(ROLE_KEY), quiet=True)

    # ---------- saving ----------

    def load(self) -> None:
        current = self.stack.currentWidget()
        if isinstance(current, SettingsSection):
            current.load()

    def changed(self, restart: str | None = None) -> None:
        if restart:
            self._restart_reasons.add(restart)
            self.restart_banner.show_message(
                "warning", f"Restart GameCapture to apply: {', '.join(sorted(self._restart_reasons))}.",
                button="Restart now")
        self.save_timer.start(400)

    def _save(self) -> None:
        try:
            self.cfg.save()
        except OSError as exc:
            self.restart_banner.show_message("warning", f"Could not save your settings: {exc}")

    def on_video_plan(self) -> None:
        capture = self.sections.get("capture")
        if isinstance(capture, CaptureSection) and self.stack.currentWidget() is capture:
            capture.update_banner()
