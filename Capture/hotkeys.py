"""Global hotkeys via pynput. Callbacks run on a worker thread so the Windows keyboard
hook never blocks on slow work (slow hooks get silently removed by Windows)."""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

from pynput import keyboard

log = logging.getLogger("gamecapture.hotkeys")


class HotkeyListener:
    def __init__(self, bindings: dict[str, Callable[[], None]]) -> None:
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue()
        hooked = {combo: (lambda fn=fn: self._queue.put(fn)) for combo, fn in bindings.items()}
        try:
            self._listener = keyboard.GlobalHotKeys(hooked)
        except ValueError as exc:
            raise ValueError(f"Invalid hotkey in config.json: {exc}") from exc
        self._worker = threading.Thread(target=self._run, name="hotkey-worker", daemon=True)

    def start(self) -> None:
        self._listener.start()
        self._worker.start()

    def stop(self) -> None:
        self._listener.stop()
        self._queue.put(None)

    def _run(self) -> None:
        while (fn := self._queue.get()) is not None:
            try:
                fn()
            except Exception as exc:
                log.error("Hotkey action failed: %s", exc)
