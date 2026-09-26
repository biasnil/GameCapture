"""GameCapture - game auto-recorder with highlight clips.

    python Tools\\get_ffmpeg.py     one-time: download ffmpeg.exe into Bin\\
    python main.py                 desktop app (default)
    python main.py run             console mode (no window)
    python main.py check           verify ffmpeg, GPU encoder and audio device
    python main.py test            record 5 seconds and save it
    python main.py live            show what League's Live Client API reports right now
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from Core.config import AppConfig
from Core.engine import Engine
from Core.logging_setup import LogSetup

log = logging.getLogger("gamecapture")


class GameCaptureCLI:
    COMMANDS = ("gui", "run", "check", "test", "live")

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    def dispatch(self, command: str, seconds: int = 5) -> int:
        if command == "gui":
            from UI.application import GameCaptureApp
            return GameCaptureApp(self.cfg).run()
        return {"run": self.run_console, "check": self.check, "live": self.live,
                "test": lambda: self.test(seconds)}[command]()

    # ---------- commands ----------

    def check(self) -> int:
        ok = Engine(self.cfg).prepare()
        if self.cfg.capture.audio:
            try:
                from Capture.audio import LoopbackAudio
                audio = LoopbackAudio()
                log.info("Audio: %s (%d Hz, %d ch)", audio.name, audio.rate, audio.channels)
                audio.stop()
            except Exception as exc:
                log.warning("Audio unavailable: %s", exc)
        log.info("Recordings folder: %s", self.cfg.recording.resolved_output_dir())
        return 0 if ok else 1

    def test(self, seconds: int) -> int:
        engine = Engine(self.cfg)
        if not engine.prepare():
            return 1
        from Capture.recorder import Recorder
        recorder = Recorder(self.cfg, engine.ffmpeg, engine.encoder)
        if not recorder.start():
            return 1
        time.sleep(seconds)
        return 0 if recorder.stop() else 1

    def live(self) -> int:
        from Games.league_client import LeagueLiveClient
        client = LeagueLiveClient()
        stats = client.game_stats()
        if stats is None:
            log.info("No match running (Live Client API not answering)")
            return 0
        log.info("Game: %s, %.0f s in", stats.get("gameMode"), stats.get("gameTime", 0))
        log.info("You: %s", client.active_player_name() or "(spectating / replay / loading)")
        events = client.events()
        log.info("Events so far: %d", len(events))
        for ev in events[-10:]:
            log.info("  %7.1fs  %s", ev.get("EventTime", 0), ev.get("EventName"))
        return 0

    def run_console(self) -> int:
        engine = Engine(self.cfg)
        quit_event = threading.Event()
        if not engine.start(on_quit=quit_event.set):
            return 1
        threading.Thread(target=self._console_loop, args=(engine, quit_event), daemon=True).start()
        log.info("Ready. %s = manual start/stop, %s = quit. Console: t / s / q",
                 self.cfg.hotkeys.toggle, self.cfg.hotkeys.quit)
        try:
            while not quit_event.wait(0.5):
                engine.tick()
        except KeyboardInterrupt:
            log.info("Ctrl+C received")
        finally:
            engine.shutdown()
            log.info("GameCapture closed")
        return 0

    @staticmethod
    def _console_loop(engine: Engine, quit_event: threading.Event) -> None:
        recorder = engine.recorder
        while not quit_event.is_set():
            try:
                line = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                return
            try:
                if line in ("t", "toggle"):
                    recorder.toggle()
                elif line in ("s", "status"):
                    recorder.log_status()
                elif line in ("q", "quit", "exit"):
                    quit_event.set()
                elif line:
                    print("Commands: t = toggle, s = status, q = quit")
            except Exception as exc:
                log.error("Command failed: %s", exc)

    # ---------- entry point ----------

    @classmethod
    def main(cls, argv: list[str] | None = None) -> int:
        parser = argparse.ArgumentParser(description="GameCapture - game auto-recorder")
        parser.add_argument("command", nargs="?", default="gui", choices=cls.COMMANDS)
        parser.add_argument("--seconds", type=int, default=5, help="length of the test recording")
        args = parser.parse_args(argv)
        cfg = AppConfig.load()
        LogSetup.configure(cfg.resolved_log_dir(), cfg.log_level)
        return cls(cfg).dispatch(args.command, args.seconds)


if __name__ == "__main__":
    sys.exit(GameCaptureCLI.main())
