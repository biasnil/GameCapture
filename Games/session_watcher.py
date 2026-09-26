"""Games without an API (Apex, Valorant, Deadlock, Marvel Rivals, R.E.P.O.): record the whole time
the game is open. Highlights come from the Bookmark hotkey."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from Core.config import AutoSettings
from Games.base import GameWatcher
from Games.processes import ProcessMonitor
from Games.registry import GameInfo

log = logging.getLogger("gamecapture.session")


@dataclass
class GameSession:
    recording_id: str | None
    started: float = field(default_factory=time.monotonic)


class ProcessSessionWatcher(GameWatcher):
    GRACE = 15.0  # game gone this long = closed (covers launcher -> game hand-offs and crashes)

    def __init__(self, game: GameInfo, settings: AutoSettings, recorder, enabled=lambda game_id: True,
                 monitor=None) -> None:
        self.GAME = game  # per-instance: one watcher class for many games
        super().__init__(settings, recorder, enabled)
        self.monitor = monitor or ProcessMonitor(game.processes)
        self.session: GameSession | None = None
        self._seen = 0.0
        self._suppress_until_closed = False

    def audio_processes(self) -> tuple[str, ...]:
        return tuple(getattr(self.monitor, "names", None) or self.GAME.processes)  # incl. a renamed exe

    def tick(self) -> None:
        now = time.monotonic()
        running = self.monitor.running()
        if running:
            self._seen = now
        elif now - self._seen > self.GRACE:
            self._suppress_until_closed = False

        s = self.session
        if s is None:
            if running and not self._suppress_until_closed and self.enabled(self.GAME.id) \
                    and not self.recorder.is_recording:
                if self.start_recording(self.GAME.prefix) and self.recorder.is_recording:
                    self.session = GameSession(self.recorder.current_id, started=now)
                    log.info("%s started - recording until it closes", self.GAME.name)
            return
        if self.recorder.current_id != s.recording_id:
            log.info("%s recording stopped manually - it won't restart until the game is closed", self.GAME.name)
            self.session = None
            self._suppress_until_closed = True
        elif not running and now - self._seen > self.GRACE:
            self._end("game closed")

    def finish(self, reason: str) -> None:
        if self.session is not None:
            self._end(reason)

    def live_status(self) -> dict | None:
        if self.session is None:
            return None
        n = len(getattr(self.recorder, "_bookmarks", []))
        return {"title": self.GAME.name, "kda": "", "highlights": n}

    def _end(self, reason: str) -> None:
        s, self.session = self.session, None
        if not self.recorder.is_recording or self.recorder.current_id != s.recording_id:
            return
        log.info("%s session over (%s)", self.GAME.name, reason)
        markers = self.bookmark_markers()
        chapters = self.chapters(markers) if self.settings.chapters and markers else None
        path = self.stop_recording("Session", chapters)
        if path is not None:
            game = {"title": self.GAME.name, "mode": "", "result": "",
                    "game_length_s": round(time.monotonic() - s.started, 1)}
            self.write_sidecar(path, self.GAME.id, game, markers)
