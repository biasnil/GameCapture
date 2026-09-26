"""Quitting the app: the watcher thread must stop cleanly and finish what it was doing."""
from helpers import MatchSimulator, TempDirTest

from Core.config import AutoSettings


class ShutdownTests(TempDirTest):
    def test_real_thread_start_and_shutdown(self):
        """Bug: quitting crashed with 'LeagueMatchWatcher._stop() missing 2 required arguments'."""
        sim = MatchSimulator(self.tmp, settings=AutoSettings(poll_interval=0.05))
        sim.watcher.start()
        self.assertTrue(sim.watcher.is_alive())
        sim.watcher.shutdown()          # must not raise
        self.assertFalse(sim.watcher.is_alive())

    def test_shutdown_mid_match_still_saves_it(self):
        sim = MatchSimulator(self.tmp)
        sim.run(seconds=60)             # quit 60 s into the match
        sim.watcher.shutdown()
        stops = sim.recorder.stops
        self.assertEqual(len(stops), 1)
        self.assertTrue(stops[0]["tag"].startswith("Ahri_Partial_"))


class PendingDeleteTests(TempDirTest):
    def test_locked_leftover_is_remembered_across_restarts(self):
        """Bug: a locked leftover .mkv came back as a 'Manual recording' after restarting."""
        from pathlib import Path
        from unittest.mock import patch

        from Capture.recorder import Recorder
        from Core.config import AppConfig
        from Core.library import RecordingLibrary
        cfg = AppConfig()
        cfg.recording.output_dir = str(self.tmp)
        cfg.log_dir = str(self.tmp / "logs")
        leftover = self.tmp / "LoL_2026-09-26_18-19-25.mkv"
        leftover.write_bytes(b"x")
        real_unlink = Path.unlink

        def locked(path, missing_ok=False):
            if path == leftover:
                raise PermissionError(32, "in use")
            return real_unlink(path, missing_ok=missing_ok)
        first = Recorder(cfg, None, "x264", pipeline=object())
        with patch.object(Path, "unlink", locked), patch("Capture.recorder.time.sleep"):
            first.discard(leftover)
        # app closed and reopened: still hidden, and deleted once it's free
        second = Recorder(cfg, None, "x264", pipeline=object())
        self.assertIn(leftover, second.busy_files)
        self.assertEqual(RecordingLibrary(self.tmp).scan(exclude=second.busy_files), [])
        second.check_alive()
        self.assertFalse(leftover.exists())
        self.assertFalse((self.tmp / ".pending_deletes.json").exists())


class EngineShutdownTests(TempDirTest):
    def test_one_failing_step_does_not_skip_the_rest(self):
        from Core.config import AppConfig
        from Core.engine import Engine

        class Boom:
            def shutdown(self):
                raise RuntimeError("watcher broke")

        class Rec:
            is_recording, stopped = True, False

            def stop(self):
                Rec.stopped = True
        engine = Engine(AppConfig())
        engine.watcher, engine.recorder = Boom(), Rec()
        engine.shutdown()
        self.assertTrue(Rec.stopped, "the recording must still be saved")
