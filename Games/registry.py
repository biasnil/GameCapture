"""Supported games.

Three kinds of game:
  * built in with an API (League, CS2): highlights are detected automatically
  * built in, process-detected (Apex, Dota 2, ...): recorded while the game's .exe is running
  * added by you (Settings > Games > Add a game): any .exe, recorded while it is running

Adding a built-in game = registering a GameInfo here (plus a watcher if it has an API)."""
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
    default_on: bool = True     # auto-record before the user has touched its switch
    custom: bool = False        # added by the user (Settings > Games > Add a game)

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
    """The three recording modes of games with a match API (League of Legends / TFT)."""
    OPTIONS = (("session", "Session"), ("match", "Match"), ("highlights", "Highlights"))
    TIPS = {
        "session": "Record from when you open League until you close it - one video with every match",
        "match": "Record each match on its own (from game start to the end screen)",
        "highlights": "Record the match, then keep only a short video of your highlights and delete the rest",
    }


SESSION_HOW = "Records the whole time the game is open. Use the Bookmark hotkey for highlights."


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

    # Process-detected games that are off until you switch them on (Settings > Games).
    CATALOG = tuple(GameInfo(gid, name, prefix, {}, "auto", procs, color, SESSION_HOW, default_on=False)
                    for gid, name, prefix, procs, color in (
        ("dota2", "Dota 2", "Dota", ("dota2.exe",), "#b8342c"),
        ("overwatch2", "Overwatch 2", "OW2", ("Overwatch.exe",), "#f99e1a"),
        ("fortnite", "Fortnite", "FN", ("FortniteClient-Win64-Shipping.exe",), "#9d4dbb"),
        ("rocket_league", "Rocket League", "RL", ("RocketLeague.exe",), "#1f6fe0"),
        ("minecraft", "Minecraft", "MC", ("Minecraft.Windows.exe",), "#62a83f"),
        ("pubg", "PUBG: Battlegrounds", "PUBG", ("TslGame.exe",), "#f2a900"),
        ("rainbow_six", "Rainbow Six Siege", "R6", ("RainbowSix.exe", "RainbowSix_Vulkan.exe"), "#4a90d9"),
        ("gta5", "Grand Theft Auto V", "GTA5", ("GTA5.exe", "GTA5_Enhanced.exe"), "#56a845"),
        ("roblox", "Roblox", "RBX", ("RobloxPlayerBeta.exe",), "#e2231a"),
        ("genshin", "Genshin Impact", "GI", ("GenshinImpact.exe",), "#d4a85a"),
        ("destiny2", "Destiny 2", "D2", ("destiny2.exe",), "#c9cfd6"),
        ("helldivers2", "Helldivers 2", "HD2", ("helldivers2.exe",), "#ffe710"),
        ("elden_ring", "Elden Ring", "ER", ("eldenring.exe",), "#c7a76c"),
    ))
    SESSION_GAMES = (APEX, VALORANT, DEADLOCK, MARVEL_RIVALS, REPO)  # process-detected, no API, on by default
    PLANNED: tuple[GameInfo, ...] = ()

    _games: dict[str, GameInfo] = {g.id: g for g in (LEAGUE, TFT, CS2, *SESSION_GAMES, *CATALOG, *PLANNED)}
    _COLORS = ("#3dd6c6", "#4c9ffe", "#a371f7", "#ff8c42", "#f5c542", "#3ecf6b", "#ff7ab6", "#e5484d")

    @classmethod
    def register(cls, info: GameInfo) -> None:
        cls._games[info.id] = info

    @classmethod
    def known(cls, game_id: str) -> bool:
        return game_id in cls._games

    @classmethod
    def unregister(cls, game_id: str) -> None:
        info = cls._games.get(game_id)
        if info is not None and info.custom:  # built-in games can't be removed
            del cls._games[game_id]

    # ---------- games you add yourself ----------

    @staticmethod
    def custom_id(name: str) -> str:
        slug = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
        return "custom_" + ("_".join(filter(None, slug.split("_"))) or "game")

    @classmethod
    def custom_game(cls, game_id: str, name: str, processes) -> GameInfo:
        procs = tuple(p for p in processes if p)
        prefix = "".join(c for c in name.title() if c.isalnum())[:12] or "Game"
        color = cls._COLORS[sum(map(ord, game_id)) % len(cls._COLORS)]
        exe = ", ".join(procs) or "its .exe"
        return GameInfo(game_id, name or game_id, prefix, {}, "auto", procs, color,
                        f"Added by you. Records the whole time {exe} is running. "
                        "Use the Bookmark hotkey for highlights.", custom=True)

    @classmethod
    def load_custom(cls, games: dict) -> list[GameInfo]:
        """Register every game from config (`{id: GameSettings}` with custom=True); drop removed ones."""
        for gid in [g.id for g in cls._games.values() if g.custom and g.id not in games]:
            cls.unregister(gid)
        added = []
        for gid, gs in games.items():
            if getattr(gs, "custom", False):
                info = cls.custom_game(gid, gs.name, gs.processes)
                cls.register(info)
                added.append(info)
        return added

    @classmethod
    def custom(cls) -> list[GameInfo]:
        return [g for g in cls.all() if g.custom]

    @classmethod
    def session_games(cls) -> list[GameInfo]:
        """Every game recorded by watching for its process (everything without a match API)."""
        return [g for g in cls.all() if g.support == "auto" and g.processes and g.id != "tft"]

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
