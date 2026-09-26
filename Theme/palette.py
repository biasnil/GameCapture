"""All colours in one place. The UI only ever refers to these names."""
from __future__ import annotations


class Palette:
    ACCENT = "#3dd6c6"
    ACCENT_HOVER = "#5ee3d6"
    ACCENT_DISABLED_BG = "#264a47"

    BG_WINDOW = "#15181d"
    BG_SIDEBAR = "#0e1014"
    BG_BASE = "#0f1216"
    BG_PANEL = "#11141a"
    BG_CARD = "#1b1f26"
    BG_CARD_HOVER = "#20252d"
    BG_CARD_SELECTED = "#1f2a2e"
    BG_BUTTON = "#232830"
    BG_BUTTON_HOVER = "#2b313a"
    BG_SELECTED = "#262d36"
    BG_HOVER = "#1d2229"

    BORDER = "#2f3640"
    BORDER_SOFT = "#232933"
    TRACK = "#2a3038"
    TICK_MAJOR = "#4a525c"
    TICK_MINOR = "#30363d"

    TEXT = "#e6edf3"
    TEXT_MUTED = "#7d8590"
    TEXT_DISABLED = "#5b636d"
    TEXT_ON_ACCENT = "#0b0e11"

    WIN = "#3ecf6b"
    LOSE = "#e5484d"
    GOLD = "#f5c542"
    REC = "#e5484d"
    REC_BG = "#3a1d22"
    REC_BORDER = "#5c2630"
    REC_TEXT = "#ff8a93"

    # storage legend
    USAGE_RECORDINGS = "#3dd6c6"
    USAGE_CLIPS = "#f5c542"
    USAGE_OTHER = "#4a525c"
    USAGE_FREE = "#1b1f26"

    STATE = {"offline": "#6e7681", "ready": "#3ecf6b", "match": "#f5c542", "recording": "#e5484d"}


class MarkerStyle:
    """Colour + SVG icon (Assets/marker_*.svg) for each highlight type."""
    COLORS = {
        "kill": "#3ecf6b", "multikill": "#f5c542", "death": "#e5484d", "assist": "#4c9ffe",
        "objective": "#a371f7", "first_blood": "#ff8c42", "ace": "#ff8c42", "structure": "#8b949e",
        "other_kill": "#6e7681", "game_start": "#8b949e", "game_end": "#8b949e", "bookmark": "#ff7ab6",
    }
    ICONS = {
        "kill": "marker_kill", "multikill": "marker_kill", "death": "marker_death",
        "assist": "marker_assist", "objective": "marker_objective", "first_blood": "marker_first_blood",
        "ace": "marker_ace", "structure": "marker_structure", "game_end": "marker_game_end",
        "game_start": "marker_game_start", "bookmark": "marker_bookmark",
    }
    DEFAULT_COLOR = "#8b949e"

    @classmethod
    def color(cls, kind: str) -> str:
        return cls.COLORS.get(kind, cls.DEFAULT_COLOR)

    @classmethod
    def icon(cls, kind: str) -> str:
        return cls.ICONS.get(kind, "marker_other")
