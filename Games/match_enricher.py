"""After a play session: look up the matches you played online and cut the session into one video per match.

Shared by games that have no live match API but do have match history online afterwards (Deadlock, TFT).
Each game supplies how to find its matches (`find_matches`), their markers (`plan`), and how a match
video is named and labelled (`match_file` / `match_game` / `summary`).

New matches can take a while to show up online, so lookups are queued on disk and retried
(2 min, 10 min, 30 min, 2 h, 6 h) - they survive restarting the app."""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from Core.sidecar import Sidecar
from Games.base import GameWatcher
from Games.registry import GameInfo

log = logging.getLogger("gamecapture.matches")


@dataclass
class EnrichJob:
    video: str
    account_id: int | str     # whatever the game's API knows you by (Steam account id, Riot ID ...)
    rec_start: float          # unix time the recording started
    rec_end: float
    attempt: int = 0
    next_try: float = 0.0


class MatchEnricher:
    GAME: GameInfo
    QUEUE_FILE = ".matches_pending.json"
    RETRY_AFTER = (120, 600, 1800, 7200, 21600)
    PRE_PAD, POST_PAD = 10.0, 15.0
    STALE_AFTER = 600.0   # last match must end within this of the session end, else wait for newer data

    def __init__(self, output_dir: Path, ffmpeg, settings, discard, api, clock=time.time) -> None:
        self.dir = Path(output_dir)
        self.ffmpeg = ffmpeg
        self.settings = settings        # callable -> the game's GameSettings (live)
        self.discard = discard          # recorder.discard: deletes now or when Windows lets go
        self.api = api
        self.clock = clock
        self.file = self.dir / self.QUEUE_FILE
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.jobs: list[EnrichJob] = self._load()

    # ---------- per game ----------

    def find_matches(self, job: EnrichJob) -> list:
        """Your matches around the session: objects with match_id, start_time, duration_s, end_time.
        Raise if the API can't be reached (the lookup is retried)."""
        raise NotImplementedError

    def plan(self, job: EnrichJob, m) -> tuple[float, list[dict]]:
        """Session-video time of the match's clock 0, and the match's markers in session time."""
        raise NotImplementedError

    def match_file(self, m) -> str:
        """File name part after the date, e.g. 'Haze_Win_3-1-2'."""
        raise NotImplementedError

    def match_game(self, m) -> dict:
        """The sidecar's `game` block for one match video."""
        raise NotImplementedError

    def summary(self, old_game: dict, matches: list) -> dict:
        """The sidecar's `game` block when the session is kept as one video."""
        raise NotImplementedError

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
            log.exception("Couldn't save the %s lookup queue", self.GAME.name)

    def enqueue(self, video: Path, account_id, rec_start: float, rec_end: float) -> None:
        with self._lock:
            self.jobs.append(EnrichJob(str(video), account_id, rec_start, rec_end, 0,
                                       self.clock() + self.RETRY_AFTER[0]))
            self._save()
        log.info("%s: will look up the matches in %s in %d min", self.GAME.name, Path(video).name,
                 self.RETRY_AFTER[0] // 60)

    def start(self) -> None:
        threading.Thread(target=self._loop, name=f"{self.GAME.id}-enricher", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(30):
            try:
                self.tick()
            except Exception:
                log.exception("%s lookup failed", self.GAME.name)

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
            found = self.find_matches(job)
        except Exception as exc:
            log.info("%s: match data unavailable (%s) - trying again later", self.GAME.name, exc)
            return "retry"
        matches = sorted((m for m in found if m.end_time > job.rec_start and m.start_time < job.rec_end),
                         key=lambda m: m.start_time)
        fresh = bool(matches) and matches[-1].end_time >= job.rec_end - self.STALE_AFTER
        if not fresh and not final:
            log.info("%s: matches for %s not online yet - trying again later", self.GAME.name, video.name)
            return "retry"
        if not matches:
            log.info("%s: no matches found for %s - keeping it as one session", self.GAME.name, video.name)
            return "done"
        self.apply(video, job, matches)
        return "done"

    def apply(self, video: Path, job: EnrichJob, matches: list) -> None:
        old = Sidecar.read(video) or {}
        bookmarks = [mk for mk in old.get("markers", []) if mk.get("type") == "bookmark"]
        length = self.ffmpeg.duration(video) or (job.rec_end - job.rec_start)
        split = self.settings().options.get("split", True)
        plans = [(m, *self.plan(job, m)) for m in matches]
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
                log.error("%s: cutting match %s failed - keeping the full session", self.GAME.name, m.match_id)
                for p in made:
                    self.discard(p)
                    self.discard(Sidecar.path_for(p))
                return
            made.append(out)
        if not made:
            self._annotate(video, old, plans, bookmarks)
            return
        log.info("%s: split %s into %d match video(s)", self.GAME.name, video.name, len(made))
        self.discard(Sidecar.path_for(video))
        self.discard(video)

    def _cut(self, video: Path, m, start: float, end: float, markers: list[dict]) -> Path | None:
        stamp = datetime.fromtimestamp(m.start_time).strftime("%Y-%m-%d_%H-%M-%S")
        final = self.dir / f"{self.GAME.prefix}_{stamp}_{self.match_file(m)}.mp4"
        tmp = final.with_name(final.stem + ".tmp.mp4")
        r = self.ffmpeg.run(["-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(video),
                             "-map", "0:v:0", "-map", "0:a?", "-dn", "-map_chapters", "-1", "-c", "copy",
                             "-avoid_negative_ts", "make_zero", str(tmp)], timeout=600)
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1024:
            tmp.unlink(missing_ok=True)
            return None
        GameWatcher.write_sidecar(final, self.GAME.id, self.match_game(m), markers)
        tmp.replace(final)
        return final

    def _annotate(self, video: Path, old: dict, plans, bookmarks: list[dict]) -> None:
        """No split: keep one session video, but add each match's markers and a summary."""
        markers = [mk for _, _, mks in plans for mk in mks] + bookmarks
        ms = [m for m, _, _ in plans]
        GameWatcher.write_sidecar(video, self.GAME.id, self.summary(dict(old.get("game") or {}), ms), markers)
        log.info("%s: added %d match(es) to %s", self.GAME.name, len(ms), video.name)

    @staticmethod
    def marker(kind: str, label: str, video_time: float, importance: int = 0, mine: bool = False,
               event: dict | None = None, game_time: float | None = None) -> dict:
        mk = {"type": kind, "label": label, "involves_me": mine, "importance": importance,
              "event": event or {}, "video_time": round(video_time, 2)}
        if game_time is not None:
            mk["game_time"] = round(game_time, 1)
        return mk
