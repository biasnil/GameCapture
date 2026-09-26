"""Session mode (League open -> closed) and Highlights mode (keep only highlights)."""
import subprocess
import unittest

from fakes import FakeProcesses
from helpers import MatchSimulator, TempDirTest

from Capture.ffmpeg import FFmpeg
from Core.condense import HighlightCondenser
from Core.config import ClipSettings
from Core.sidecar import Sidecar


class SessionModeTests(TempDirTest):
    def make(self, league_open=True, **kw):
        sim = MatchSimulator(self.tmp, delay=30, **kw)       # first match starts 30 s after League opens
        sim.watcher.mode = lambda: "session"
        sim.watcher.processes = FakeProcesses(up=league_open)
        return sim

    def test_records_from_open_to_close_with_every_match(self):
        sim = self.make()

        def play(s):
            t = s.watcher.client.game_time
            if 200 < t < 202:                               # first match over: queue a second one
                s.watcher.client.start = s.clock() + 20
            run = s.watcher.session_run
            if (run is not None and len(run.matches) == 2 and s.watcher.session is None
                    and s.watcher.client.game_stats() is None):
                s.watcher.processes.up = False              # close League after the second match
        sim.run(seconds=600, during=play)
        stops = sim.recorder.stops
        self.assertEqual(len(stops), 1, "one video for the whole session")
        self.assertEqual(stops[0]["tag"], "Session_2games")
        data = Sidecar.read(stops[0]["path"])
        self.assertEqual((data["game"]["title"], data["game"]["result"]), ("League session", "2W 0L"))
        self.assertEqual(len(data["game"]["matches"]), 2)
        self.assertEqual((data["game"]["kills"], data["game"]["deaths"], data["game"]["assists"]), (6, 2, 2))
        # the recording started before the match, so video time = game time + ~30 s for match 1
        first_kill = next(m for m in data["markers"] if m["label"] == "Killed Enemy1" and m["match"] == 1)
        self.assertAlmostEqual(first_kill["video_time"], first_kill["game_time"] + 30, delta=1.5)
        second = [m for m in data["markers"] if m["match"] == 2]
        self.assertTrue(second and min(m["video_time"] for m in second) > 200)
        self.assertTrue(any(m["label"] == "Match 2 start - Ahri" for m in data["markers"]))

    def test_starts_as_soon_as_league_opens(self):
        sim = self.make()
        sim.run(seconds=5)
        self.assertTrue(sim.recorder.is_recording)
        self.assertIsNone(sim.watcher.session, "no match yet - just the client")

    def test_nothing_while_league_is_closed(self):
        sim = self.make(league_open=False)
        sim.watcher.client.start = sim.clock() + 10_000     # no match either
        self.assertFalse(sim.run(seconds=60).recorder.is_recording)

    def test_manual_stop_is_respected_until_league_closes(self):
        sim = self.make()
        state = {"stopped": False}

        def stop_once(s):
            if s.recorder.is_recording and not state["stopped"] and s.clock() > 1010:
                s.recorder.stop()
                state["stopped"] = True
        sim.run(seconds=120, during=stop_once)
        self.assertEqual(len(sim.recorder.stops), 1)
        self.assertFalse(sim.recorder.is_recording, "must not restart while League is still open")


class FakeCondenser:
    def __init__(self, tmp, result="video"):
        self.tmp, self.result, self.calls = tmp, result, []

    def condense(self, video, meta):
        self.calls.append((video, meta))
        if self.result == "error":
            raise RuntimeError("ffmpeg broke")
        if self.result == "none":
            return None
        out = video.with_name(video.stem + "_highlights.mp4")
        out.write_bytes(b"short")
        return out


class HighlightsModeTests(TempDirTest):
    def run_match(self, result="video"):
        sim = MatchSimulator(self.tmp)
        sim.watcher.mode = lambda: "highlights"
        sim.watcher.condenser = FakeCondenser(self.tmp, result)
        return sim.run()

    def test_full_recording_replaced_by_highlights(self):
        sim = self.run_match()
        stop = sim.recorder.stops[0]
        self.assertTrue(stop["hold"], "full video stays hidden while it's being condensed")
        self.assertEqual(len(sim.watcher.condenser.calls), 1)
        self.assertFalse(stop["path"].exists(), "full recording deleted")
        self.assertFalse(Sidecar.path_for(stop["path"]).exists())
        self.assertEqual([p.name for p in self.tmp.glob("*.mp4")], [stop["path"].stem + "_highlights.mp4"])
        self.assertEqual(sim.recorder.held, set())

    def test_no_highlights_keeps_nothing(self):
        sim = self.run_match("none")
        self.assertEqual(list(self.tmp.glob("*.mp4")), [])

    def test_failure_keeps_full_recording(self):
        sim = self.run_match("error")
        self.assertTrue(sim.recorder.stops[0]["path"].exists())
        self.assertEqual(sim.recorder.held, set(), "released so the library shows it")


class CondenserTests(TempDirTest):
    def test_segments_and_remap(self):
        c = HighlightCondenser(None, ClipSettings(pre_seconds=5, post_seconds=5))
        markers = [{"type": "kill", "involves_me": True, "label": "K1", "video_time": 20},
                   {"type": "death", "involves_me": True, "label": "D", "video_time": 30},
                   {"type": "kill", "involves_me": True, "label": "K2", "video_time": 60},
                   {"type": "other_kill", "involves_me": False, "label": "X", "video_time": 62}]
        segs = c.segments(markers)
        self.assertEqual([(s.start, s.end) for s in segs], [(15, 25), (55, 65)])   # death isn't a highlight
        moved = {m["label"]: m["video_time"] for m in c.remap(markers, segs)}
        self.assertEqual(moved, {"K1": 5, "K2": 15, "X": 17})

    @unittest.skipUnless(FFmpeg.locate(), "ffmpeg not found")
    def test_real_condense(self):
        ff = FFmpeg.locate()
        video = self.tmp / "match.mp4"
        subprocess.run([ff.exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=160x90:rate=10", "-f", "lavfi", "-i", "sine", "-t", "90",
                        "-c:v", "libx264", "-preset", "ultrafast", "-g", "20", "-c:a", "aac", str(video)], check=True)
        meta = Sidecar.build(video.name, "league", {"champion": "Ahri", "result": "Win"}, [
            {"type": "kill", "involves_me": True, "label": "K1", "video_time": 20, "importance": 2},
            {"type": "multikill", "involves_me": True, "label": "Double kill", "video_time": 70, "importance": 3}])
        out = HighlightCondenser(ff, ClipSettings(pre_seconds=5, post_seconds=5)).condense(video, meta)
        info = ff.run(["-i", str(out)]).stderr
        h, m, s = info.split("Duration: ")[1].split(",")[0].split(":")
        self.assertAlmostEqual(int(m) * 60 + float(s), 20, delta=2.5)
        data = Sidecar.read(out)
        self.assertTrue(data["game"]["highlights_only"])
        self.assertEqual([m["video_time"] for m in data["markers"]], [5, 15])
