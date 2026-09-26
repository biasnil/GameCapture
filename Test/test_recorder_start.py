"""Starting a recording when things go wrong (your Apex log: 'Audio hookup failed' every 11 s)."""
import time
import unittest
from unittest.mock import patch

from fakes import FakeClock, FakeProcesses, FakeRecorder
from helpers import TempDirTest

from Capture.ffmpeg import FFmpeg
from Capture.recorder import Recorder
from Core.config import AppConfig, AutoSettings
from Games.registry import GameRegistry
from Games.session_watcher import ProcessSessionWatcher

FF = FFmpeg.locate()
TEST_SOURCE = ["-f", "lavfi", "-i", "testsrc=size=160x90:rate=10"]   # stands in for screen capture


class SimplePipeline:
    def video_args(self, cap):
        return ["-c:v", "libx264", "-preset", "ultrafast", "-g", "20"]


class AudioThatNeverConnects:
    rate, channels, name = 48000, 2, "fake speakers"

    def start(self, port, connect_timeout=10.0, alive=lambda: True):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not alive():
                raise RuntimeError("ffmpeg exited before its audio input opened")
            time.sleep(0.05)
        raise RuntimeError("ffmpeg never opened its audio input")

    def stop(self):
        pass


@unittest.skipUnless(FF, "ffmpeg not found")
class RecorderStartTests(TempDirTest):
    def recorder(self):
        cfg = AppConfig()
        cfg.recording.output_dir = str(self.tmp)
        cfg.log_dir = str(self.tmp / "logs")
        return Recorder(cfg, FF, "x264", SimplePipeline())

    def test_audio_failure_records_without_sound(self):
        rec = self.recorder()
        with patch.object(FFmpeg, "capture_input", staticmethod(lambda *a: TEST_SOURCE)), \
                patch.object(Recorder, "_open_audio", lambda self: AudioThatNeverConnects()):
            self.assertTrue(rec.start(prefix="Apex"), "should fall back to video-only")
            time.sleep(1.5)
            path = rec.stop()
        self.assertTrue(path.exists() and path.stat().st_size > 1000)
        self.assertEqual(len(list(self.tmp.glob("Apex_*"))), 1, "the failed attempt leaves no junk file")
        self.assertIn("(no audio)", (self.tmp / "logs" / "ffmpeg.log").read_text())

    def test_ffmpeg_failing_is_reported_quickly_with_its_reason(self):
        rec = self.recorder()
        broken = ["-f", "lavfi", "-i", "no_such_filter_xyz"]
        started = time.monotonic()
        with patch.object(FFmpeg, "capture_input", staticmethod(lambda *a: broken)), \
                patch.object(Recorder, "_open_audio", lambda self: AudioThatNeverConnects()):
            self.assertFalse(rec.start(prefix="Apex"))
        self.assertLess(time.monotonic() - started, 3, "must not wait 10 s for a dead ffmpeg")
        self.assertTrue(rec.last_error)
        self.assertFalse(rec.is_recording)
        self.assertEqual(list(self.tmp.glob("Apex_*")), [])


class RetryBackoffTests(TempDirTest):
    def test_failed_start_waits_before_retrying(self):
        clock = FakeClock()

        class FailingRecorder(FakeRecorder):
            attempts = 0

            def start(self, prefix=None):
                FailingRecorder.attempts += 1
                return False
        rec = FailingRecorder(self.tmp, clock)
        w = ProcessSessionWatcher(GameRegistry.APEX, AutoSettings(), rec, monitor=FakeProcesses(up=True))
        with patch("Games.session_watcher.time.monotonic", clock), patch("Games.base.time.monotonic", clock):
            for _ in range(120):          # two minutes in game
                w.tick()
                clock.advance(1)
        self.assertEqual(FailingRecorder.attempts, 2, "once, then once more after 60 s - not every tick")


class SilencePcm:
    """Feeds real PCM silence into ffmpeg's audio input over TCP, like LoopbackAudio does."""
    rate, channels, name = 48000, 2, "silence"

    def start(self, port, connect_timeout=10.0, alive=lambda: True):
        import socket
        import threading
        deadline = time.monotonic() + connect_timeout
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                if not alive():
                    raise RuntimeError("ffmpeg exited before its audio input opened")
                if time.monotonic() > deadline:
                    raise RuntimeError("ffmpeg never opened its audio input")
                time.sleep(0.05)
        self.running = True

        def pump():
            chunk = bytes(self.rate // 50 * self.channels * 2)      # 20 ms of silence
            while self.running:
                try:
                    self.sock.sendall(chunk)
                except OSError:
                    return
                time.sleep(0.02)
        threading.Thread(target=pump, daemon=True).start()

    def stop(self):
        self.running = False
        self.sock.close()


@unittest.skipUnless(FF, "ffmpeg not found")
class RealAudioInputTests(TempDirTest):
    """Uses YOUR ffmpeg.exe with the exact audio input arguments GameCapture sends.
    If an ffmpeg update ever breaks them again, this fails here instead of during a match."""

    def test_records_sound_through_the_tcp_input(self):
        cfg = AppConfig()
        cfg.recording.output_dir = str(self.tmp)
        cfg.log_dir = str(self.tmp / "logs")
        rec = Recorder(cfg, FF, "x264", SimplePipeline())
        with patch.object(FFmpeg, "capture_input", staticmethod(lambda *a: TEST_SOURCE)), \
                patch.object(Recorder, "_open_audio", lambda self: SilencePcm()):
            self.assertTrue(rec.start(prefix="Apex"))
            time.sleep(2)
            path = rec.stop()
        info = FF.run(["-i", str(path)]).stderr
        self.assertIn("Audio:", info, (self.tmp / "logs" / "ffmpeg.log").read_text())
        self.assertNotIn("(no audio)", (self.tmp / "logs" / "ffmpeg.log").read_text())

    def test_audio_args_never_use_input_only_options_removed_from_ffmpeg(self):
        import inspect
        src = inspect.getsource(Recorder._launch)
        self.assertNotIn('"-thread_queue_size"', src)
