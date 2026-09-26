"""Add a game dialog: pick any running program (or browse to its .exe) and GameCapture records it
the whole time it is running, like the built-in games without an API."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QVBoxLayout)

from Games.registry import GameRegistry

# Things that are running on every PC and are never the game you want.
SYSTEM_NAMES = {"system", "svchost.exe", "explorer.exe", "dwm.exe", "csrss.exe", "services.exe", "lsass.exe",
                "winlogon.exe", "conhost.exe", "runtimebroker.exe", "searchhost.exe", "sihost.exe",
                "taskhostw.exe", "ctfmon.exe", "fontdrvhost.exe", "smss.exe", "wininit.exe", "registry",
                "python.exe", "pythonw.exe", "ffmpeg.exe", "memory compression", "system idle process",
                "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "discord.exe", "chrome.exe",
                "msedge.exe", "firefox.exe", "spotify.exe", "startmenuexperiencehost.exe", "textinputhost.exe",
                "shellexperiencehost.exe", "searchindexer.exe", "securityhealthsystray.exe", "audiodg.exe"}


class AddGameDialog(QDialog):
    def __init__(self, parent=None, taken: set[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add a game")
        self.setMinimumWidth(460)
        self._taken = {t.lower() for t in (taken or set())}   # executables other games already use

        intro = QLabel("Start the game, then pick it from the list - or browse to its .exe. GameCapture will "
                       "record it the whole time it is running. Press the Bookmark hotkey in game to mark "
                       "highlights.")
        intro.setWordWrap(True)
        intro.setObjectName("Muted")

        self.exe = QComboBox()
        self.exe.setEditable(True)
        self.exe.lineEdit().setPlaceholderText("MyGame.exe")
        self.exe.currentTextChanged.connect(self._on_exe)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._fill_running)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        exe_row = QHBoxLayout()
        exe_row.addWidget(self.exe, 1)
        exe_row.addWidget(refresh)
        exe_row.addWidget(browse)

        self.name = QLineEdit()
        self.name.setPlaceholderText("My Game")
        self.name.textEdited.connect(lambda _: setattr(self, "_name_edited", True))
        self._name_edited = False

        self.error = QLabel()
        self.error.setStyleSheet("color: #e5484d;")
        self.error.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Game executable", exe_row)
        form.addRow("Name", self.name)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add game")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(self.buttons)
        self._fill_running()
        self.exe.setCurrentText("")

    # ---------- result ----------

    @property
    def game_name(self) -> str:
        return self.name.text().strip() or self.pretty_name(self.exe_name)

    @property
    def exe_name(self) -> str:
        text = Path(self.exe.currentText().strip().strip('"')).name
        if text and not text.lower().endswith(".exe"):
            text += ".exe"
        return text

    @staticmethod
    def pretty_name(exe: str) -> str:
        """'RocketLeague.exe' -> 'Rocket League', 'my_game-win64.exe' -> 'My Game Win64'."""
        stem = Path(exe).stem.replace("_", " ").replace("-", " ")
        spaced = "".join(" " + c if c.isupper() and i and stem[i - 1].islower() else c for i, c in enumerate(stem))
        return " ".join(w[:1].upper() + w[1:] for w in spaced.split()) or exe

    # ---------- events ----------

    def _fill_running(self) -> None:
        try:
            from Games.processes import ProcessSnapshot
            names = sorted(n for n in ProcessSnapshot._scan() if n.endswith(".exe") and n not in SYSTEM_NAMES)
        except Exception:
            names = []
        current = self.exe.currentText()
        self.exe.blockSignals(True)
        self.exe.clear()
        self.exe.addItems(names)
        self.exe.setCurrentText(current)
        self.exe.blockSignals(False)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose the game's .exe", "", "Programs (*.exe);;All files (*)")
        if path:
            self.exe.setCurrentText(Path(path).name)
            if not self._name_edited:
                parent = Path(path).parent.name
                self.name.setText(parent if parent and parent.lower() not in ("bin", "binaries", "win64", "x64")
                                  else self.pretty_name(path))

    def _on_exe(self, _text: str) -> None:
        self.error.setText("")
        if not self._name_edited:
            self.name.setText(self.pretty_name(self.exe_name) if self.exe_name else "")

    def _accept(self) -> None:
        exe = self.exe_name
        if not exe:
            self.error.setText("Pick the game's executable first.")
            return
        if exe.lower() in self._taken:
            self.error.setText(f"{exe} is already recorded by another game in the list.")
            return
        self.accept()


class CustomGames:
    """Games you add yourself, stored in the config as games[id] with custom=True."""

    @staticmethod
    def add(cfg, name: str, exe: str) -> str:
        """Store a new game in the config and register it. Returns its id."""
        from Core.config import GameSettings
        base = GameRegistry.custom_id(name)
        game_id, n = base, 2
        while game_id in cfg.games or GameRegistry.known(game_id):
            game_id, n = f"{base}_{n}", n + 1
        cfg.games[game_id] = GameSettings(enabled=True, mode="session", processes=[exe], name=name, custom=True)
        GameRegistry.load_custom(cfg.games)
        return game_id

    @staticmethod
    def remove(cfg, game_id: str) -> None:
        gs = cfg.games.get(game_id)
        if gs is not None and gs.custom:
            del cfg.games[game_id]
        GameRegistry.load_custom(cfg.games)

    @staticmethod
    def taken_executables(cfg) -> set[str]:
        """Executables another game already records (League's client isn't a game you'd add)."""
        out: set[str] = set()
        for g in GameRegistry.supported():
            if g.id not in ("league", "tft"):
                gs = cfg.games.get(g.id)
                out.update((gs.processes if gs else None) or g.processes)
        return out
