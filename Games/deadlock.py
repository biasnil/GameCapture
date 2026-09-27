"""Deadlock: turn a recorded play session into one labelled video per match, with highlights.

There's no live game API, so the session is recorded while the game is open (ProcessSessionWatcher).
Afterwards we ask the community Deadlock API (api.deadlock-api.com - free, no key) which matches you
played: start time, length, hero, K/D/A, result. Each match is cut out of the session (stream copy,
no re-encode) and, if the match details list deaths with their in-game time, your kills and deaths
become timeline markers. The lookup queue and cutting live in Games/match_enricher.py."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from Games.match_enricher import EnrichJob, MatchEnricher
from Games.registry import GameRegistry
from Games.session_watcher import ProcessSessionWatcher

log = logging.getLogger("gamecapture.deadlock")

MULTIKILL = {2: "Double kill", 3: "Triple kill", 4: "Quadra kill", 5: "Penta kill"}
MATCH_MODES = {1: "Unranked", 2: "Private lobby", 3: "Co-op bots", 4: "Ranked", 6: "Tutorial", 7: "Hero Labs"}


# ======================================================================== account + API

class SteamAccount:
    @staticmethod
    def active_account_id() -> int | None:
        """The logged-in Steam account (SteamID3 number, what the Deadlock API calls account_id)."""
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Valve\\Steam\\ActiveProcess") as key:
                value = int(winreg.QueryValueEx(key, "ActiveUser")[0])
                return value or None
        except (ImportError, OSError, ValueError):
            return None


class DeadlockApi:
    BASE = "https://api.deadlock-api.com"

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self._heroes: dict[int, str] | None = None

    def _get(self, path: str):
        req = urllib.request.Request(self.BASE + path, headers={"User-Agent": "GameCapture (personal recorder)"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def match_history(self, account_id: int) -> list[dict]:
        data = self._get(f"/v1/players/{account_id}/match-history")
        return data if isinstance(data, list) else []

    def metadata(self, match_id: int) -> dict | None:
        try:
            return self._get(f"/v1/matches/{match_id}/metadata")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def hero_name(self, hero_id: int) -> str:
        if self._heroes is None:
            try:
                self._heroes = {int(h["id"]): h.get("name", "") for h in self._get("/v1/assets/heroes")
                                if isinstance(h, dict) and "id" in h}
            except Exception:
                self._heroes = {}
        return self._heroes.get(int(hero_id)) or f"Hero {hero_id}"


# ======================================================================== match data

@dataclass
class DeadlockMatch:
    match_id: int
    start_time: float        # unix seconds
    duration_s: float
    hero_id: int
    kills: int
    deaths: int
    assists: int
    won: bool | None
    mode: str = ""

    @property
    def end_time(self) -> float:
        return self.start_time + self.duration_s

    @classmethod
    def from_history(cls, row: dict) -> "DeadlockMatch | None":
        try:
            team, result = row.get("player_team"), row.get("match_result")
            won = (team == result) if team is not None and result is not None else None
            return cls(int(row["match_id"]), float(row["start_time"]), float(row.get("match_duration_s") or 0),
                       int(row.get("hero_id") or 0), int(row.get("player_kills") or 0),
                       int(row.get("player_deaths") or 0), int(row.get("player_assists") or 0), won,
                       MATCH_MODES.get(row.get("match_mode"), ""))
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def result(self) -> str:
        return {True: "Win", False: "Lose"}.get(self.won, "Unknown")


class DeadlockTimeline:
    """Your kills and deaths (in-game seconds) from match metadata. The schema isn't documented
    in detail, so this looks for it defensively: a players list with account_id + player_slot,
    each with death_details [{game_time_s, killer_player_slot}]. Anything missing -> no markers."""

    @classmethod
    def _players(cls, obj) -> list[dict]:
        if isinstance(obj, list):
            if obj and all(isinstance(p, dict) for p in obj) and any("account_id" in p for p in obj) \
                    and any("player_slot" in p for p in obj):
                return obj
            for item in obj:
                found = cls._players(item)
                if found:
                    return found
        elif isinstance(obj, dict):
            for value in obj.values():
                found = cls._players(value)
                if found:
                    return found
        return []

    @staticmethod
    def _time(d: dict) -> float | None:
        for key in ("game_time_s", "game_time", "time_s"):
            if isinstance(d.get(key), (int, float)):
                return float(d[key])
        return None

    @classmethod
    def parse(cls, meta: dict | None, account_id: int) -> tuple[list[float], list[float]]:
        """-> (kill times, death times) for you, in game seconds."""
        players = cls._players(meta or {})
        me = next((p for p in players if p.get("account_id") == account_id), None)
        if me is None:
            return [], []
        slot = me.get("player_slot")
        deaths = sorted(t for d in me.get("death_details") or [] if isinstance(d, dict)
                        and (t := cls._time(d)) is not None)
        kills = sorted(t for p in players if p is not me for d in p.get("death_details") or []
                       if isinstance(d, dict) and d.get("killer_player_slot") == slot
                       and (t := cls._time(d)) is not None)
        return kills, deaths

    @staticmethod
    def markers(kills: list[float], deaths: list[float], to_video, chain_s: float = 10.0) -> list[dict]:
        """Markers in video time. Kills within chain_s of each other form one multikill."""
        out = []

        def mk(kind, label, t, importance, event=None):
            out.append({"type": kind, "label": label, "involves_me": True, "importance": importance,
                        "event": event or {}, "video_time": round(to_video(t), 2), "game_time": round(t, 1)})
        streak: list[float] = []
        for t in kills + [float("inf")]:
            if streak and t - streak[-1] > chain_s:
                if len(streak) >= 2:
                    n = len(streak)
                    mk("multikill", MULTIKILL.get(n, f"{n} kills"), streak[0], 4 if n >= 4 else 3,
                       {"KillStreak": n})
                streak = []
            if t != float("inf"):
                mk("kill", "Kill", t, 2)
                streak.append(t)
        for t in deaths:
            mk("death", "Died", t, 1)
        return sorted(out, key=lambda m: m["video_time"])


# ======================================================================== enrichment

class DeadlockEnricher(MatchEnricher):
    GAME = GameRegistry.DEADLOCK
    QUEUE_FILE = ".deadlock_pending.json"

    def __init__(self, output_dir: Path, ffmpeg, settings, discard, api: DeadlockApi | None = None,
                 clock=time.time) -> None:
        super().__init__(output_dir, ffmpeg, settings, discard, api or DeadlockApi(), clock)

    def find_matches(self, job: EnrichJob) -> list[DeadlockMatch]:
        return [m for m in (DeadlockMatch.from_history(r) for r in self.api.match_history(job.account_id))
                if m is not None]

    def plan(self, job: EnrichJob, m: DeadlockMatch) -> tuple[float, list[dict]]:
        offset = float(self.settings().options.get("offset_s", 0))
        zero = m.start_time - job.rec_start + offset
        meta = None
        try:
            meta = self.api.metadata(m.match_id)
        except Exception as exc:
            log.info("Deadlock: no details for match %d (%s)", m.match_id, exc)
        kills, deaths = DeadlockTimeline.parse(meta, job.account_id)
        markers = DeadlockTimeline.markers(kills, deaths, lambda t: zero + t)
        hero = self.api.hero_name(m.hero_id)
        markers.insert(0, self.marker("game_start", f"{hero} - match start", max(0.0, zero)))
        markers.append(self.marker("game_end", f"Match end - {m.result}", zero + m.duration_s))
        return zero, markers

    def match_file(self, m: DeadlockMatch) -> str:
        safe = "".join(c for c in self.api.hero_name(m.hero_id) if c.isalnum()) or "Deadlock"
        return f"{safe}_{m.result}_{m.kills}-{m.deaths}-{m.assists}"

    def match_game(self, m: DeadlockMatch) -> dict:
        hero = self.api.hero_name(m.hero_id)
        return {"title": hero, "champion": hero, "mode": m.mode, "result": m.result, "kills": m.kills,
                "deaths": m.deaths, "assists": m.assists, "match_id": m.match_id,
                "game_length_s": round(m.duration_s, 1)}

    def summary(self, old_game: dict, matches: list[DeadlockMatch]) -> dict:
        wins = sum(m.won is True for m in matches)
        return dict(old_game, title="Deadlock session", matches=len(matches),
                    result=f"{wins}W {sum(m.won is False for m in matches)}L",
                    kills=sum(m.kills for m in matches), deaths=sum(m.deaths for m in matches),
                    assists=sum(m.assists for m in matches))


# ======================================================================== watcher

class DeadlockWatcher(ProcessSessionWatcher):
    """Records while Deadlock is open, then queues the match lookup."""

    def __init__(self, settings, recorder, enabled=lambda gid: True, monitor=None, enricher=None,
                 account=lambda: None) -> None:
        super().__init__(GameRegistry.DEADLOCK, settings, recorder, enabled, monitor)
        self.enricher = enricher
        self.account = account          # -> SteamID3 account id (setting, else the logged-in Steam user)

    def after_save(self, path: Path, started_wall: float, ended_wall: float) -> None:
        if self.enricher is None:
            return
        account = self.account()
        if not account:
            log.warning("Deadlock: couldn't tell which Steam account you use - set it in Settings > Deadlock "
                        "to get per-match videos")
            return
        self.enricher.enqueue(path, account, started_wall, ended_wall)
