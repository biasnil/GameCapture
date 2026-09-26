"""Is a game running? One shared process snapshot, refreshed at most every few seconds,
so several games can be watched for the price of one scan."""
from __future__ import annotations

import threading
import time


class ProcessSnapshot:
    INTERVAL = 3.0
    _names: set[str] = set()
    _taken = 0.0
    _lock = threading.Lock()

    @classmethod
    def names(cls) -> set[str]:
        with cls._lock:
            now = time.monotonic()
            if now - cls._taken >= cls.INTERVAL:
                cls._taken = now
                cls._names = cls._scan()
            return cls._names

    @staticmethod
    def _scan() -> set[str]:
        import psutil
        names = set()
        for proc in psutil.process_iter(["name"]):
            name = proc.info.get("name")
            if name:
                names.add(name.lower())
        return names


class ProcessMonitor:
    """True while any of `names` is running."""

    def __init__(self, names) -> None:
        self.names = {n.lower() for n in names}

    def running(self) -> bool:
        return bool(self.names & ProcessSnapshot.names())


class LeagueProcesses(ProcessMonitor):
    def __init__(self) -> None:
        from Games.registry import GameRegistry
        super().__init__(GameRegistry.LEAGUE.processes)
