"""Where everything lives, relative to the project root."""
from __future__ import annotations

from pathlib import Path


class Paths:
    ROOT = Path(__file__).resolve().parent.parent
    ASSETS = ROOT / "Assets"
    BIN = ROOT / "Bin"
    CONFIG = ROOT / "config.json"

    @classmethod
    def resolve(cls, value: str | Path) -> Path:
        """Expand ~ and make relative paths relative to the project root."""
        path = Path(value).expanduser()
        return path if path.is_absolute() else cls.ROOT / path

    @classmethod
    def ensure_dir(cls, value: str | Path) -> Path:
        path = cls.resolve(value)
        path.mkdir(parents=True, exist_ok=True)
        return path
