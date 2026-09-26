"""Supported games. Adding a game = registering a GameInfo here (plus a watcher if it has an API)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, eq=False)
class GameInfo:
    id: str                     # stable key stored in sidecars - never change it once released
    name: str                   # display name
    prefix: str                 # filename prefix
    mode_names: dict[str, str] = field(default_factory=dict)  # API mode id -> readable name
    support: str = "planned"    # "highlights" | "auto" | "planned"
    processes: tuple[str, ...] = ()   # executable names that mean "the game is running"
    color: str = "#3dd6c6"      # tile badge colour
    how: str = ""               # one line for Settings: how detection works

    @property
    def supported(self) -> bool:
        return self.support != "planned"

    @property
    def status_text(self) -> str:
        return {"highlights": "Auto-record + highlights", "auto": "Auto-record + bookmarks"}.get(self.support, "Planned")

    @property
    def initials(self) -> str:
        words = [w for w in self.name.replace("-", " ").replace(".", " ").split()
                 if w[0].isalnum() and w.lower() not in ("of", "the", "and")]
        return ("".join(w[0] for w in words[:2]) or self.name[:2]).upper()


class RecordingModes:
    """The three recording modes (League of Legends / TFT), with the wording used in the UI."""
    OPTIONS = (("session", "Session"), ("match", "Match"), ("highlights", "Highlights"))
    TIPS = {
        "session": "Record from when you open League until you close it - one video with every match",
        "match": "Record each match on its own (from game start to the end screen)",
        "highlights": "Record the match, then keep only a short video of your highlights and delete the rest",
    }


class GameRegistry:
    LEAGUE = GameInfo("league", "League of Legends", "LoL", {
        "CLASSIC": "Summoner's Rift", "ARAM": "ARAM", "URF": "URF", "ARURF": "ARURF",
        "CHERRY": "Arena", "ONEFORALL": "One for All", "NEXUSBLITZ": "Nexus Blitz",
        "PRACTICETOOL": "Practice Tool", "SWIFTPLAY": "Swiftplay", "ULTBOOK": "Ultimate Spellbook",
    }, "highlights", ("LeagueClient.exe", "LeagueClientUx.exe", "League of Legends.exe"), "#c89b3c",
        "Riot's local Live Client Data API: kills, deaths, assists, multikills, objectives, aces.")
    TFT = GameInfo("tft", "Teamfight Tactics", "TFT", {"TFT": "Teamfight Tactics"}, "auto",
                   ("League of Legends.exe",), "#e0b44c",
                   "Detected through League's local API (game mode TFT). Riot's API has no in-match "
                   "events for TFT, so use the Bookmark hotkey for highlights.")
    CS2 = GameInfo("cs2", "Counter-Strike 2", "CS2", {
        "competitive": "Competitive", "premier": "Premier", "casual": "Casual", "deathmatch": "Deathmatch",
        "scrimcomp2v2": "Wingman", "gungameprogressive": "Arms Race", "skirmish": "War Games",
    }, "highlights", ("cs2.exe",), "#de9b35",
        "Valve's official Game State Integration (VAC-safe): kills, headshots, multikills, ACE, deaths, "
        "assists, round MVPs, final score.")
    APEX = GameInfo("apex", "Apex Legends", "Apex", {}, "auto", ("r5apex.exe", "r5apex_dx12.exe"), "#cd3333",
                    "No game API - records the whole time the game is open. Use the Bookmark hotkey for highlights.")
    VALORANT = GameInfo("valorant", "Valorant", "VAL", {}, "auto", ("VALORANT-Win64-Shipping.exe",), "#ff4655",
                        "Riot has no local match API for Valorant - records the whole time the game is open. "
                        "Use the Bookmark hotkey for highlights.")
    DEADLOCK = GameInfo("deadlock", "Deadlock", "DL", {}, "auto", ("deadlock.exe",), "#8c7a5b",
                        "No live API (deadlock-api.com is post-match stats) - records the whole time the game is "
                        "open. Use the Bookmark hotkey for highlights.")
    MARVEL_RIVALS = GameInfo("marvel_rivals", "Marvel Rivals", "MR", {}, "auto", ("Marvel-Win64-Shipping.exe",),
                             "#e8b923", "No live API (marvelrivalsapi.com has match totals, no timestamps) - "
                             "records the whole time the game is open. Use the Bookmark hotkey for highlights.")
    REPO = GameInfo("repo", "R.E.P.O.", "REPO", {}, "auto", ("REPO.exe",), "#5fb3a1",
                    "No game API - records the whole time the game is open. Use the Bookmark hotkey for highlights.")

    PLANNED = tuple(GameInfo(gid, name, prefix, color=color) for gid, name, prefix, color in (
        ("dota2", "Dota 2", "Dota", "#b8342c"), ("overwatch2", "Overwatch 2", "OW2", "#f99e1a"),
        ("fortnite", "Fortnite", "FN", "#9d4dbb"), ("rocket_league", "Rocket League", "RL", "#1f6fe0"),
        ("minecraft", "Minecraft", "MC", "#62a83f"),
    ))
    SESSION_GAMES = (APEX, VALORANT, DEADLOCK, MARVEL_RIVALS, REPO)  # process-detected, no API

    _games: dict[str, GameInfo] = {g.id: g for g in (LEAGUE, TFT, CS2, *SESSION_GAMES, *PLANNED)}

    @classmethod
    def register(cls, info: GameInfo) -> None:
        cls._games[info.id] = info

    @classmethod
    def all(cls) -> list[GameInfo]:
        """Highlights first, then auto-record, then planned; alphabetical inside each group."""
        order = {"highlights": 0, "auto": 1, "planned": 2}
        return sorted(cls._games.values(), key=lambda g: (order[g.support], g.name.lower()))

    @classmethod
    def supported(cls) -> list[GameInfo]:
        return [g for g in cls.all() if g.supported]

    @classmethod
    def get(cls, game_id: str | None) -> GameInfo | None:
        """Known game, a readable placeholder for an unknown id, or None for no id (manual recording)."""
        if not game_id:
            return None
        return cls._games.get(game_id) or GameInfo(game_id, game_id.replace("_", " ").title(), game_id[:6])
