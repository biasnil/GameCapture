"""Where everything lives.

The program (Assets, Bin) stays next to the code. Your settings and app data live in your
user profile, so updating or moving GameCapture never touches them:

    Windows   %APPDATA%\\GameCapture\\        config.json, Logs\\, Projects\\
    others    ~/.config/GameCapture/

Set GAMECAPTURE_HOME to use another folder (portable installs, tests)."""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger("gamecapture")


def _data_dir() -> Path:
    override = os.environ.get("GAMECAPTURE_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "GameCapture"
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "GameCapture"


def _bin_dir(app_dir: Path, bundle: Path) -> Path:
    """Bin\\ next to GameCapture.exe wins (so ffmpeg can be swapped), else the one bundled inside."""
    beside = app_dir / "Bin"
    return beside if (beside / "ffmpeg.exe").exists() or not (bundle / "Bin").exists() else bundle / "Bin"


class Paths:
    FROZEN = bool(getattr(sys, "frozen", False))              # running as the built GameCapture.exe
    ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))  # bundled files
    APP_DIR = Path(sys.executable).resolve().parent if FROZEN else ROOT              # where the .exe is
    ASSETS = ROOT / "Assets"
    BIN = _bin_dir(APP_DIR, ROOT) if FROZEN else ROOT / "Bin"
    LEGACY_CONFIG = APP_DIR / "config.json"   # before settings moved to the user profile
    DATA = _data_dir()
    CONFIG = DATA / "config.json"
    PROJECTS = DATA / "Projects"          # video editor projects

    @classmethod
    def resolve(cls, value: str | Path) -> Path:
        """Expand ~ and make relative paths relative to the app data folder."""
        path = Path(value).expanduser()
        return path if path.is_absolute() else cls.DATA / path

    @classmethod
    def ensure_dir(cls, value: str | Path) -> Path:
        path = cls.resolve(value)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def migrate_legacy_config(cls, target: Path | None = None, legacy: Path | None = None) -> bool:
        """Copy an old project-folder config.json to the app data folder, once.
        The old file is left where it is (it's harmless, and deleting settings is never worth the risk)."""
        target = target or cls.CONFIG
        legacy = legacy or cls.LEGACY_CONFIG
        if target.exists() or not legacy.is_file():
            return False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, target)
        except OSError as exc:
            log.warning("Could not move %s to %s: %s", legacy, target, exc)
            return False
        log.info("Settings moved to %s", target)
        return True
