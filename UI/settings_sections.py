"""The individual Settings sections (Games, Capture, Auto-record, Clips, Storage, Notifications, App).

Every control writes straight into the live config; the page saves config.json shortly after."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from Capture.ffmpeg import FFmpeg
from Capture.presets import VideoPresets
from Core.formatting import Format
from Core.library import RecordingLibrary
from Core.paths import Paths
from Games.registry import GameInfo, GameRegistry, RecordingModes
from Theme.palette import Palette
from UI.icons import Icons
from UI.shell import Shell
from UI.widgets import (Banner, IconButton, IconTextButton, LegendItem, PresetCard, SectionHeader, SegmentedControl,
                        SettingRow, ToggleSwitch, UsageBar)

if TYPE_CHECKING:
    from UI.settings_page import SettingsPage

ENCODER_NAMES = {"nvenc": "NVIDIA NVENC", "amf": "AMD AMF", "qsv": "Intel Quick Sync", "x264": "x264 (software)"}


# ======================================================================== base

class SettingsSection(QScrollArea):
    key = ""
    title = ""
    icon = ""

    def __init__(self, page: "SettingsPage") -> None:
        super().__init__()
        self.page = page
        self.cfg = page.cfg
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setMaximumWidth(860)
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(28, 20, 28, 20)
        self.body.setSpacing(10)
        centre = QWidget()                       # keeps the column centred on wide windows
        row = QHBoxLayout(centre)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(inner, 100)
        row.addStretch(1)
        self.setWidget(centre)
        self._loading = False

    def finish(self) -> None:
        self.body.addStretch()

    def load(self) -> None:
        """Refresh controls from the config (no change events while doing it)."""

    def changed(self, restart: str | None = None) -> None:
        if not self._loading:
            self.page.changed(restart)

    @staticmethod
    def label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {Palette.TEXT_MUTED}; font-weight: 600; margin-top: 8px;")
        return lbl

    @staticmethod
    def seconds(maximum: float = 60) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0, maximum)
        spin.setDecimals(0)
        spin.setSuffix(" s")
        spin.setFixedWidth(110)
        return spin

    def toggle_row(self, label: str, hint: str, getter, setter, restart: str | None = None) -> ToggleSwitch:
        switch = ToggleSwitch(getter())
        switch.toggled.connect(lambda on: (setter(on), self.changed(restart)))
        self.body.addWidget(SettingRow(label, switch, hint))
        return switch


# ======================================================================== games

class GameBadge(QLabel):
    def __init__(self, game: GameInfo, size: int = 40) -> None:
        super().__init__(game.initials)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        alpha = "" if game.supported else "; color: rgba(255,255,255,0.55)"
        self.setStyleSheet(f"background: {game.color}; border-radius: 8px; font-weight: 700; "
                           f"font-size: {size // 3}px; color: #0b0e11{alpha};")
        if not game.supported:
            self.setStyleSheet(self.styleSheet() + "background: #2a3038;")


class GameTile(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, game: GameInfo, section: "GamesSection") -> None:
        super().__init__()
        self.game = game
        self.setObjectName("GameTile")
        self.setProperty("planned", "false" if game.supported else "true")
        self.setFixedHeight(64)
        name = QLabel(game.name)
        name.setStyleSheet("font-weight: 600; background: transparent;"
                           + ("" if game.supported else f" color: {Palette.TEXT_MUTED};"))
        status = QLabel(game.status_text)
        status.setObjectName("Muted")
        status.setStyleSheet("background: transparent; font-size: 12px;")
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(name)
        text.addWidget(status)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 12, 8)
        row.addWidget(GameBadge(game))
        row.addLayout(text, 1)
        if game.supported:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.switch = ToggleSwitch(section.cfg.game(game.id).enabled)
            self.switch.toggled.connect(lambda on: section.set_enabled(game.id, on))
            row.addWidget(self.switch)
            self.setToolTip(f"Click for {game.name} settings")
        else:
            self.setToolTip("Not auto-detected yet - record it manually with the Record button or hotkey")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.game.supported:
            self.clicked.emit(self.game.id)


class GamesSection(SettingsSection):
    key, title, icon = "games", "Games", "gamepad"

    def __init__(self, page) -> None:
        super().__init__(page)
        self.body.addWidget(SectionHeader("Games", "Choose which games GameCapture records automatically"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search for a game")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(Icons.icon("search", Palette.TEXT_MUTED, 16), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._layout_tiles)
        self.body.addWidget(self.search)
        self.grid = QGridLayout()
        self.grid.setSpacing(8)
        self.body.addLayout(self.grid)
        note = QLabel(f"Highlights = detected automatically. Bookmarks = press {Format.hotkey(self.cfg.hotkeys.bookmark)} "
                      f"after a great play. Planned games aren't auto-detected yet - record them with the Record "
                      f"button or {Format.hotkey(self.cfg.hotkeys.toggle)}.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        self.body.addWidget(note)
        self.finish()
        self.tiles = [GameTile(g, self) for g in GameRegistry.all()]
        for tile in self.tiles:
            tile.clicked.connect(page.open_game)
        self._layout_tiles()

    def _layout_tiles(self) -> None:
        q = self.search.text().strip().lower()
        while self.grid.count():
            self.grid.takeAt(0)
        visible = [t for t in self.tiles if q in t.game.name.lower()]
        for t in self.tiles:
            t.setVisible(t in visible)
        for i, tile in enumerate(visible):
            self.grid.addWidget(tile, i // 3, i % 3)

    def set_enabled(self, game_id: str, on: bool) -> None:
        self.cfg.game(game_id).enabled = on
        self.changed()
        self.page.refresh_my_games()

    def load(self) -> None:
        for tile in self.tiles:
            if tile.game.supported:
                tile.switch.blockSignals(True)
                tile.switch.setChecked(self.cfg.game(tile.game.id).enabled)
                tile.switch.blockSignals(False)


class GameDetailSection(SettingsSection):
    MODE_GAMES = ("league", "tft")   # share League's client and recording modes

    def __init__(self, page, game: GameInfo) -> None:
        super().__init__(page)
        self.game = game
        self.key, self.title = f"game:{game.id}", game.name
        head = QHBoxLayout()
        head.addWidget(GameBadge(game, 48))
        head.addWidget(SectionHeader(game.name, game.status_text), 1)
        self.body.addLayout(head)
        gs = self.cfg.game(game.id)
        auto_hint = ("Start recording when a match starts, stop when it ends" if game.support == "highlights"
                     or game.id == "tft" else "Record the whole time the game is open")
        self.switch = self.toggle_row("Auto-record", auto_hint, lambda: gs.enabled, self._set_enabled)

        self.mode = None
        if game.id in self.MODE_GAMES:
            self.mode = SegmentedControl(list(RecordingModes.OPTIONS))
            self.mode.setFixedWidth(330)
            self.mode.set_tooltips(RecordingModes.TIPS)
            self.mode.changed.connect(lambda v: None if self._loading else page.win.set_mode(v))
            self.mode_hint = QLabel()
            self.mode_hint.setObjectName("Muted")
            self.mode_hint.setWordWrap(True)
            self.body.addWidget(SettingRow("Recording mode", self.mode,
                                           "Shared by League and TFT. Also in the top bar"))
            self.body.addWidget(self.mode_hint)
        if game.id == "league":
            self.skip = QLineEdit()
            self.skip.setPlaceholderText("PRACTICETOOL, ARAM ...")
            self.skip.setFixedWidth(260)
            self.skip.editingFinished.connect(self._save_skip)
            self.body.addWidget(SettingRow("Don't record these modes", self.skip,
                                           "Comma-separated game mode ids, e.g. PRACTICETOOL, ARAM, CHERRY (Arena)"))
        if game.id == "cs2":
            self._build_cs2()
        if game.support == "auto" and game.id != "tft":
            self.procs = QLineEdit()
            self.procs.setFixedWidth(300)
            self.procs.setPlaceholderText(", ".join(game.processes))
            self.procs.editingFinished.connect(self._save_processes)
            self.body.addWidget(SettingRow("Game executable", self.procs,
                                           "How GameCapture spots the game (Task Manager > Details). "
                                           "Only change it if a game update renames it"))

        self.body.addWidget(self.label("How it works"))
        how = QLabel(game.how)
        how.setWordWrap(True)
        self.body.addWidget(how)
        tip = QLabel(f"Press {Format.hotkey(self.cfg.hotkeys.bookmark)} in game to add your own highlight at any moment.")
        tip.setObjectName("Muted")
        tip.setWordWrap(True)
        self.body.addWidget(tip)
        self.finish()

    # ---------- CS2 ----------

    def _build_cs2(self) -> None:
        self.body.addWidget(self.label("Game State Integration"))
        self.cs2_banner = Banner()
        self.cs2_banner.button.clicked.connect(self._install_cs2)
        self.body.addWidget(self.cs2_banner)
        self.cs2_port = QSpinBox()
        self.cs2_port.setRange(1024, 65535)
        self.cs2_port.setFixedWidth(160)
        self.cs2_port.valueChanged.connect(self._set_cs2_port)
        self.body.addWidget(SettingRow("Local port", self.cs2_port,
                                       "Only change if another app uses it - then press Install again and restart"))

    def _cs2_opts(self) -> dict:
        from Games.cs2 import CS2Installer
        opts = self.cfg.game("cs2").options
        opts.setdefault("gsi_port", 3021)
        opts.setdefault("gsi_token", CS2Installer.new_token())
        return opts

    def _refresh_cs2(self) -> None:
        from Games.cs2 import CS2Installer
        installed = CS2Installer.installed_file()
        if installed is None and CS2Installer.find_cfg_dir() is None:
            self.cs2_banner.show_message("warning", "CS2 wasn't found through Steam on this PC.", button="Try again")
        elif installed is None:
            self.cs2_banner.show_message("warning", "Not installed yet: CS2 needs a small config file to send "
                                                    "match data to GameCapture.", button="Install")
        else:
            self.cs2_banner.show_message("ok", f"Installed - highlights are detected automatically.<br>"
                                               f"<span style='color:{Palette.TEXT_MUTED}'>{installed}</span>",
                                         button="Reinstall")

    def _install_cs2(self) -> None:
        from Games.cs2 import CS2Installer
        opts = self._cs2_opts()
        try:
            CS2Installer.install(int(opts["gsi_port"]), opts["gsi_token"])
            self.changed()
            self._refresh_cs2()
            self.cs2_banner.text.setText(self.cs2_banner.text.text() +
                                         "<br><b>Restart CS2</b> if it's open, so it loads the file.")
        except OSError as exc:
            self.cs2_banner.show_message("warning", f"Couldn't install: {exc}", button="Try again")

    def _set_cs2_port(self, port: int) -> None:
        if not self._loading:
            self._cs2_opts()["gsi_port"] = port
            self.changed(restart="CS2 port")

    # ---------- common ----------

    def _set_enabled(self, on: bool) -> None:
        self.cfg.game(self.game.id).enabled = on
        self.page.refresh_my_games()

    def _save_skip(self) -> None:
        self.cfg.auto.skip_modes = [m.strip().upper() for m in self.skip.text().split(",") if m.strip()]
        self.changed()

    def _save_processes(self) -> None:
        names = [n.strip() for n in self.procs.text().split(",") if n.strip()]
        self.cfg.game(self.game.id).processes = names
        self.changed(restart=f"{self.game.name} executable")

    def load(self) -> None:
        self._loading = True
        gs = self.cfg.game(self.game.id)
        self.switch.setChecked(gs.enabled)
        if self.mode is not None:
            mode = self.cfg.game("league").mode
            self.mode.set_value(mode)
            self.mode_hint.setText(RecordingModes.TIPS.get(mode, ""))
        if self.game.id == "league":
            self.skip.setText(", ".join(self.cfg.auto.skip_modes))
        if self.game.id == "cs2":
            self.cs2_port.setValue(int(self._cs2_opts()["gsi_port"]))
            self._refresh_cs2()
        if hasattr(self, "procs"):
            self.procs.setText(", ".join(gs.processes))
        self._loading = False


# ======================================================================== capture

class CaptureSection(SettingsSection):
    key, title, icon = "capture", "Capture", "camera"
    RESOLUTIONS = [("native", "Native (your screen)"), ("1440p", "1440p (QHD)"), ("1080p", "1080p (Full HD)"),
                   ("720p", "720p (HD)"), ("480p", "480p")]
    BITRATES = [4000, 6000, 8000, 12000, 16000, 20000, 30000, 40000, 50000]

    def __init__(self, page) -> None:
        super().__init__(page)
        self.body.addWidget(SectionHeader("Video", "Control your video resolution, frame rate and quality"))
        self.banner = Banner()
        self.banner.button.clicked.connect(self._banner_action)
        self.body.addWidget(self.banner)

        cards = QGridLayout()
        cards.setSpacing(8)
        self.cards: dict[str, PresetCard] = {}
        presets = [(p.key, p.title, p.subtitle) for p in VideoPresets.ALL]
        presets.append((VideoPresets.CUSTOM, "Custom", "Use your own recipe"))
        for i, (key, title, sub) in enumerate(presets):
            card = PresetCard(key, title, sub)
            card.clicked.connect(self._pick_preset)
            self.cards[key] = card
            cards.addWidget(card, i // 2, i % 2, 1, 2 if key == VideoPresets.CUSTOM else 1)
        self.body.addLayout(cards)

        self.resolution = QComboBox()
        for key, label in self.RESOLUTIONS:
            self.resolution.addItem(label, key)
        self.resolution.setFixedWidth(300)
        self.resolution.currentIndexChanged.connect(lambda _: self._set("resolution", self.resolution.currentData()))
        self.fps = SegmentedControl([(f, str(f)) for f in (30, 60, 120, 144)])
        self.fps.setFixedWidth(300)
        self.fps.changed.connect(lambda v: self._set("fps", v))
        self.mode = SegmentedControl([("quality", "Constant quality"), ("bitrate", "Bitrate")])
        self.mode.setFixedWidth(300)
        self.mode.changed.connect(lambda v: self._set("rate_control", v))
        self.quality = QSpinBox()
        self.quality.setRange(14, 32)
        self.quality.setFixedWidth(300)
        self.quality.valueChanged.connect(lambda v: self._set("quality", v))
        self.bitrate = QComboBox()
        self.bitrate.setEditable(True)
        for b in self.BITRATES:
            self.bitrate.addItem(f"{b}", b)
        self.bitrate.setFixedWidth(300)
        self.bitrate.currentTextChanged.connect(self._set_bitrate)

        self.body.addWidget(SettingRow("Resolution", self.resolution, "Never upscales: picks native if your screen is smaller"))
        self.body.addWidget(SettingRow("Frame rate (FPS)", self.fps))
        self.body.addWidget(SettingRow("Rate control", self.mode,
                                       "Constant quality adapts file size to the action; bitrate keeps it fixed"))
        self.quality_row = SettingRow("Quality", self.quality, "Lower = sharper and bigger files. 18-24 is sensible")
        self.bitrate_row = SettingRow("Bitrate (kbps)", self.bitrate, "12000 is plenty for 1080p60")
        self.body.addWidget(self.quality_row)
        self.body.addWidget(self.bitrate_row)

        self.body.addWidget(self.label("Capture"))
        self.encoder = QComboBox()
        for key in ("auto", *FFmpeg.ENCODERS):
            self.encoder.addItem("Automatic (best available)" if key == "auto" else ENCODER_NAMES[key], key)
        self.encoder.setFixedWidth(300)
        self.encoder.currentIndexChanged.connect(
            lambda _: self._set("encoder", self.encoder.currentData(), restart="encoder"))
        self.monitor = QSpinBox()
        self.monitor.setRange(0, 8)
        self.monitor.setFixedWidth(300)
        self.monitor.valueChanged.connect(lambda v: self._set("monitor", v))
        self.body.addWidget(SettingRow("Encoder", self.encoder, "Hardware encoders cost almost no in-game FPS"))
        self.body.addWidget(SettingRow("Monitor", self.monitor, "0 = main display, 1 = second display ..."))
        cap = self.cfg.capture
        self.toggle_row("Show mouse cursor", "", lambda: cap.draw_mouse, lambda on: setattr(cap, "draw_mouse", on))
        self.audio = AudioSettings(self)
        self.body.addWidget(self.audio)
        self.finish()

    # ---------- behaviour ----------

    def _set(self, field: str, value, restart: str | None = None) -> None:
        if self._loading or value is None:
            return
        setattr(self.cfg.capture, field, value)
        self._sync_presets()
        self.changed(restart)
        if field in ("resolution", "monitor"):
            self.page.win.replan_video()

    def _set_bitrate(self, text: str) -> None:
        digits = "".join(ch for ch in text if ch.isdigit())
        if digits:
            self._set("bitrate_kbps", max(1000, min(200000, int(digits))))

    def _pick_preset(self, key: str) -> None:
        preset = VideoPresets.get(key)
        if preset is None:  # "Custom": keep current values, just show the controls
            self.cards[VideoPresets.CUSTOM].set_selected(True)
            for k, c in self.cards.items():
                c.set_selected(k == VideoPresets.CUSTOM)
            return
        preset.apply(self.cfg.capture)
        self.load()
        self.changed()
        self.page.win.replan_video()

    def _sync_presets(self) -> None:
        current = VideoPresets.current(self.cfg.capture)
        for key, card in self.cards.items():
            card.set_selected(key == current)
        bitrate = self.cfg.capture.rate_control == "bitrate"
        self.quality_row.setVisible(not bitrate)
        self.bitrate_row.setVisible(bitrate)

    def load(self) -> None:
        self._loading = True
        cap = self.cfg.capture
        self.resolution.setCurrentIndex(max(0, self.resolution.findData(cap.resolution)))
        self.fps.set_value(cap.fps)
        self.mode.set_value(cap.rate_control)
        self.quality.setValue(cap.quality)
        self.bitrate.setCurrentText(str(cap.bitrate_kbps))
        self.encoder.setCurrentIndex(max(0, self.encoder.findData(cap.encoder)))
        self.monitor.setValue(cap.monitor)
        self._sync_presets()
        self.audio.load()
        self._loading = False
        self.update_banner()

    def update_banner(self) -> None:
        engine = self.page.win.engine
        plan = engine.pipeline.last_plan if engine.pipeline else None
        if engine.recorder is None:
            self.banner.show_message("warning", "The recorder isn't running - open the Log page to see why.")
        elif engine.encoder == "x264":
            self.banner.show_message("warning", "Software encoding (x264) is in use - it costs in-game FPS.<br>"
                                     "Update your graphics driver so NVENC / AMF / Quick Sync can be used.")
        elif plan is not None and plan.scaling == "cpu":
            self.banner.show_message("warning", f"{plan.description}.<br>Record at native resolution to avoid it.",
                                     button="Use native")
        else:
            detail = f"<br><span style='color:{Palette.TEXT_MUTED}'>{plan.description}</span>" if plan else ""
            self.banner.show_message("ok", f"Hardware encoding: {ENCODER_NAMES.get(engine.encoder, engine.encoder)}"
                                           f" - minimal impact on in-game FPS.{detail}")

    def _banner_action(self) -> None:
        self._set("resolution", "native")
        self.load()


class AudioSettings(QWidget):
    """Capture > Audio: everything you hear, or only the game + chosen apps; microphone;
    per-source volume; optional separate tracks."""
    SYSTEM_NAMES = {"system", "svchost.exe", "explorer.exe", "dwm.exe", "csrss.exe", "services.exe", "lsass.exe",
                    "winlogon.exe", "conhost.exe", "runtimebroker.exe", "searchhost.exe", "sihost.exe",
                    "taskhostw.exe", "ctfmon.exe", "fontdrvhost.exe", "smss.exe", "wininit.exe", "registry",
                    "python.exe", "pythonw.exe", "ffmpeg.exe", "memory compression", "system idle process"}

    def __init__(self, section: "CaptureSection") -> None:
        super().__init__()
        self.section = section
        self.cfg = section.cfg
        self._loading = False
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addWidget(section.label("Audio"))
        cap = self.cfg.capture

        self.enabled = ToggleSwitch(cap.audio)
        self.enabled.toggled.connect(lambda on: self._set("audio", on))
        col.addWidget(SettingRow("Record audio", self.enabled))
        self.mode = SegmentedControl([("system", "Everything I hear"), ("isolated", "Game + chosen apps")])
        self.mode.setFixedWidth(340)
        self.mode.set_tooltips({"system": "Every sound on your PC: game, Discord, music, notifications",
                                "isolated": "Only the game you're playing plus the apps you pick below. "
                                            "Needs Windows 10 (2004) or newer"})
        self.mode.changed.connect(lambda v: self._set("audio_mode", v))
        col.addWidget(SettingRow("Sound source", self.mode))

        self.system_vol = self._volume(lambda v: self._set("system_volume", v))
        self.system_row = SettingRow("Everything I hear - volume", self.system_vol)
        col.addWidget(self.system_row)

        self.game_on = ToggleSwitch(cap.game_audio)
        self.game_on.toggled.connect(lambda on: self._set("game_audio", on))
        self.game_vol = self._volume(lambda v: self._set("game_volume", v))
        self.game_row = SettingRow("Game sound", self._pair(self.game_vol, self.game_on),
                                   "The game being recorded - detected automatically")
        col.addWidget(self.game_row)

        self.apps_box = QVBoxLayout()
        self.apps_box.setSpacing(4)
        col.addLayout(self.apps_box)
        self.add_combo = QComboBox()
        self.add_combo.setEditable(True)
        self.add_combo.setFixedWidth(240)
        self.add_combo.lineEdit().setPlaceholderText("Discord.exe")
        refresh = IconTextButton("restart", "")
        refresh.setToolTip("Refresh the list of running apps")
        refresh.clicked.connect(self._fill_running)
        add = QPushButton("Add app")
        add.clicked.connect(self._add_app)
        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.addWidget(self.add_combo)
        add_row.addWidget(refresh)
        add_row.addWidget(add)
        add_box = QWidget()
        add_box.setLayout(add_row)
        self.add_row = SettingRow("Also record an app", add_box, "e.g. Discord for your friends' voices. "
                                                               "Pick a running app or type its .exe name")
        col.addWidget(self.add_row)

        self.mic_on = ToggleSwitch(cap.mic)
        self.mic_on.toggled.connect(lambda on: self._set("mic", on))
        self.mic_vol = self._volume(lambda v: self._set("mic_volume", v))
        col.addWidget(SettingRow("Microphone", self._pair(self.mic_vol, self.mic_on), "Your default Windows microphone"))

        self.tracks = ToggleSwitch(cap.audio_tracks)
        self.tracks.toggled.connect(lambda on: self._set("audio_tracks", on))
        col.addWidget(SettingRow("Separate audio tracks", self.tracks,
                                 "Track 1 is the mix everyone hears. Each source also gets its own track, so you "
                                 "can mute your mic or Discord later in an editor (DaVinci, Premiere)"))
        self._fill_running()

    # ---------- building blocks ----------

    @staticmethod
    def _volume(on_change) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 200)
        spin.setSuffix(" %")
        spin.setFixedWidth(90)
        spin.valueChanged.connect(on_change)
        return spin

    @staticmethod
    def _pair(*widgets) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        for w in widgets:
            row.addWidget(w)
        return box

    def _set(self, field: str, value) -> None:
        if self._loading:
            return
        setattr(self.cfg.capture, field, value)
        self._sync()
        self.section.changed()

    def _sync(self) -> None:
        cap = self.cfg.capture
        isolated = cap.audio_mode == "isolated"
        for w in (self.game_row, self.add_row):
            w.setVisible(isolated)
        self.system_row.setVisible(not isolated)
        for i in range(self.apps_box.count()):
            item = self.apps_box.itemAt(i).widget()
            if item is not None:
                item.setVisible(isolated)
        for w in (self.mode, self.system_vol, self.game_on, self.game_vol, self.mic_on, self.mic_vol,
                  self.tracks, self.add_combo):
            w.setEnabled(cap.audio)

    # ---------- apps ----------

    def _fill_running(self) -> None:
        try:
            from Games.processes import ProcessSnapshot
            names = sorted(n for n in ProcessSnapshot._scan() if n.endswith(".exe") and n not in self.SYSTEM_NAMES)
        except Exception:
            names = []
        current = self.add_combo.currentText()
        self.add_combo.clear()
        self.add_combo.addItems(names)
        self.add_combo.setCurrentText(current)

    def _add_app(self) -> None:
        exe = self.add_combo.currentText().strip()
        if not exe:
            return
        if not exe.lower().endswith(".exe"):
            exe += ".exe"
        apps = self.cfg.capture.apps
        if any(a.get("exe", "").lower() == exe.lower() for a in apps):
            return
        apps.append({"exe": exe, "volume": 100, "enabled": True})
        self.add_combo.setCurrentText("")
        self._build_apps()
        self.section.changed()

    def _build_apps(self) -> None:
        while self.apps_box.count():
            w = self.apps_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for app in self.cfg.capture.apps:
            on = ToggleSwitch(app.get("enabled", True))
            vol = self._volume(lambda v, a=app: (a.__setitem__("volume", v), self.section.changed()))
            vol.setValue(int(app.get("volume", 100)))
            on.toggled.connect(lambda checked, a=app: (a.__setitem__("enabled", checked), self.section.changed()))
            remove = IconButton("trash", f"Stop recording {app['exe']}")
            remove.clicked.connect(lambda _=False, a=app: self._remove_app(a))
            self.apps_box.addWidget(SettingRow(app["exe"].rsplit(".", 1)[0], self._pair(vol, on, remove),
                                               app["exe"]))
        self._sync()

    def _remove_app(self, app: dict) -> None:
        self.cfg.capture.apps.remove(app)
        self._build_apps()
        self.section.changed()

    def load(self) -> None:
        self._loading = True
        cap = self.cfg.capture
        self.enabled.setChecked(cap.audio)
        self.mode.set_value(cap.audio_mode)
        self.system_vol.setValue(cap.system_volume)
        self.game_on.setChecked(cap.game_audio)
        self.game_vol.setValue(cap.game_volume)
        self.mic_on.setChecked(cap.mic)
        self.mic_vol.setValue(cap.mic_volume)
        self.tracks.setChecked(cap.audio_tracks)
        self._build_apps()
        self._loading = False
        self._sync()


# ======================================================================== other sections

class AutoRecordSection(SettingsSection):
    key, title, icon = "auto", "Auto-record", "bolt"

    def __init__(self, page) -> None:
        super().__init__(page)
        a = self.cfg.auto
        self.body.addWidget(SectionHeader("Auto-record", "What happens when a match starts and ends"))
        self.master = self.toggle_row("Auto-record supported games", "Turn off to only record manually",
                                      lambda: a.enabled, lambda on: setattr(a, "enabled", on))
        self.post = self.seconds()
        self.post.valueChanged.connect(lambda v: (setattr(a, "post_roll_seconds", v), self.changed()))
        self.body.addWidget(SettingRow("Keep recording after the game ends", self.post, "Catches the victory screen"))
        self.grace = self.seconds(120)
        self.grace.valueChanged.connect(lambda v: (setattr(a, "end_grace_seconds", v), self.changed()))
        self.body.addWidget(SettingRow("Wait after a crash or disconnect", self.grace,
                                       "Short drops won't split the match into two files"))
        self.chapters = self.toggle_row("Highlight chapters in the video", "Jump between moments in any video player",
                                        lambda: a.chapters, lambda on: setattr(a, "chapters", on))
        self.rename = self.toggle_row("Result in the file name", "e.g. LoL_..._Ahri_Win_7-2-5.mp4",
                                      lambda: a.rename_with_result, lambda on: setattr(a, "rename_with_result", on))
        self.finish()

    def load(self) -> None:
        self._loading = True
        a = self.cfg.auto
        self.master.setChecked(a.enabled)
        self.post.setValue(a.post_roll_seconds)
        self.grace.setValue(a.end_grace_seconds)
        self.chapters.setChecked(a.chapters)
        self.rename.setChecked(a.rename_with_result)
        self._loading = False


class ClipsSection(SettingsSection):
    key, title, icon = "clips", "Clips", "scissors"
    DISCORD_LIMITS = ((20, "20 MB - free account"), (50, "50 MB - Nitro Basic"), (500, "500 MB - Nitro"))

    def __init__(self, page) -> None:
        super().__init__(page)
        c = self.cfg.clips
        self.body.addWidget(SectionHeader("Clips", "Defaults for exported clips and highlight reels"))
        self.pre, self.post = self.seconds(), self.seconds()
        self.pre.valueChanged.connect(lambda v: self._set("pre_seconds", v))
        self.post.valueChanged.connect(lambda v: self._set("post_seconds", v))
        self.body.addWidget(SettingRow("Before a highlight", self.pre, "How much build-up each clip includes"))
        self.body.addWidget(SettingRow("After a highlight", self.post))
        self.folder = QLineEdit()
        self.folder.setFixedWidth(260)
        self.folder.editingFinished.connect(lambda: self._set("folder", self.folder.text().strip() or "Clips"))
        self.body.addWidget(SettingRow("Clips folder", self.folder, "Relative to the recordings folder, or a full path"))
        self.discord = QComboBox()
        for mb, label in self.DISCORD_LIMITS:
            self.discord.addItem(label, mb)
        self.discord.setFixedWidth(260)
        self.discord.currentIndexChanged.connect(lambda _: self._set("discord_limit_mb", self.discord.currentData()))
        self.body.addWidget(SettingRow("Discord upload limit", self.discord,
                                       "Share clip and 'Fit for Discord' shrink clips to stay under this"))
        self.finish()

    def _set(self, field: str, value) -> None:
        if self._loading:
            return
        setattr(self.cfg.clips, field, value)
        self.page.win.sessions.apply_clip_defaults()
        self.changed()

    def load(self) -> None:
        self._loading = True
        c = self.cfg.clips
        self.pre.setValue(c.pre_seconds)
        self.post.setValue(c.post_seconds)
        self.folder.setText(c.folder)
        self.discord.setCurrentIndex(max(0, self.discord.findData(c.discord_limit_mb)))
        self._loading = False


class StorageSection(SettingsSection):
    key, title, icon = "storage", "Storage", "hdd"

    def __init__(self, page) -> None:
        super().__init__(page)
        st = self.cfg.storage
        self.body.addWidget(SectionHeader("Storage", "Where recordings go, how much space they use, and when to clean up"))
        self.folder = QLineEdit()
        self.folder.editingFinished.connect(self._set_folder)
        browse = IconTextButton("folder", "Browse...")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        box = QWidget()
        box.setLayout(row)
        box.setFixedWidth(420)
        self.body.addWidget(SettingRow("Recordings folder", box))

        self.body.addWidget(self.label("Drive usage"))
        self.bar = UsageBar()
        self.body.addWidget(self.bar)
        legend = QGridLayout()
        legend.setHorizontalSpacing(24)
        self.leg_rec = LegendItem(Palette.USAGE_RECORDINGS, "Recordings")
        self.leg_clips = LegendItem(Palette.USAGE_CLIPS, "Clips")
        self.leg_other = LegendItem(Palette.USAGE_OTHER, "Other files")
        self.leg_free = LegendItem("#39414c", "Free")
        self.leg_limit = LegendItem(Palette.TEXT, "Auto-delete limit", dashed=True)
        for i, item in enumerate((self.leg_rec, self.leg_clips, self.leg_other, self.leg_free)):
            legend.addWidget(item, i // 2, i % 2)
        self.body.addLayout(legend)
        self.limit_title = self.label("Recordings vs auto-delete limit")
        self.limit_bar = UsageBar()
        limit_legend = QHBoxLayout()
        self.leg_rec2 = LegendItem(Palette.USAGE_RECORDINGS, "Recordings")
        limit_legend.addWidget(self.leg_rec2)
        limit_legend.addWidget(self.leg_limit)
        self.limit_box = QWidget()
        box_layout = QVBoxLayout(self.limit_box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.addWidget(self.limit_title)
        box_layout.addWidget(self.limit_bar)
        box_layout.addLayout(limit_legend)
        self.body.addWidget(self.limit_box)

        self.body.addWidget(self.label("Auto-delete"))
        self.limit_switch = self.toggle_row(
            "Limit recordings size", "When recordings go over the limit, the oldest ones are deleted",
            lambda: st.limit_enabled, lambda on: self._set_limit("limit_enabled", on))
        self.limit = QDoubleSpinBox()
        self.limit.setRange(1, 100000)
        self.limit.setDecimals(0)
        self.limit.setSingleStep(10)
        self.limit.setSuffix(" GB")
        self.limit.setFixedWidth(160)
        self.limit.valueChanged.connect(lambda v: self._set_limit("limit_gb", v))
        self.body.addWidget(SettingRow("Maximum size", self.limit, "Recordings only - your exported clips are never deleted"))
        self.keep_fav = self.toggle_row(
            "Keep recordings with favourites", "Recordings with a starred highlight are never auto-deleted",
            lambda: st.keep_favorites, lambda on: self._set_limit("keep_favorites", on))
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.body.addWidget(self.preview)
        open_btn = IconTextButton("folder", "Open recordings folder")
        open_btn.clicked.connect(lambda: Shell.open_path(self.cfg.recording.resolved_output_dir()))
        self.body.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.finish()

    def _set_folder(self) -> None:
        value = self.folder.text().strip()
        if value and value != self.cfg.recording.output_dir and not self._loading:
            self.cfg.recording.output_dir = value
            self.changed(restart="recordings folder")

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Recordings folder", str(Path(self.folder.text()).expanduser()))
        if path:
            self.folder.setText(path)
            self._set_folder()

    def _set_limit(self, field: str, value) -> None:
        if self._loading:
            return
        setattr(self.cfg.storage, field, value)
        self.changed()
        self.refresh_usage()

    @staticmethod
    def _gb(n_bytes: float) -> str:
        return f"{n_bytes / 1024 ** 3:,.1f} GB"

    def load(self) -> None:
        self._loading = True
        st = self.cfg.storage
        self.folder.setText(self.cfg.recording.output_dir)
        self.limit_switch.setChecked(st.limit_enabled)
        self.limit.setValue(st.limit_gb)
        self.keep_fav.setChecked(st.keep_favorites)
        self._loading = False
        self.refresh_usage()

    def refresh_usage(self) -> None:
        manager = self.page.win.storage_manager()
        u = manager.usage()
        st = self.cfg.storage
        limit = st.limit_gb * 1024 ** 3 if st.limit_enabled else None
        self.bar.set_data([(u.recordings, Palette.USAGE_RECORDINGS), (u.clips, Palette.USAGE_CLIPS),
                           (u.other, Palette.USAGE_OTHER), (u.free, Palette.USAGE_FREE)], u.total)
        self.limit_box.setVisible(limit is not None)
        if limit is not None:  # zoomed in: recordings against the limit, so both are visible
            scale = max(limit, u.recordings) * 1.15
            over = u.recordings > limit
            self.limit_bar.set_data([(u.recordings, Palette.GOLD if over else Palette.USAGE_RECORDINGS)], scale, limit)
            pct = u.recordings * 100 / limit if limit else 0
            self.leg_rec2.set_value(f"{self._gb(u.recordings)} ({pct:.0f}% of the limit)")
        n_rec = len(manager.library.scan())
        self.leg_rec.set_value(f"{self._gb(u.recordings)} · {n_rec} file{'s' if n_rec != 1 else ''}")
        self.leg_clips.set_value(self._gb(u.clips))
        self.leg_other.set_value(self._gb(u.other))
        self.leg_free.set_value(f"{self._gb(u.free)} of {self._gb(u.total)}")
        self.leg_limit.set_value(self._gb(limit) if limit else "off")
        self.limit.setEnabled(st.limit_enabled)
        self.keep_fav.setEnabled(st.limit_enabled)

        if not st.limit_enabled:
            self.preview.setText("")
            return
        victims = manager.plan_cleanup(self.page.win.storage_exclusions())
        if victims:
            size = sum(manager.entry_bytes(e) for e in victims)
            self.preview.setText(f"<span style='color:{Palette.GOLD}'>Over the limit: the {len(victims)} oldest "
                                 f"recording{'s' if len(victims) != 1 else ''} ({self._gb(size)}) will be deleted "
                                 f"after the next recording (you'll be asked first if you leave this page).</span>")
        else:
            left = limit - u.recordings
            self.preview.setText(f"<span style='color:{Palette.WIN}'>Within the limit - "
                                 f"{self._gb(max(0, left))} left before anything is deleted.</span>")


class NotificationsSection(SettingsSection):
    key, title, icon = "notifications", "Notifications", "bell"

    def __init__(self, page) -> None:
        super().__init__(page)
        g = self.cfg.gui
        self.body.addWidget(SectionHeader("Notifications", "Pop-ups from the tray icon"))
        self.start = self.toggle_row("Recording started", "", lambda: g.notify_start,
                                     lambda on: setattr(g, "notify_start", on))
        self.saved = self.toggle_row("Recording saved", "Shows the result and how many highlights were found",
                                     lambda: g.notify_saved, lambda on: setattr(g, "notify_saved", on))
        self.finish()

    def load(self) -> None:
        self._loading = True
        self.start.setChecked(self.cfg.gui.notify_start)
        self.saved.setChecked(self.cfg.gui.notify_saved)
        self._loading = False


class AppSection(SettingsSection):
    key, title, icon = "app", "App", "app_window"

    def __init__(self, page) -> None:
        super().__init__(page)
        g, hk = self.cfg.gui, self.cfg.hotkeys
        self.body.addWidget(SectionHeader("App", "Window, tray and shortcuts"))
        self.tray = self.toggle_row("Keep running in the tray", "Closing the window keeps matches recording",
                                    lambda: g.minimize_to_tray, lambda on: setattr(g, "minimize_to_tray", on))
        self.start_min = self.toggle_row("Start minimised", "Open straight to the tray",
                                         lambda: g.start_minimized, lambda on: setattr(g, "start_minimized", on))
        self.body.addWidget(self.label("Hotkeys"))
        for name, combo in (("Start / stop recording", hk.toggle), ("Bookmark a highlight", hk.bookmark),
                            ("Quit GameCapture", hk.quit)):
            value = QLabel(Format.hotkey(combo))
            value.setObjectName("Muted")
            self.body.addWidget(SettingRow(name, value))
        hint = QLabel("Hotkeys work while in game. Change them in config.json (restart to apply).")
        hint.setObjectName("Muted")
        self.body.addWidget(hint)
        self.body.addWidget(self.label("Files"))
        row = QHBoxLayout()
        cfg_btn = IconTextButton("app_window", "Open config.json")
        cfg_btn.clicked.connect(lambda: Shell.reveal(Paths.CONFIG))
        log_btn = IconTextButton("folder", "Open logs folder")
        log_btn.clicked.connect(lambda: Shell.open_path(self.cfg.resolved_log_dir()))
        row.addWidget(cfg_btn)
        row.addWidget(log_btn)
        row.addStretch()
        self.body.addLayout(row)
        self.finish()

    def load(self) -> None:
        self._loading = True
        self.tray.setChecked(self.cfg.gui.minimize_to_tray)
        self.start_min.setChecked(self.cfg.gui.start_minimized)
        self._loading = False
