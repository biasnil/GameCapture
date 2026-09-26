"""Text helpers shared by the recorder, clip export and the UI."""
from __future__ import annotations

import re


class Format:
    @staticmethod
    def duration(seconds: float) -> str:
        """75 -> '01:15', 3725 -> '1:02:05'."""
        m, s = divmod(int(max(0, seconds)), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    @staticmethod
    def safe_filename(text: str) -> str:
        """Kai'Sa -> KaiSa, Nunu & Willump -> NunuWillump."""
        return re.sub(r"[^A-Za-z0-9_\-]+", "", text)

    @staticmethod
    def slug(text: str, limit: int = 40) -> str:
        """'Killed Bob + Double kill' -> 'Killed_Bob_Double_kill' (for clip names)."""
        return re.sub(r"[^A-Za-z0-9_\-]+", "_", text).strip("_")[:limit] or "clip"

    @staticmethod
    def ffmeta_escape(text: str) -> str:
        """Escape characters that are special in ffmpeg's FFMETADATA files."""
        return re.sub(r"([=;#\\\n])", r"\\\1", text)

    @staticmethod
    def hotkey(combo: str) -> str:
        """'<ctrl>+<alt>+r' -> 'Ctrl+Alt+R'."""
        return "+".join(part.strip("<>").capitalize() if len(part.strip("<>")) > 1 else part.upper()
                        for part in combo.split("+"))
