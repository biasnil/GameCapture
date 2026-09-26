"""A scripted League match: produces exactly what the Live Client Data API would return at any game time."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Scenario:
    events: list[tuple[float, str, dict]]      # (game_time, EventName, extra fields)
    champion: str = "Ahri"
    mode: str = "CLASSIC"
    riot_id: str = "Tester#DEV"
    others: list[str] = field(default_factory=lambda: ["Enemy1", "Enemy2", "Enemy3", "Ally1"])

    @property
    def game_name(self) -> str:
        return self.riot_id.split("#")[0]

    @property
    def end_time(self) -> float:
        ends = [t for t, name, _ in self.events if name == "GameEnd"]
        return ends[0] if ends else max(t for t, _, _ in self.events)

    # ---- the four endpoints GameCapture uses ----

    def game_stats(self, t: float) -> dict:
        return {"gameMode": self.mode, "gameTime": t, "mapName": "Map11", "mapNumber": 11, "mapTerrain": "Default"}

    def active_player_name(self) -> str:
        return self.riot_id

    def events_until(self, t: float) -> list[dict]:
        return [{"EventID": i, "EventName": name, "EventTime": et, **extra}
                for i, (et, name, extra) in enumerate(self.events) if et <= t]

    def player_list(self, t: float) -> list[dict]:
        k = d = a = 0
        for ev in self.events_until(t):
            if ev["EventName"] != "ChampionKill":
                continue
            if ev.get("KillerName") == self.game_name:
                k += 1
            elif ev.get("VictimName") == self.game_name:
                d += 1
            elif self.game_name in ev.get("Assisters", []):
                a += 1
        me = {"riotId": self.riot_id, "riotIdGameName": self.game_name, "championName": self.champion,
              "scores": {"kills": k, "deaths": d, "assists": a, "creepScore": 0, "wardScore": 0}}
        others = [{"riotId": f"{n}#X", "riotIdGameName": n, "championName": "Garen",
                   "scores": {"kills": 0, "deaths": 0, "assists": 0}} for n in self.others]
        return [me, *others]

    # ---- ready-made scenarios ----

    @staticmethod
    def _kill(killer: str, victim: str, assisters: list[str] | None = None) -> dict:
        return {"KillerName": killer, "VictimName": victim, "Assisters": assisters or []}

    @classmethod
    def quick(cls) -> "Scenario":
        """~2.5 minutes of game time, something every 10-20 s. Good for real-time tests."""
        me, k = "Tester", cls._kill
        return cls(events=[
            (0.03, "GameStart", {}),
            (15.0, "FirstBlood", {"Recipient": me}),
            (15.0, "ChampionKill", k(me, "Enemy1")),
            (30.0, "ChampionKill", k("Ally1", "Enemy2", [me])),
            (45.0, "ChampionKill", k(me, "Enemy2")),
            (47.0, "ChampionKill", k(me, "Enemy3")),
            (47.3, "Multikill", {"KillerName": me, "KillStreak": 2}),
            (60.0, "ChampionKill", k("Enemy1", "Ally1")),
            (75.0, "ChampionKill", k("Enemy3", me)),
            (90.0, "DragonKill", {"KillerName": "Ally1", "Assisters": [me], "DragonType": "Fire", "Stolen": "False"}),
            (105.0, "TurretKilled", {"KillerName": me, "TurretKilled": "Turret_T2_L_03_A", "Assisters": []}),
            (120.0, "BaronKill", {"KillerName": me, "Assisters": [], "Stolen": "True"}),
            (135.0, "Ace", {"Acer": me, "AcingTeam": "ORDER"}),
            (150.0, "GameEnd", {"Result": "Win"}),
        ])
