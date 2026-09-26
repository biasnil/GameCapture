"""Stand-ins for the real Live Client and Recorder, driven by a controllable clock."""
from __future__ import annotations

from pathlib import Path

from scenario import Scenario


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeLiveClient:
    """Same interface as LeagueLiveClient. The game starts `delay` seconds after creation and the
    API goes silent `linger` seconds after GameEnd (like the real client closing)."""

    def __init__(self, scenario: Scenario, clock: FakeClock, delay: float = 0.0, linger: float = 8.0,
                 crash_at: float | None = None) -> None:
        self.s = scenario
        self.clock = clock
        self.start = clock() + delay
        self.linger = linger
        self.crash_at = crash_at  # game time at which the API vanishes (crash/disconnect)

    @property
    def game_time(self) -> float:
        return self.clock() - self.start

    def _up(self) -> bool:
        t = self.game_time
        if t < 0 or (self.crash_at is not None and t >= self.crash_at):
            return False
        return t <= self.s.end_time + self.linger

    def game_stats(self):
        return self.s.game_stats(self.game_time) if self._up() else None

    def active_player_name(self):
        return self.s.active_player_name() if self._up() else None

    def player_list(self):
        return self.s.player_list(self.game_time) if self._up() else None

    def events(self):
        return self.s.events_until(self.game_time) if self._up() else []


class FakeRecorder:
    """Recorder without ffmpeg: 'recordings' are small placeholder files."""

    def __init__(self, out_dir: Path, clock: FakeClock) -> None:
        self.out_dir = out_dir
        self.clock = clock
        self._start: float | None = None
        self._n = 0
        self.stops: list[dict] = []   # every stop() call, for assertions
        self.held: set = set()
        self._bookmarks: list[float] = []
        self.prefix = "LoL"

    @property
    def is_recording(self) -> bool:
        return self._start is not None

    @property
    def current_id(self):
        return f"rec{self._n}" if self.is_recording else None

    @property
    def busy_files(self):
        return set()

    @property
    def is_finalizing(self) -> bool:
        return False

    def elapsed(self) -> float:
        return self.clock() - self._start if self.is_recording else 0.0

    def start(self, prefix: str | None = None, audio_apps=None) -> bool:
        self.audio_apps = tuple(audio_apps or ())
        if self.is_recording:
            return False
        self._n += 1
        self._start = self.clock()
        self.prefix = prefix or "LoL"
        self._bookmarks = []
        return True

    def bookmark(self):
        if not self.is_recording:
            return None
        self._bookmarks.append(self.elapsed())
        return self._bookmarks[-1]

    def take_bookmarks(self):
        marks, self._bookmarks = self._bookmarks, []
        return marks

    def stop(self, tag: str | None = None, chapters=None, hold: bool = False):
        if not self.is_recording:
            return None
        path = self.out_dir / f"{self.prefix}_rec{self._n}{'_' + tag if tag else ''}.mp4"
        path.write_bytes(b"fake video")
        self.stops.append({"tag": tag, "chapters": chapters, "path": path, "duration": self.elapsed(), "hold": hold})
        self._start = None
        if hold:
            self.held.add(path)
        return path

    def release(self, path) -> None:
        self.held.discard(path)

    def discard(self, path) -> None:
        path.unlink(missing_ok=True)

    def toggle(self) -> None:
        self.stop() if self.is_recording else self.start()


class FakeProcesses:
    """Stand-in for LeagueProcesses: flip `.up` to 'open' or 'close' League."""

    def __init__(self, up: bool = False) -> None:
        self.up = up

    def running(self) -> bool:
        return self.up
