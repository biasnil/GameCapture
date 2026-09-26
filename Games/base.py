"""What every game watcher shares: its own thread, safe shutdown, bookmarks and sidecar writing."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from Core.config import AutoSettings
from Core.sidecar import Sidecar
from Games.registry import GameInfo

log = logging.getLogger("gamecapture.games")


class GameWatcher:
    GAME: GameInfo
    RETRY_AFTER = 60.0   # after a recording fails to start, wait this long before trying again
    _failed_at = float("-inf")

    def __init__(self, settings: AutoSettings, recorder, enabled=lambda game_id: True) -> None:
        # Owns a thread rather than *being* one (a Thread subclass once shadowed Thread._stop()).
        self._thread = threading.Thread(target=self.run, name=f"{self.GAME.id}-watcher", daemon=True)
        self._stop_event = threading.Event()
        self.settings = settings
        self.recorder = recorder
        self.enabled = enabled   # enabled(game_id) -> bool, checked live before each new recording

    # ---------- thread ----------

    def start(self) -> None:
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def run(self) -> None:
        log.info("Watching for %s", self.GAME.name)
        while not self._stop_event.wait(self.settings.poll_interval):
            try:
                self.tick()
            except Exception:
                log.exception("%s watcher error", self.GAME.name)

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        self.finish("app closed")

    # ---------- per game ----------

    def tick(self) -> None:
        raise NotImplementedError

    def finish(self, reason: str) -> None:
        """Save whatever is in progress (called when the app closes)."""

    def live_status(self) -> dict | None:
        """{'title', 'kda', 'highlights'} while this game has a recording going, else None."""
        return None

    # ---------- shared helpers ----------

    def bookmark_markers(self) -> list[dict]:
        """Bookmarks pressed during the recording that just stopped, as highlight markers."""
        take = getattr(self.recorder, "take_bookmarks", None)
        times = take() if take else []
        return [{"type": "bookmark", "label": f"Bookmark {i}", "involves_me": True, "importance": 2,
                 "video_time": round(t, 2), "event": {}} for i, t in enumerate(times, 1)]

    def stop_recording(self, tag: str | None, chapters=None, hold: bool = False) -> Path | None:
        try:
            return self.recorder.stop(tag=tag if self.settings.rename_with_result else None,
                                      chapters=chapters, hold=hold)
        except Exception:
            log.exception("Stopping the recording failed")
            return None

    def audio_processes(self) -> tuple[str, ...]:
        """Executables whose sound counts as 'the game' in isolated audio mode."""
        return tuple(self.GAME.processes)

    def start_recording(self, prefix: str | None = None) -> bool:
        now = time.monotonic()
        if now - self._failed_at < self.RETRY_AFTER:
            return False
        try:
            ok = self.recorder.start(prefix=prefix, audio_apps=self.audio_processes())
        except TypeError:  # recorders without these options (tests)
            ok = self.recorder.start()
        if not ok and not self.recorder.is_recording:
            self._failed_at = now
            log.error("%s: recording couldn't start - trying again in %d s (details in the Log)",
                      self.GAME.name, self.RETRY_AFTER)
        return ok

    @staticmethod
    def chapters(markers: list[dict]) -> list[tuple[float, str]]:
        """Your moments + start/end become chapters; same-second events are merged."""
        out: list[tuple[float, str]] = []
        for mk in sorted(markers, key=lambda m: m["video_time"]):
            if not (mk["involves_me"] or mk["type"] in ("game_start", "game_end")):
                continue
            t, label = mk["video_time"], mk["label"]
            if out and t - out[-1][0] < 1.0:
                out[-1] = (out[-1][0], f"{out[-1][1]} + {label}")
            else:
                out.append((t, label))
        return out

    @staticmethod
    def write_sidecar(video: Path, game_id: str, game: dict, markers: list[dict]) -> dict | None:
        data = Sidecar.build(video.name, game_id, game, sorted(markers, key=lambda m: m["video_time"]))
        try:
            Sidecar.write(video, data)
        except Exception:
            log.exception("Could not write highlights for %s", video.name)
            return None
        mine = sum(1 for mk in markers if mk["involves_me"])
        log.info("Highlights: %d markers (%d involving you) -> %s", len(markers), mine, Sidecar.path_for(video).name)
        return data
