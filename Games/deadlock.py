"""Deadlock: turn a recorded play session into one labelled video per match, with highlights.

There's no live game API, so the session is recorded while the game is open (ProcessSessionWatcher).
Afterwards we ask the community Deadlock API (api.deadlock-api.com - free, no key) which matches you
played: start time, length, hero, K/D/A, result. Each match is cut out of the session (stream copy,
no re-encode) and, if the match details list deaths with their in-game time, your kills and deaths
become timeline markers.

New matches can take a while to show up in the API, so lookups are queued on disk and retried
(2 min, 10 min, 30 min, 2 h, 6 h) - they survive restarting the app."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from Core.sidecar import Sidecar
from Games.base import GameWatcher
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


# ======================================================================== enrichment queue

@dataclass
class EnrichJob:
    video: str
    account_id: int
    rec_start: float          # unix time the recording started
    rec_end: float
    attempt: int = 0
    next_try: float = 0.0


class DeadlockEnricher:
    RETRY_AFTER = (120, 600, 1800, 7200, 21600)
    PRE_PAD, POST_PAD = 10.0, 15.0
    STALE_AFTER = 600.0   # last match must end within this of the session end, else wait for newer data

    def __init__(self, output_dir: Path, ffmpeg, settings, discard, api: DeadlockApi | None = None,
                 clock=time.time) -> None:
        self.dir = Path(output_dir)
        self.ffmpeg = ffmpeg
        self.settings = settings        # callable -> GameSettings of deadlock (live)
        self.discard = discard          # recorder.discard: deletes now or when Windows lets go
        self.api = api or DeadlockApi()
        self.clock = clock
        self.file = self.dir / ".deadlock_pending.json"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.jobs: list[EnrichJob] = self._load()

    # ---------- queue ----------

    def _load(self) -> list[EnrichJob]:
        try:
            return [EnrichJob(**j) for j in json.loads(self.file.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError):
            return []

    def _save(self) -> None:
        try:
            if self.jobs:
                self.file.write_text(json.dumps([asdict(j) for j in self.jobs], indent=1), encoding="utf-8")
            else:
                self.file.unlink(missing_ok=True)
        except OSError:
            log.exception("Couldn't save the Deadlock lookup queue")

    def enqueue(self, video: Path, account_id: int, rec_start: float, rec_end: float) -> None:
        with self._lock:
            self.jobs.append(EnrichJob(str(video), account_id, rec_start, rec_end, 0,
                                       self.clock() + self.RETRY_AFTER[0]))
            self._save()
        log.info("Deadlock: will look up the matches in %s in %d min", Path(video).name, self.RETRY_AFTER[0] // 60)

    def start(self) -> None:
        threading.Thread(target=self._loop, name="deadlock-enricher", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(30):
            try:
                self.tick()
            except Exception:
                log.exception("Deadlock lookup failed")

    def tick(self) -> None:
        now = self.clock()
        with self._lock:
            due = [j for j in self.jobs if j.next_try <= now]
        for job in due:
            outcome = self.process(job)
            with self._lock:
                if outcome == "retry" and job.attempt + 1 < len(self.RETRY_AFTER):
                    job.attempt += 1
                    job.next_try = now + self.RETRY_AFTER[job.attempt]
                elif job in self.jobs:
                    self.jobs.remove(job)
                self._save()

    # ---------- one session ----------

    def process(self, job: EnrichJob) -> str:
        """-> "done" (finished or nothing to do) or "retry" (data not there yet)."""
        video = Path(job.video)
        if not video.exists():
            return "done"
        final = job.attempt + 1 >= len(self.RETRY_AFTER)
        try:
            history = self.api.match_history(job.account_id)
        except Exception as exc:
            log.info("Deadlock API unavailable (%s) - trying again later", exc)
            return "retry"
        matches = [m for m in (DeadlockMatch.from_history(r) for r in history) if m is not None
                   and m.end_time > job.rec_start and m.start_time < job.rec_end]
        matches.sort(key=lambda m: m.start_time)
        fresh = bool(matches) and matches[-1].end_time >= job.rec_end - self.STALE_AFTER
        if not fresh and not final:
            log.info("Deadlock: matches for %s not in the API yet - trying again later", video.name)
            return "retry"
        if not matches:
            log.info("Deadlock: no matches found for %s - keeping it as one session", video.name)
            return "done"
        self.apply(video, job, matches)
        return "done"

    def _plan(self, job: EnrichJob, m: DeadlockMatch) -> tuple[float, list[dict]]:
        """Session-video time of this match's game clock 0, and its markers in session time."""
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
        markers.insert(0, {"type": "game_start", "label": f"{hero} - match start", "involves_me": False,
                           "importance": 0, "event": {}, "video_time": round(max(0.0, zero), 2)})
        markers.append({"type": "game_end", "label": f"Match end - {m.result}", "involves_me": False,
                        "importance": 0, "event": {}, "video_time": round(zero + m.duration_s, 2)})
        return zero, markers

    def apply(self, video: Path, job: EnrichJob, matches: list[DeadlockMatch]) -> None:
        old = Sidecar.read(video) or {}
        bookmarks = [mk for mk in old.get("markers", []) if mk.get("type") == "bookmark"]
        length = self.ffmpeg.duration(video) or (job.rec_end - job.rec_start)
        split = self.settings().options.get("split", True)
        plans = [(m, *self._plan(job, m)) for m in matches]
        if not split:
            self._annotate(video, old, plans, bookmarks)
            return
        made = []
        for m, zero, markers in plans:
            start = max(0.0, zero - self.PRE_PAD)
            end = min(length, zero + m.duration_s + self.POST_PAD)
            if end - start < 30:
                continue  # the match isn't really in this recording
            inside = [dict(b) for b in bookmarks if start <= b["video_time"] <= end]
            shifted = [dict(mk, video_time=round(mk["video_time"] - start, 2)) for mk in markers + inside]
            out = self._cut(video, m, start, end, shifted)
            if out is None:
                log.error("Deadlock: cutting match %d failed - keeping the full session", m.match_id)
                for p in made:
                    self.discard(p)
                    self.discard(Sidecar.path_for(p))
                return
            made.append(out)
        if not made:
            self._annotate(video, old, plans, bookmarks)
            return
        log.info("Deadlock: split %s into %d match video(s)", video.name, len(made))
        self.discard(Sidecar.path_for(video))
        self.discard(video)

    def _cut(self, video: Path, m: DeadlockMatch, start: float, end: float, markers: list[dict]) -> Path | None:
        hero = self.api.hero_name(m.hero_id)
        stamp = datetime.fromtimestamp(m.start_time).strftime("%Y-%m-%d_%H-%M-%S")
        safe = "".join(c for c in hero if c.isalnum()) or "Deadlock"
        final = self.dir / f"{GameRegistry.DEADLOCK.prefix}_{stamp}_{safe}_{m.result}_{m.kills}-{m.deaths}-{m.assists}.mp4"
        tmp = final.with_name(final.stem + ".tmp.mp4")
        r = self.ffmpeg.run(["-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(video),
                             "-map", "0:v:0", "-map", "0:a?", "-dn", "-map_chapters", "-1", "-c", "copy",
                             "-avoid_negative_ts", "make_zero", str(tmp)], timeout=600)
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1024:
            tmp.unlink(missing_ok=True)
            return None
        game = {"title": hero, "champion": hero, "mode": m.mode, "result": m.result, "kills": m.kills,
                "deaths": m.deaths, "assists": m.assists, "match_id": m.match_id,
                "game_length_s": round(m.duration_s, 1)}
        GameWatcher.write_sidecar(final, GameRegistry.DEADLOCK.id, game, markers)
        tmp.replace(final)
        return final

    def _annotate(self, video: Path, old: dict, plans, bookmarks: list[dict]) -> None:
        """No split: keep one session video, but add each match's markers and a summary."""
        markers = [mk for _, _, mks in plans for mk in mks] + bookmarks
        ms = [m for m, _, _ in plans]
        wins = sum(m.won is True for m in ms)
        game = dict(old.get("game") or {}, title="Deadlock session", matches=len(ms),
                    result=f"{wins}W {sum(m.won is False for m in ms)}L",
                    kills=sum(m.kills for m in ms), deaths=sum(m.deaths for m in ms),
                    assists=sum(m.assists for m in ms))
        GameWatcher.write_sidecar(video, GameRegistry.DEADLOCK.id, game, markers)
        log.info("Deadlock: added %d match(es) to %s", len(ms), video.name)


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
