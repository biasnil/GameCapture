"""Teamfight Tactics: the TFT client (TFTClient-Win64-Shipping.exe) has no live match API, so the session is
recorded while it is open (ProcessSessionWatcher). Afterwards Riot's official TFT match history
(tft-match-v1) says which matches you played: when, your placement, whether it was a win (top 4) or a loss,
and the round you went out in. Each match is cut out of the session (stream copy, no re-encode).

Riot's API needs your own key (developer.riotgames.com). Riot's policy doesn't allow shipping a key
inside the app, so you paste yours in Settings > Teamfight Tactics. Match history has no round-by-round
results, so single rounds aren't marked - use the Bookmark hotkey for those.

Nothing here runs during a match: the lookup happens after the session (Riot's TFT policy forbids
real-time in-game information)."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from Games.match_enricher import EnrichJob, MatchEnricher
from Games.registry import GameRegistry
from Games.session_watcher import ProcessSessionWatcher

log = logging.getLogger("gamecapture.tft")

REGIONS = ("americas", "europe", "asia", "sea")      # tft-match-v1 routing values
QUEUES = {1090: "Normal", 1100: "Ranked", 1130: "Hyper Roll", 1160: "Double Up", 1110: "Tutorial"}


class TftApiError(Exception):
    """The key is missing, expired or wrong, or the Riot ID doesn't exist - retrying won't help until
    the settings change (the lookup is still retried, in case you fix them)."""


# ======================================================================== API

class TftApi:
    """Riot's official API. `options` -> the TFT settings dict (api_key, region), read on every call so a
    new key takes effect straight away."""

    def __init__(self, options=lambda: {}, timeout: float = 20.0) -> None:
        self.options = options
        self.timeout = timeout
        self._puuids: dict[str, str] = {}

    @property
    def region(self) -> str:
        region = str(self.options().get("region") or "americas").lower()
        return region if region in REGIONS else "americas"

    def _get(self, region: str, path: str, query: dict | None = None):
        key = str(self.options().get("api_key") or "").strip()
        if not key:
            raise TftApiError("no Riot API key - add yours in Settings > Teamfight Tactics")
        url = f"https://{region}.api.riotgames.com{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, headers={"X-Riot-Token": key, "User-Agent": "GameCapture"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code in (401, 403):
                raise TftApiError("Riot API key rejected (developer keys expire after 24 h)") from None
            raise

    def puuid(self, riot_id: str) -> str:
        """'Name#TAG' -> PUUID (account-v1; SEA accounts are served from ASIA)."""
        if riot_id not in self._puuids:
            name, _, tag = riot_id.partition("#")
            if not name or not tag:
                raise TftApiError(f"'{riot_id}' isn't a Riot ID - it looks like Name#TAG")
            region = "asia" if self.region == "sea" else self.region
            try:
                data = self._get(region, "/riot/account/v1/accounts/by-riot-id/"
                                 f"{urllib.parse.quote(name)}/{urllib.parse.quote(tag)}")
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise TftApiError(f"Riot ID {riot_id} not found") from None
                raise
            self._puuids[riot_id] = data["puuid"]
        return self._puuids[riot_id]

    def match_ids(self, puuid: str, start: float, end: float) -> list[str]:
        ids = self._get(self.region, f"/tft/match/v1/matches/by-puuid/{puuid}/ids",
                        {"startTime": int(start), "endTime": int(end), "count": 20})
        return ids if isinstance(ids, list) else []

    def match(self, match_id: str) -> dict | None:
        try:
            return self._get(self.region, f"/tft/match/v1/matches/{match_id}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise


# ======================================================================== match data

class TftRounds:
    @staticmethod
    def stage(last_round: int) -> str:
        """Riot's round counter -> the stage shown in game. Stage 1 has 4 rounds, later stages 7
        (Riot: going out in stage 2-1 is last_round 5)."""
        if last_round <= 0:
            return "?"
        if last_round <= 4:
            return f"1-{last_round}"
        return f"{2 + (last_round - 5) // 7}-{(last_round - 5) % 7 + 1}"

    @staticmethod
    def ordinal(n: int) -> str:
        return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


@dataclass
class TftMatch:
    match_id: str
    start_time: float        # unix seconds, the loading screen
    duration_s: float        # how long you were in it (to your last round)
    placement: int
    won: bool | None         # Riot's `win`: a top-4 finish
    last_round: int
    level: int = 0
    players_eliminated: int = 0
    damage: int = 0
    mode: str = ""

    @property
    def end_time(self) -> float:
        return self.start_time + self.duration_s

    @property
    def result(self) -> str:
        return {True: "Win", False: "Loss"}.get(self.won, "Unknown")

    @property
    def place(self) -> str:
        return TftRounds.ordinal(self.placement) if self.placement else "?"

    @property
    def stage(self) -> str:
        return TftRounds.stage(self.last_round)

    @classmethod
    def from_api(cls, data: dict | None, puuid: str) -> "TftMatch | None":
        try:
            info = data["info"]
            me = next(p for p in info["participants"] if p.get("puuid") == puuid)
            length = float(me.get("time_eliminated") or info.get("game_length") or 0)
            if info.get("gameCreation"):
                start = float(info["gameCreation"]) / 1000
            else:   # older matches: game_datetime is when the game ended
                start = float(info["game_datetime"]) / 1000 - float(info.get("game_length") or length)
            placement = int(me.get("placement") or 0)
            won = me.get("win")
            if won is None and placement:
                won = placement <= 4
            return cls(str(data["metadata"]["match_id"]), start, length, placement, won,
                       int(me.get("last_round") or 0), int(me.get("level") or 0),
                       int(me.get("players_eliminated") or 0), int(me.get("total_damage_to_players") or 0),
                       QUEUES.get(info.get("queue_id") or info.get("queueId"), ""))
        except (KeyError, TypeError, ValueError, StopIteration):
            return None


# ======================================================================== enrichment

class TftEnricher(MatchEnricher):
    GAME = GameRegistry.TFT
    QUEUE_FILE = ".tft_pending.json"
    POST_PAD = 45.0   # time_eliminated doesn't count the loading screen - keep a little extra at the end
    SEARCH_PAD = 3600.0

    def __init__(self, output_dir: Path, ffmpeg, settings, discard, api: TftApi | None = None,
                 clock=time.time) -> None:
        super().__init__(output_dir, ffmpeg, settings, discard, api or TftApi(lambda: settings().options), clock)

    def find_matches(self, job: EnrichJob) -> list[TftMatch]:
        puuid = self.api.puuid(str(job.account_id))
        ids = self.api.match_ids(puuid, job.rec_start - self.SEARCH_PAD, job.rec_end + self.SEARCH_PAD)
        return [m for m in (TftMatch.from_api(self.api.match(i), puuid) for i in ids) if m is not None]

    def plan(self, job: EnrichJob, m: TftMatch) -> tuple[float, list[dict]]:
        zero = m.start_time - job.rec_start + float(self.settings().options.get("offset_s", 0))
        top = m.placement == 1
        finish = (f"{m.place} place - you won the lobby" if top
                  else f"{m.place} place ({m.result}) - out in round {m.stage}")
        return zero, [
            self.marker("game_start", f"Match start{' - ' + m.mode if m.mode else ''}", max(0.0, zero)),
            self.marker("game_end", finish, zero + m.duration_s,
                        event={"placement": m.placement, "win": m.won, "last_round": m.last_round}),
        ]

    def match_file(self, m: TftMatch) -> str:
        return f"{m.place}_{m.result}"

    def match_game(self, m: TftMatch) -> dict:
        return {"title": "Teamfight Tactics", "mode": m.mode, "result": f"{m.result} - {m.place}",
                "placement": m.placement, "win": m.won, "last_round": m.stage, "level": m.level,
                "players_eliminated": m.players_eliminated, "damage_to_players": m.damage,
                "match_id": m.match_id, "game_length_s": round(m.duration_s, 1)}

    def summary(self, old_game: dict, matches: list[TftMatch]) -> dict:
        wins = sum(m.won is True for m in matches)
        places = ", ".join(m.place for m in matches)
        return dict(old_game, title="TFT session", matches=len(matches),
                    result=f"{wins}W {sum(m.won is False for m in matches)}L ({places})")


# ======================================================================== watcher

class TftWatcher(ProcessSessionWatcher):
    """Records while the TFT client is open, then queues the match lookup."""

    def __init__(self, settings, recorder, enabled=lambda gid: True, monitor=None, enricher=None,
                 riot_id=lambda: None) -> None:
        super().__init__(GameRegistry.TFT, settings, recorder, enabled, monitor)
        self.enricher = enricher
        self.riot_id = riot_id          # -> 'Name#TAG' from Settings > Teamfight Tactics

    def after_save(self, path: Path, started_wall: float, ended_wall: float) -> None:
        if self.enricher is None:
            return
        riot_id = (self.riot_id() or "").strip()
        if not riot_id:
            log.info("TFT: add your Riot ID and API key in Settings > Teamfight Tactics to get one video "
                     "per match with your placement")
            return
        self.enricher.enqueue(path, riot_id, started_wall, ended_wall)
