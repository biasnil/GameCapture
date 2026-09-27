"""Watches League's Live Client Data API: auto-records each League or TFT match and marks highlights.

Match start  = the API answers with a local player -> start recording
Match events = new entries in /eventdata, converted from game time to video time
Match end    = GameEnd event (+ post-roll), or the API going silent for `end_grace_seconds`
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from Core.config import AutoSettings
from Core.formatting import Format
from Core.sidecar import Sidecar
from Games.base import GameWatcher
from Games.league_client import LeagueLiveClient
from Games.registry import GameRegistry

log = logging.getLogger("gamecapture.league")


@dataclass
class MatchSession:
    riot_id: str
    mode: str
    recording_id: str | None            # which recording the markers belong to
    game_id: str = "league"             # "league" or "tft" (same client, same API)
    champion: str = "Unknown"
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    result: str | None = None
    markers: list[dict] = field(default_factory=list)
    seen_events: set[int] = field(default_factory=set)
    last_seen: float = field(default_factory=time.monotonic)
    last_scores: float = 0.0
    ended_at: float | None = None       # when GameEnd arrived
    game_time: float = 0.0

    @property
    def game_name(self) -> str:
        return self.riot_id.split("#", 1)[0]

    def is_me(self, name: str | None) -> bool:
        return bool(name) and name in (self.riot_id, self.game_name)


class LeagueEventClassifier:
    """Turns a raw Live Client event into a highlight marker (or None to ignore it)."""
    MULTIKILL = {2: "Double kill", 3: "Triple kill", 4: "Quadra kill", 5: "PENTAKILL"}
    MONSTERS = {"DragonKill": "Dragon", "BaronKill": "Baron", "HeraldKill": "Rift Herald",
                "HordeKill": "Voidgrub", "AtakhanKill": "Atakhan"}

    @staticmethod
    def _marker(ev: dict, kind: str, label: str, mine: bool, importance: int) -> dict:
        return {"type": kind, "label": label, "involves_me": mine, "importance": importance, "event": ev}

    @classmethod
    def classify(cls, ev: dict, s: MatchSession) -> dict | None:
        name = ev.get("EventName", "")
        killer = ev.get("KillerName")
        in_it = s.is_me(killer) or any(s.is_me(a) for a in ev.get("Assisters") or [])
        m = lambda kind, label, mine, imp: cls._marker(ev, kind, label, mine, imp)  # noqa: E731

        if name == "ChampionKill":
            victim = ev.get("VictimName")
            if s.is_me(killer):
                return m("kill", f"Killed {victim}", True, 2)
            if s.is_me(victim):
                return m("death", f"Killed by {killer}", True, 1)
            if in_it:
                return m("assist", f"Assist on {victim}", True, 1)
            return m("other_kill", f"{killer} killed {victim}", False, 0)
        if name == "Multikill":
            streak = int(ev.get("KillStreak", 2))
            return m("multikill", cls.MULTIKILL.get(streak, f"{streak}x kill"), s.is_me(killer), 3 if streak < 4 else 4)
        if name in cls.MONSTERS:
            monster = cls.MONSTERS[name]
            if name == "DragonKill" and ev.get("DragonType"):
                monster = f"{ev['DragonType']} Dragon"
            stolen = str(ev.get("Stolen", "")).lower() == "true"
            label = f"{monster} {'STOLEN' if stolen else 'taken'} by {killer}"
            return m("objective", label, in_it, 3 if stolen and in_it else 2 if in_it else 1)
        if name in ("TurretKilled", "InhibKilled"):
            return m("structure", f"{'Turret' if name == 'TurretKilled' else 'Inhibitor'} destroyed", in_it, 1)
        if name == "FirstBlood":
            mine = s.is_me(ev.get("Recipient"))
            return m("first_blood", "First blood" + (" (you)" if mine else ""), mine, 2 if mine else 1)
        if name == "Ace":
            mine = s.is_me(ev.get("Acer"))
            return m("ace", "Ace" + (" (you)" if mine else ""), mine, 3 if mine else 1)
        if name == "GameStart":
            return m("game_start", "Game start", False, 0)
        if name == "GameEnd":
            return m("game_end", f"Game end - {ev.get('Result', '?')}", False, 0)
        return None


@dataclass
class SessionRun:
    """Session mode: one recording from League opening to League closing, spanning many matches."""
    recording_id: str | None
    matches: list[dict] = field(default_factory=list)
    markers: list[dict] = field(default_factory=list)

    def add_match(self, s: MatchSession, result: str) -> None:
        n = len(self.matches) + 1
        for m in s.markers:  # tell the matches apart on the timeline
            if m["type"] == "game_start":
                m["label"] = f"Match {n} start - {s.champion}"
            elif m["type"] == "game_end":
                m["label"] = f"Match {n} end - {result}"
            m["match"] = n
        self.markers.extend(s.markers)
        starts = [m["video_time"] for m in s.markers]
        self.matches.append({"champion": s.champion, "mode": s.mode, "result": result, "kills": s.kills,
                             "deaths": s.deaths, "assists": s.assists,
                             "start_video_time": min(starts) if starts else None})


class LeagueMatchWatcher(GameWatcher):
    """Recording modes (League / TFT, from Settings or the header switch):
      match       one video per match (default)
      session     one video from League opening until it closes, with every match's highlights
      highlights  record the match, keep only a short video of your highlights, delete the rest"""
    GAME = GameRegistry.LEAGUE
    SESSION_GRACE = 10.0  # League gone this long = session over (covers client restarts)

    def __init__(self, settings: AutoSettings, recorder, enabled=lambda game_id: True, mode=lambda: "match",
                 processes=None, condenser=None) -> None:
        super().__init__(settings, recorder, enabled)
        self.mode = mode              # read when a match or session starts
        self.processes = processes    # LeagueProcesses (session mode); None = only in-game detection
        self.condenser = condenser    # HighlightCondenser (highlights mode)
        self.client = LeagueLiveClient()
        self.session: MatchSession | None = None
        self.session_run: SessionRun | None = None
        self._skip_noted = False
        self._league_seen = 0.0
        self._suppress_until_closed = False
        # After a match ends, League keeps the API up on the end-of-game screen. Don't start a
        # new recording until it has gone quiet at least once (i.e. the game really closed).
        self._wait_for_silence = False

    @staticmethod
    def game_for_mode(mode: str) -> str:
        return "tft" if mode.upper().startswith("TFT") else "league"

    def finish(self, reason: str) -> None:
        if self.session is not None:
            self._end("app closed mid-match")
        if self.session_run is not None:
            self._finish_session(reason)

    def live_status(self) -> dict | None:
        s = self.session
        if s is not None:
            title = s.champion if s.game_id == "league" else "Teamfight Tactics"
            kda = f"{s.kills}/{s.deaths}/{s.assists}" if s.game_id == "league" else ""
            return {"title": title, "kda": kda,
                    "highlights": sum(1 for m in list(s.markers) if m["involves_me"])}
        if self.session_run is not None:
            return {"title": "League session", "kda": "", "highlights": 0}
        return None

    # ---------- state machine ----------

    def tick(self) -> None:
        stats = self.client.game_stats()
        now = time.monotonic()
        self._tick_session(now, in_game=stats is not None)

        if self.session is None:
            if stats is None:
                self._skip_noted = False
                self._wait_for_silence = False
                return
            if not self._wait_for_silence and not self._suppress_until_closed:
                self._maybe_begin(stats)
            return

        s = self.session
        if stats is None:
            if s.ended_at is not None or now - s.last_seen > self.settings.end_grace_seconds:
                self._end("match over" if s.ended_at else "game closed without GameEnd")
            return
        s.last_seen = now
        s.game_time = float(stats.get("gameTime", 0.0))
        self._poll_events(s.game_time)
        if now - s.last_scores >= 5:
            self._refresh_scores()
        if s.ended_at is not None and now - s.ended_at >= self.settings.post_roll_seconds:
            self._end("match over")

    def _tick_session(self, now: float, in_game: bool) -> None:
        """Session mode: start when League opens, stop when it has been closed for a while."""
        running = in_game or (self.processes is not None and self.processes.running())
        if running:
            self._league_seen = now
        elif now - self._league_seen > self.SESSION_GRACE:
            self._suppress_until_closed = False

        run = self.session_run
        if run is not None:
            if self.recorder.current_id != run.recording_id:
                log.info("Session recording stopped manually - it won't restart until League is closed")
                self.session_run = None
                self._suppress_until_closed = True
            elif not running and self.session is None and now - self._league_seen > self.SESSION_GRACE:
                self._finish_session("League closed")
            return

        if (running and self.mode() == "session" and self.enabled("league") and not self._suppress_until_closed
                and self.session is None):
            if not self.recorder.is_recording:
                self.start_recording(GameRegistry.LEAGUE.prefix)
            if self.recorder.is_recording:
                self.session_run = SessionRun(self.recorder.current_id)
                log.info("League session started - recording until League closes")

    def _maybe_begin(self, stats: dict) -> None:
        me = self.client.active_player_name()
        if not me:
            return  # loading screen, spectator or replay
        mode = str(stats.get("gameMode", ""))
        game_id = self.game_for_mode(mode)
        if self.session_run is None and not self.enabled(game_id):
            return  # this game (League or TFT) is switched off
        if any(mode.upper().startswith(m.upper()) for m in self.settings.skip_modes):
            if not self._skip_noted:
                log.info("Match detected in %s - skipped (skip modes)", mode)
                self._skip_noted = True
            return
        if game_id == "tft" and self.recorder.is_recording and self.session_run is None:
            return  # the TFT client's own watcher is recording this session
        log.info("Match detected: %s as %s", mode, me)
        if not self.recorder.is_recording:
            self.start_recording(GameRegistry.get(game_id).prefix)
        elif self.session_run is None:
            log.info("Already recording - using the current recording for this match")
        self.session = MatchSession(riot_id=me, mode=mode, recording_id=self.recorder.current_id, game_id=game_id)
        self._refresh_scores()

    def _end(self, reason: str) -> None:
        s, self.session = self.session, None
        self._wait_for_silence = True
        result = s.result or ("Partial" if reason == "app closed mid-match" else "Unfinished")
        log.info("Match ended (%s): %s %s %d/%d/%d", reason, s.champion, s.result or "",
                 s.kills, s.deaths, s.assists)
        run = self.session_run
        if run is not None and s.recording_id == run.recording_id:
            run.add_match(s, result)  # session mode: keep recording, collect the match
            log.info("Session: %d match(es) so far", len(run.matches))
            return
        if not self.recorder.is_recording or self.recorder.current_id != s.recording_id:
            log.info("Recording was stopped manually during the match - nothing to finalize")
            return

        highlights = self.mode() == "highlights" and self.condenser is not None
        if s.game_id == "tft":
            tag = f"TFT_{result}"
            game = {"title": "Teamfight Tactics", "mode": s.mode, "result": result,
                    "game_length_s": round(s.game_time, 1)}
        else:
            tag = f"{s.champion}_{result}_{s.kills}-{s.deaths}-{s.assists}"
            game = {"riot_id": s.riot_id, "champion": s.champion, "mode": s.mode, "result": result,
                    "kills": s.kills, "deaths": s.deaths, "assists": s.assists,
                    "game_length_s": round(s.game_time, 1)}
        markers = s.markers + self.bookmark_markers()
        chapters = self.chapters(markers) if self.settings.chapters else None
        path = self.stop_recording(tag, chapters, hold=highlights)
        if path is None:
            log.error("No video to attach %d highlights to", len(markers))
            return
        meta = self.write_sidecar(path, s.game_id, game, markers)
        if highlights:
            self._keep_highlights_only(path, meta)

    def _finish_session(self, reason: str) -> None:
        run, self.session_run = self.session_run, None
        if not self.recorder.is_recording or self.recorder.current_id != run.recording_id:
            return
        results = [m["result"] for m in run.matches]
        wins, losses = results.count("Win"), results.count("Lose")
        n = len(run.matches)
        log.info("League session over (%s): %d match(es)", reason, n)
        tag = f"Session_{n}game{'s' if n != 1 else ''}"
        markers = run.markers + self.bookmark_markers()
        chapters = self.chapters(markers) if self.settings.chapters else None
        path = self.stop_recording(tag, chapters)
        if path is None:
            return
        game = {"title": "League session", "session": True, "mode": "",
                "result": f"{wins}W {losses}L" if n else "No matches",
                "kills": sum(m["kills"] for m in run.matches), "deaths": sum(m["deaths"] for m in run.matches),
                "assists": sum(m["assists"] for m in run.matches), "matches": run.matches}
        self.write_sidecar(path, "league", game, markers)

    def _keep_highlights_only(self, path: Path, meta: dict | None) -> None:
        """Highlights mode: replace the full recording with a short highlights video."""
        try:
            short = self.condenser.condense(path, meta or {})
        except Exception:
            log.exception("Making the highlights video failed - keeping the full recording")
            self.recorder.release(path)
            return
        if short is None:
            log.info("No highlights this match - nothing kept (Highlights mode)")
        self.recorder.discard(Sidecar.path_for(path))
        self.recorder.discard(path)
        self.recorder.release(path)

    # ---------- events ----------

    def _poll_events(self, game_now: float) -> None:
        s = self.session
        if self.recorder.current_id != s.recording_id:
            return  # user stopped/restarted manually; markers would point at the wrong file
        video_now = self.recorder.elapsed()
        for ev in self.client.events():
            eid = ev.get("EventID")
            if eid is None or eid in s.seen_events:
                continue
            s.seen_events.add(eid)
            if ev.get("EventName") == "GameEnd":
                s.result = ev.get("Result")
                s.ended_at = time.monotonic()
                self._refresh_scores()
            marker = LeagueEventClassifier.classify(ev, s)
            if marker is None:
                continue
            game_t = float(ev.get("EventTime", 0.0))
            video_t = video_now - (game_now - game_t)
            if video_t < 0:
                continue  # happened before this recording started
            marker.update(video_time=round(video_t, 2), game_time=round(game_t, 2))
            s.markers.append(marker)
            if marker["involves_me"] or marker["importance"] >= 2:
                log.info("[MARK] %s  (video %s)", marker["label"], Format.duration(video_t))

    def _refresh_scores(self) -> None:
        s = self.session
        s.last_scores = time.monotonic()
        for p in self.client.player_list() or []:
            if s.is_me(p.get("riotId")) or s.is_me(p.get("riotIdGameName")) or s.is_me(p.get("summonerName")):
                s.champion = p.get("championName") or s.champion
                sc = p.get("scores") or {}
                s.kills, s.deaths, s.assists = sc.get("kills", 0), sc.get("deaths", 0), sc.get("assists", 0)
                return
