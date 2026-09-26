"""Shared test plumbing."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fakes import FakeClock, FakeLiveClient, FakeRecorder
from scenario import Scenario

from Core.config import AutoSettings
from Games.league_watcher import LeagueMatchWatcher


class TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class MatchSimulator:
    """Runs a whole scripted match through the real LeagueMatchWatcher on a fake clock."""

    def __init__(self, out_dir: Path, scenario: Scenario | None = None,
                 settings: AutoSettings | None = None, **client_kw) -> None:
        self.clock = FakeClock()
        self.recorder = FakeRecorder(out_dir, self.clock)
        self.watcher = LeagueMatchWatcher(settings or AutoSettings(post_roll_seconds=5, end_grace_seconds=15),
                                          self.recorder)
        self.watcher.client = FakeLiveClient(scenario or Scenario.quick(), self.clock, **client_kw)

    def run(self, seconds: float = 400, step: float = 1.0, during=None) -> "MatchSimulator":
        """`during(sim)` runs after every tick, for mid-match interventions."""
        with patch("Games.league_watcher.time.monotonic", self.clock):
            elapsed = 0.0
            while elapsed < seconds:
                self.watcher.tick()
                if during:
                    during(self)
                self.clock.advance(step)
                elapsed += step
        return self
