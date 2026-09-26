"""Quality presets, rate control, the resize pipeline, per-game settings and the games catalogue."""
import json
import unittest

from helpers import MatchSimulator, TempDirTest

from Capture.ffmpeg import FFmpeg, RateControl
from Capture.pipeline import VideoPipeline
from Capture.presets import VideoPresets
from Core.config import AppConfig, CaptureSettings
from Games.registry import GameRegistry


class FakeFFmpeg:
    """Pretends to be ffmpeg: a fixed screen size and a set of filters that 'work'."""

    def __init__(self, screen=(2560, 1440), working=()):
        self.screen = screen
        self.working = set(working)
        self.probed = []

    def screen_size(self, monitor):
        return self.screen

    def probe_filter(self, encoder, monitor, vf):
        self.probed.append(vf)
        return any(w in vf for w in self.working)


class PresetTests(unittest.TestCase):
    def test_apply_and_detect(self):
        cap = CaptureSettings()
        for preset in VideoPresets.ALL:
            preset.apply(cap)
            self.assertEqual(VideoPresets.current(cap), preset.key)
        cap.fps = 144
        self.assertEqual(VideoPresets.current(cap), "custom")

    def test_defaults_are_the_high_preset(self):
        self.assertEqual(VideoPresets.current(CaptureSettings()), "high")

    def test_bitrate_mode_is_custom(self):
        cap = CaptureSettings(rate_control="bitrate")
        self.assertEqual(VideoPresets.current(cap), "custom")


class RateControlTests(unittest.TestCase):
    def test_nvenc_quality_and_bitrate(self):
        nv = FFmpeg.encoder("nvenc")
        self.assertIn("-cq", nv.capture_args(60, RateControl("quality", 21), None))
        args = nv.capture_args(60, RateControl("bitrate", bitrate_kbps=12000), None)
        self.assertEqual(args[args.index("-b:v") + 1], "12000k")
        self.assertEqual(args[args.index("-maxrate") + 1], "18000k")
        self.assertEqual(args[-2:], ["-g", "120"])

    def test_every_encoder_builds_both_modes(self):
        for name, profile in FFmpeg.ENCODERS.items():
            for rc in (RateControl("quality"), RateControl("bitrate")):
                args = profile.capture_args(30, rc, "scale=1:1")
                self.assertEqual(args[:2], ["-vf", "scale=1:1"], name)
                self.assertNotIn("{", " ".join(args), f"{name}: unfilled placeholder")


class PipelineTests(unittest.TestCase):
    def cap(self, resolution):
        return CaptureSettings(resolution=resolution)

    def test_native_needs_no_probing(self):
        ff = FakeFFmpeg()
        plan = VideoPipeline(ff, "nvenc").plan(self.cap("native"))
        self.assertEqual((plan.scaling, plan.size, ff.probed), ("native", (2560, 1440), []))

    def test_never_upscales(self):
        plan = VideoPipeline(FakeFFmpeg(screen=(1920, 1080)), "nvenc").plan(self.cap("1440p"))
        self.assertEqual(plan.scaling, "native")

    def test_gpu_scaler_fallback_order(self):
        ff = FakeFFmpeg(working={"scale_cuda"})  # scale_d3d11 missing in this build
        plan = VideoPipeline(ff, "nvenc").plan(self.cap("1080p"))
        self.assertEqual((plan.scaling, plan.size), ("gpu", (1920, 1080)))
        self.assertIn("scale_d3d11", ff.probed[0])
        self.assertIn("1920:1080", plan.filter)

    def test_cpu_fallback_and_warning(self):
        plan = VideoPipeline(FakeFFmpeg(working={"hwdownload"}), "nvenc").plan(self.cap("720p"))
        self.assertEqual((plan.scaling, plan.size), ("cpu", (1280, 720)))
        self.assertIn("CPU", plan.description)

    def test_nothing_works_records_native(self):
        plan = VideoPipeline(FakeFFmpeg(working=()), "amf").plan(self.cap("720p"))
        self.assertEqual(plan.scaling, "native")

    def test_probe_result_is_cached(self):
        ff = FakeFFmpeg(working={"scale_d3d11"})
        pipe = VideoPipeline(ff, "nvenc")
        pipe.plan(self.cap("1080p"))
        pipe.plan(self.cap("1080p"))
        self.assertEqual(len(ff.probed), 1)

    def test_ultrawide_keeps_aspect(self):
        plan = VideoPipeline(FakeFFmpeg(screen=(3440, 1440), working={"scale_d3d11"}), "nvenc").plan(self.cap("1080p"))
        self.assertEqual(plan.size, (2580, 1080))

    def test_unknown_screen_assumes_16_9(self):
        plan = VideoPipeline(FakeFFmpeg(screen=None, working={"scale_d3d11"}), "nvenc").plan(self.cap("720p"))
        self.assertEqual(plan.size, (1280, 720))

    def test_video_args_use_the_plan(self):
        ff = FakeFFmpeg(working={"scale_d3d11"})
        args = VideoPipeline(ff, "nvenc").video_args(CaptureSettings(resolution="1080p", rate_control="bitrate"))
        self.assertIn("scale_d3d11=1920:1080", args)
        self.assertIn("-b:v", args)


class GameSettingsTests(TempDirTest):
    def test_games_roundtrip(self):
        path = self.tmp / "config.json"
        cfg = AppConfig()
        cfg.game("league").enabled = False
        cfg.save(path)
        self.assertFalse(json.loads(path.read_text())["games"]["league"]["enabled"])
        self.assertFalse(AppConfig.load(path).game("league").enabled)

    def test_old_config_without_games_section(self):
        path = self.tmp / "config.json"
        path.write_text(json.dumps({"capture": {"fps": 30, "quality": 23}}))
        cfg = AppConfig.load(path)
        self.assertTrue(cfg.game("league").enabled)
        self.assertEqual((cfg.capture.fps, cfg.capture.resolution), (30, "1080p"))

    def test_disabled_game_is_not_recorded_but_running_match_finishes(self):
        state = {"on": False}
        sim = MatchSimulator(self.tmp)
        sim.watcher.enabled = lambda *_: state["on"]
        self.assertEqual(sim.run(seconds=60).recorder.stops, [])
        # turned on mid-way through a later match: records it; turning off mid-match doesn't cut it
        sim2 = MatchSimulator(self.tmp)
        sim2.watcher.enabled = lambda *_: state["on"]
        state["on"] = True

        def switch_off_at_60(s):
            if s.watcher.client.game_time > 60:
                state["on"] = False
        self.assertEqual(len(sim2.run(during=switch_off_at_60).recorder.stops), 1)

    def test_catalogue(self):
        games = GameRegistry.all()
        support = [g.support for g in games]
        self.assertEqual(support, sorted(support, key=["highlights", "auto", "planned"].index))
        self.assertEqual({g.id for g in games if g.support == "highlights"}, {"league", "cs2"})
        self.assertEqual({g.id for g in games if g.support == "auto"},
                         {"tft", "apex", "valorant", "deadlock", "marvel_rivals", "repo"})
        self.assertTrue(all(g.processes for g in GameRegistry.supported()))
        self.assertEqual(GameRegistry.get("cs2").initials, "CS")
        self.assertEqual(GameRegistry.REPO.initials, "RE")
        self.assertEqual(GameRegistry.LEAGUE.initials, "LL")

    def test_hotkey_text(self):
        from Core.formatting import Format
        self.assertEqual(Format.hotkey("<ctrl>+<alt>+r"), "Ctrl+Alt+R")
