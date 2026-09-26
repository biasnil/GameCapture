"""The app's name and version - shown in the window title, Settings > App, the log and the .exe's file
properties. Bump VERSION for every build you share (build.bat names the zip after it)."""
from __future__ import annotations


class AppInfo:
    NAME = "GameCapture"
    VERSION = "1.0.0"

    @classmethod
    def title(cls) -> str:
        return f"{cls.NAME} {cls.VERSION}"

    @classmethod
    def version_tuple(cls) -> tuple[int, int, int, int]:
        parts = [int(p) for p in cls.VERSION.split(".") if p.isdigit()][:4]
        return tuple(parts + [0] * (4 - len(parts)))  # type: ignore[return-value]
