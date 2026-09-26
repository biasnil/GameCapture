"""config.json <-> typed settings objects. The file lives in the app data folder (see Core/paths.py)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from Core.paths import Paths


def _known(cls, data: dict) -> dict:
    """Keep only keys the dataclass knows, so stray keys in config.json don't crash."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass
class CaptureSettings:
    monitor: int = 0            # 0 = main display, 1 = second display, ...
    fps: int = 60
    resolution: str = "1080p"   # native | 1440p | 1080p | 720p | 480p (never upscales)
    rate_control: str = "quality"  # quality (constant quality) | bitrate (fixed kbps)
    quality: int = 21           # quality mode: lower = better / bigger (18-24 is sensible)
    bitrate_kbps: int = 12000   # bitrate mode
    draw_mouse: bool = True
    encoder: str = "auto"       # auto | nvenc | amf | qsv | x264
    audio: bool = True          # record audio at all
    # --- audio isolation ---
    audio_mode: str = "system"  # system = everything you hear | isolated = only the game + chosen apps
    system_volume: int = 100    # %, "everything you hear"
    game_audio: bool = True     # isolated: the game being recorded
    game_volume: int = 100
    mic: bool = False           # add your microphone (either mode)
    mic_volume: int = 100
    apps: list[dict] = field(default_factory=list)  # isolated: [{"exe": "Discord.exe", "volume": 100, "enabled": True}]
    audio_tracks: bool = False  # track 1 = mix, then one track per source (for editing)


@dataclass
class RecordingSettings:
    output_dir: str = "~/Videos/GameCapture"
    filename_prefix: str = "Recording"   # manual recordings; auto-recordings use the game's prefix
    final_format: str = "mp4"   # mp4 (remuxed after stop) or mkv
    keep_mkv: bool = False      # keep the crash-safe .mkv after remuxing

    def resolved_output_dir(self) -> Path:
        return Paths.ensure_dir(self.output_dir).resolve()


@dataclass
class AutoSettings:
    enabled: bool = True             # auto-record games
    poll_interval: float = 1.0       # seconds between checks of the game API
    end_grace_seconds: float = 15.0  # API silent this long (no GameEnd) = match over
    post_roll_seconds: float = 5.0   # keep recording this long after GameEnd
    skip_modes: list[str] = field(default_factory=lambda: ["PRACTICETOOL"])
    chapters: bool = True            # embed highlight chapters in the video file
    rename_with_result: bool = True  # <Game>_<time>_<Character>_Win_7-2-5.mp4 (games that report a result)


@dataclass
class ClipSettings:
    pre_seconds: float = 10.0
    post_seconds: float = 5.0
    folder: str = "Clips"            # relative to the recordings folder
    discord_limit_mb: int = 20       # Discord upload limit: 20 free, 50 Nitro Basic, 500 Nitro

    def resolved_dir(self, output_dir: Path) -> Path:
        path = Path(self.folder).expanduser()
        path = path if path.is_absolute() else output_dir / path
        path.mkdir(parents=True, exist_ok=True)
        return path


@dataclass
class StorageSettings:
    limit_enabled: bool = False      # auto-delete the oldest recordings above limit_gb
    limit_gb: float = 100.0
    keep_favorites: bool = True      # never auto-delete a recording with a starred highlight


@dataclass
class GuiSettings:
    minimize_to_tray: bool = True
    start_minimized: bool = False
    notify_start: bool = True        # tray notification when a recording starts
    notify_saved: bool = True        # tray notification when a recording is saved
    precache: bool = True            # preload the next highlight / editor clip so jumps don't stutter
                                     # (never while recording, so it can't cost in-game FPS)


@dataclass
class GameSettings:
    enabled: bool = True             # auto-record this game
    mode: str = "match"              # session (game open -> closed) | match | highlights (keep only highlights)
    processes: list[str] = field(default_factory=list)  # override the game's executable names (if they change)
    options: dict = field(default_factory=dict)          # game-specific extras (e.g. CS2 integration port/token)
    name: str = ""                   # games you added yourself: display name (empty for built-in games)
    custom: bool = False             # True = added in Settings > Games > Add a game

    MODES = ("session", "match", "highlights")


@dataclass
class HotkeySettings:
    toggle: str = "<ctrl>+<alt>+r"
    bookmark: str = "<ctrl>+<alt>+b"   # mark a highlight by hand, in any game
    quit: str = "<ctrl>+<alt>+q"


@dataclass
class AppConfig:
    capture: CaptureSettings = field(default_factory=CaptureSettings)
    recording: RecordingSettings = field(default_factory=RecordingSettings)
    hotkeys: HotkeySettings = field(default_factory=HotkeySettings)
    auto: AutoSettings = field(default_factory=AutoSettings)
    clips: ClipSettings = field(default_factory=ClipSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    gui: GuiSettings = field(default_factory=GuiSettings)
    games: dict[str, GameSettings] = field(default_factory=dict)  # filled in as games are seen
    ffmpeg_path: str = ""            # blank = Bin/ffmpeg.exe, then PATH
    log_dir: str = "Logs"            # relative = inside the app data folder
    log_level: str = "INFO"
    stop_on_exit: bool = True

    SECTIONS = {"capture": CaptureSettings, "recording": RecordingSettings, "hotkeys": HotkeySettings,
                "auto": AutoSettings, "clips": ClipSettings, "storage": StorageSettings, "gui": GuiSettings}

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        if path is None:
            path = Paths.CONFIG
            Paths.migrate_legacy_config(path)  # settings used to live next to the code
        if not path.exists():
            cfg = cls()
            cfg.save(path)
            print(f"Created default config at {path}")
            return cfg
        raw = json.loads(path.read_text(encoding="utf-8"))
        games = {gid: GameSettings(**_known(GameSettings, g)) for gid, g in (raw.get("games") or {}).items()}
        cfg = cls(
            **{name: sec(**_known(sec, raw.get(name, {}))) for name, sec in cls.SECTIONS.items()},
            **{k: v for k, v in _known(cls, raw).items() if k not in cls.SECTIONS and k != "games"},
        )
        cfg.games.update(games)
        return cfg

    def game(self, game_id: str) -> GameSettings:
        """Per-game settings (created with the game's defaults the first time it is seen)."""
        gs = self.games.get(game_id)
        if gs is None:
            from Games.registry import GameRegistry
            info = GameRegistry.get(game_id)
            gs = self.games[game_id] = GameSettings(enabled=info.default_on if info else True)
        return gs

    def custom_games(self) -> dict[str, GameSettings]:
        return {gid: gs for gid, gs in self.games.items() if gs.custom}

    def save(self, path: Path | None = None) -> None:
        path = path or Paths.CONFIG
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)  # never leave a half-written config behind

    def resolved_log_dir(self) -> Path:
        return Paths.ensure_dir(self.log_dir)
