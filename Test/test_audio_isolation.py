"""Audio isolation: game / chosen apps / microphone, volumes, and separate audio tracks."""
import sys
import time
import types
import unittest
from unittest.mock import patch

import numpy as np
from helpers import TempDirTest

from Capture.audio_mixer import AudioMixer
from Capture.audio_sources import (AppAudioSource, AudioSource, FrameBuffer, MicrophoneSource, ProcessFinder,
                                   StereoConverter, SystemAudioSource)
from Capture.ffmpeg import FFmpeg
from Capture.recorder import Recorder
from Core.config import AppConfig

FF = FFmpeg.locate()


class ToneSource(AudioSource):
    """Constant-level stand-in for a real device."""

    def __init__(self, label, level, volume=1.0):
        super().__init__(label, volume)
        self.level = level

    def pull(self, n):
        return np.full((n, 2), self.level * self.volume, np.float32)


class BufferTests(unittest.TestCase):
    def test_pads_with_silence(self):
        b = FrameBuffer()
        b.push(np.ones((10, 2), np.float32))
        out = b.pull(15)
        self.assertEqual(out[:10].sum(), 20)
        self.assertEqual(out[10:].sum(), 0)

    def test_latency_is_trimmed(self):
        b = FrameBuffer(max_s=0.1, keep_s=0.02)
        for _ in range(20):
            b.push(np.ones((960, 2), np.float32))          # 20 ms each, never drained
        self.assertLessEqual(len(b), 48000 * 0.1)


class ConverterTests(unittest.TestCase):
    def test_mono_becomes_stereo(self):
        out = StereoConverter(48000, 1).convert(np.array([0.1, 0.2], np.float32))
        self.assertEqual(out.shape, (2, 2))
        self.assertTrue(np.allclose(out[:, 0], out[:, 1]))

    def test_44k1_resampled_without_drift(self):
        conv, total = StereoConverter(44100, 2), 0
        for _ in range(100):                               # 100 chunks of 10 ms
            total += len(conv.convert(np.zeros(441 * 2, np.float32)))
        self.assertAlmostEqual(total, 48000, delta=3)      # one second in, one second out


class MixerTests(unittest.TestCase):
    def test_mix_volumes_and_tracks(self):
        mixer = AudioMixer([ToneSource("Game", 0.25), ToneSource("Microphone", 0.5, volume=0.5)], separate_tracks=True)
        self.assertEqual(mixer.track_names, ["Mix", "Game", "Microphone"])
        mix, tracks = mixer.mix(4)
        samples = np.frombuffer(mix, "<i2")
        self.assertTrue(np.all(np.abs(samples - int(0.5 * 32767)) <= 1))   # 0.25 + 0.5*0.5
        self.assertEqual(len(tracks), 2)

    def test_clipping_is_safe(self):
        mixer = AudioMixer([ToneSource("A", 0.9), ToneSource("B", 0.9)])
        samples = np.frombuffer(mixer.mix(4)[0], "<i2")
        self.assertTrue(np.all(samples == 32767))

    def test_broken_sources_are_dropped(self):
        class Broken(AudioSource):
            def start(self):
                raise RuntimeError("no device")
        mixer = AudioMixer([Broken("Microphone"), ToneSource("Game", 0.1)], separate_tracks=True)
        mixer.open_sources()
        self.assertEqual([s.label for s in mixer.sources], ["Game"])
        self.assertFalse(mixer.separate_tracks, "one source left: no point in extra tracks")
        with self.assertRaises(RuntimeError):
            AudioMixer([Broken("Microphone")]).open_sources()


class FakeProcTap:
    """Stands in for the proc-tap package: remembers which PIDs were captured."""
    started = []

    class ProcessAudioCapture:
        def __init__(self, pid, on_data=None):
            self.pid, self.on_data = pid, on_data

        def start(self):
            FakeProcTap.started.append(self.pid)
            self.on_data(np.full(960 * 2, 0.3, np.float32).tobytes(), -1)

        def close(self):
            pass


class AppSourceTests(unittest.TestCase):
    def setUp(self):
        FakeProcTap.started = []
        self.modules = patch.dict(sys.modules, {"proctap": types.SimpleNamespace(
            ProcessAudioCapture=FakeProcTap.ProcessAudioCapture)})
        self.modules.start()

    def tearDown(self):
        self.modules.stop()

    def test_captures_the_app_and_attaches_late(self):
        running = []
        with patch.object(ProcessFinder, "top_pids", lambda names: list(running)):
            src = AppAudioSource("Discord", ("Discord.exe",))
            src.start()                                    # Discord not open yet
            self.assertEqual(FakeProcTap.started, [])
            running.append(4242)                           # user opens Discord
            src._last_scan = 0
            src.maintain()
        self.assertEqual(FakeProcTap.started, [4242])
        self.assertAlmostEqual(float(src.pull(960)[0, 0]), 0.3, places=5)

    def test_only_top_level_process_is_hooked(self):
        procs = [types.SimpleNamespace(pid=10, info={"name": "Discord.exe", "ppid": 1}),
                 types.SimpleNamespace(pid=11, info={"name": "Discord.exe", "ppid": 10}),   # voice child
                 types.SimpleNamespace(pid=12, info={"name": "Discord.exe", "ppid": 10}),
                 types.SimpleNamespace(pid=20, info={"name": "chrome.exe", "ppid": 1})]
        fake_psutil = types.SimpleNamespace(process_iter=lambda attrs: procs)
        with patch.dict(sys.modules, {"psutil": fake_psutil}):
            self.assertEqual(ProcessFinder.top_pids(["discord.EXE"]), [10])


class SourceSelectionTests(TempDirTest):
    def recorder(self, **capture):
        cfg = AppConfig()
        cfg.recording.output_dir = str(self.tmp)
        cfg.log_dir = str(self.tmp / "logs")
        for k, v in capture.items():
            setattr(cfg.capture, k, v)
        rec = Recorder(cfg, None, "x264", pipeline=object())
        return rec

    def sources(self, rec, audio_apps=()):
        rec._audio_apps = tuple(audio_apps)
        with patch.object(AppAudioSource, "start", lambda self: None), \
                patch.object(MicrophoneSource, "start", lambda self: None), \
                patch.object(SystemAudioSource, "start", lambda self: None):
            audio = rec._open_audio()
        return audio

    def test_default_is_the_simple_everything_path(self):
        from Capture.audio import LoopbackAudio
        with patch.object(LoopbackAudio, "__init__", lambda self: None):
            self.assertIsInstance(self.recorder()._open_audio(), LoopbackAudio)

    def test_isolated_game_discord_and_mic(self):
        rec = self.recorder(audio_mode="isolated", mic=True, mic_volume=80, audio_tracks=True,
                            apps=[{"exe": "Discord.exe", "volume": 50, "enabled": True},
                                  {"exe": "Spotify.exe", "volume": 100, "enabled": False}])
        mixer = self.sources(rec, audio_apps=("r5apex.exe",))
        self.assertEqual([s.label for s in mixer.sources], ["Game", "Discord", "Microphone"])
        self.assertEqual(mixer.sources[0].exe_names, ("r5apex.exe",))
        self.assertEqual([s.volume for s in mixer.sources], [1.0, 0.5, 0.8])
        self.assertEqual(mixer.track_names, ["Mix", "Game", "Discord", "Microphone"])

    def test_everything_plus_mic(self):
        mixer = self.sources(self.recorder(mic=True))
        self.assertEqual([s.label for s in mixer.sources], ["Everything", "Microphone"])


@unittest.skipUnless(FF, "ffmpeg not found")
class MultiTrackRecordingTests(TempDirTest):
    def test_file_has_mix_plus_named_tracks(self):
        cfg = AppConfig()
        cfg.recording.output_dir = str(self.tmp)
        cfg.log_dir = str(self.tmp / "logs")

        class Pipe:
            def video_args(self, cap):
                return ["-c:v", "libx264", "-preset", "ultrafast", "-g", "20"]
        rec = Recorder(cfg, FF, "x264", Pipe())
        mixer = AudioMixer([ToneSource("Game", 0.2), ToneSource("Microphone", 0.1)], separate_tracks=True)
        with patch.object(FFmpeg, "capture_input", staticmethod(lambda *a: ["-f", "lavfi", "-i",
                                                                             "testsrc=size=160x90:rate=10"])), \
                patch.object(Recorder, "_open_audio", lambda self: mixer):
            self.assertTrue(rec.start(prefix="Apex"))
            time.sleep(2)
            path = rec.stop()
        info = FF.run(["-i", str(path)]).stderr
        self.assertEqual(info.count("Audio:"), 3, info)
        for name in ("Mix", "Game", "Microphone"):
            self.assertRegex(info, rf"(title|handler_name)\s+: {name}\n")


class FakePyAudio:
    """Stands in for PyAudioWPatch. Like Windows, a device opens ONLY in the layouts it allows."""
    paFloat32, paContinue, paWASAPI = 1, 0, 13
    allowed = {8}
    device = {"index": 3, "name": "Speakers (HyperX Cloud III)", "maxInputChannels": 8,
              "defaultSampleRate": 48000.0, "isLoopbackDevice": True}
    opened = []

    class PyAudio:
        def get_host_api_info_by_type(self, t):
            return {"defaultOutputDevice": 3}

        def get_device_info_by_index(self, i):
            return FakePyAudio.device

        def get_default_input_device_info(self):
            return FakePyAudio.device

        def open(self, channels, stream_callback=None, **kw):
            if channels not in FakePyAudio.allowed:
                raise OSError(-9998, "Invalid number of channels")
            FakePyAudio.opened.append(channels)
            data = np.zeros((480, channels), np.float32)
            data[:, 2 if channels > 2 else 0] = 0.5       # centre channel (voices) on surround
            stream_callback(data.tobytes(), 480, None, 0)
            return types.SimpleNamespace(stop_stream=lambda: None, close=lambda: None)

        def terminate(self):
            pass


class SurroundHeadsetTests(unittest.TestCase):
    """Bug: with 'everything on', game sound was missing - the loopback was forced to 2 channels,
    which Windows refuses for a 7.1 headset."""

    def setUp(self):
        FakePyAudio.opened = []
        self.mod = patch.dict(sys.modules, {"pyaudiowpatch": FakePyAudio})
        self.mod.start()

    def tearDown(self):
        self.mod.stop()
        FakePyAudio.allowed = {8}

    def test_71_loopback_opens_in_its_own_layout_and_is_downmixed(self):
        src = SystemAudioSource()
        src.start()
        self.assertEqual(FakePyAudio.opened, [8])
        frames = src.pull(480)
        self.assertGreater(frames[0, 0], 0.1, "centre channel (voices) reaches the left ear")
        self.assertAlmostEqual(float(frames[0, 0]), float(frames[0, 1]), places=5)

    def test_falls_back_to_stereo_when_native_is_refused(self):
        FakePyAudio.allowed = {2}
        src = MicrophoneSource()
        src.start()
        self.assertEqual(FakePyAudio.opened, [2])
        self.assertGreater(float(src.pull(480)[0, 0]), 0.1)

    def test_reports_every_attempt_when_nothing_works(self):
        FakePyAudio.allowed = set()
        with self.assertRaises(RuntimeError) as ctx:
            SystemAudioSource().start()
        self.assertIn("8 ch", str(ctx.exception))

    def test_downmix_never_clips(self):
        full = np.ones((10, 8), np.float32)
        self.assertLessEqual(float(StereoConverter.downmix(full).max()), 1.0001)


class AudioTestAndWarningTests(TempDirTest):
    def test_self_test_reports_each_source(self):
        class Broken(AudioSource):
            def start(self):
                raise RuntimeError("Invalid number of channels")

        class Quiet(ToneSource):
            pass
        loud, quiet = ToneSource("Game", 0.25), Quiet("Discord", 0.0)
        report = dict(AudioMixer([loud, quiet, Broken("Microphone")]).self_test(seconds=0.1))
        self.assertTrue(report["Game"].startswith("OK"))
        self.assertTrue(report["Discord"].startswith("silent"))
        self.assertIn("Invalid number of channels", report["Microphone"])

    def test_recording_warns_when_a_source_is_missing(self):
        cfg = AppConfig()
        cfg.recording.output_dir = str(self.tmp)
        cfg.log_dir = str(self.tmp / "logs")
        cfg.capture.mic = True
        rec = Recorder(cfg, None, "x264", pipeline=object())

        def broken(self):
            raise RuntimeError("no device")
        with patch.object(SystemAudioSource, "start", broken), patch.object(MicrophoneSource, "start", lambda s: None):
            mixer = rec._open_audio()
        self.assertEqual([s.label for s in mixer.sources], ["Microphone"])
        self.assertEqual(rec.audio_warning, "no everything sound")
