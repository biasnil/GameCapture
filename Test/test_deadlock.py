"""Deadlock: match lookup after a session, per-match videos and kill/death markers (no internet needed)."""
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fakes import FakeClock, FakeProcesses, FakeRecorder
from helpers import TempDirTest

from Capture.ffmpeg import FFmpeg
from Core.config import AutoSettings, GameSettings
from Core.sidecar import Sidecar
from Games.deadlock import DeadlockEnricher, DeadlockMatch, DeadlockTimeline, DeadlockWatcher, EnrichJob

FF = FFmpeg.locate()
ME = 1234
T0 = 1_760_000_000.0   # unix time the session recording started


def row(match_id=1, start=T0 + 60, duration=40, won=True, k=3, d=1, a=2):
    return {"match_id": match_id, "start_time": start, "match_duration_s": duration, "hero_id": 7,
            "player_kills": k, "player_deaths": d, "player_assists": a, "match_mode": 1,
            "player_team": 0, "match_result": 0 if won else 1}


class FakeApi:
    def __init__(self, history=None, meta=None, fail=False):
        self.history, self.meta, self.fail = history or [], meta, fail

    def match_history(self, _account):
        if self.fail:
            raise OSError("offline")
        return self.history

    def metadata(self, _match_id):
        return self.meta

    def hero_name(self, _hero_id):
        return "Haze"


class MatchDataTests(unittest.TestCase):
    def test_history_row(self):
        m = DeadlockMatch.from_history(row(won=False))
        self.assertEqual((m.result, m.mode, m.kills, m.end_time), ("Lose", "Unranked", 3, T0 + 100))
        self.assertIsNone(DeadlockMatch.from_history({"no": "id"}))

    def test_kills_and_deaths_from_metadata(self):
        meta = {"match_info": {"players": [
            {"account_id": ME, "player_slot": 1, "death_details": [{"game_time_s": 50, "killer_player_slot": 2}]},
            {"account_id": 9, "player_slot": 2, "death_details": [{"game_time_s": 10, "killer_player_slot": 1},
                                                                 {"game_time_s": 14, "killer_player_slot": 1}]},
        ]}}
        kills, deaths = DeadlockTimeline.parse(meta, ME)
        self.assertEqual((kills, deaths), ([10.0, 14.0], [50.0]))
        markers = DeadlockTimeline.markers(kills, deaths, lambda t: t + 100)
        self.assertEqual([m["type"] for m in markers], ["kill", "multikill", "kill", "death"])
        self.assertEqual(markers[1]["label"], "Double kill")
        self.assertEqual(DeadlockTimeline.parse({"unexpected": "shape"}, ME), ([], []))


class EnricherTests(TempDirTest):
    def enricher(self, api, ffmpeg=None, split=True, now=T0 + 10_000):
        gs = GameSettings(options={"split": split})
        return DeadlockEnricher(self.tmp, ffmpeg, lambda: gs, lambda p: Path(p).unlink(missing_ok=True),
                                api=api, clock=lambda: now)

    def session(self) -> Path:
        video = self.tmp / "DL_session.mp4"
        video.write_bytes(b"x")
        Sidecar.write(video, Sidecar.build(video.name, "deadlock", {"title": "Deadlock"},
                                           [{"type": "bookmark", "label": "Bookmark 1", "involves_me": True,
                                             "importance": 2, "video_time": 70.0, "event": {}}]))
        return video

    def test_offline_or_not_yet_listed_is_retried(self):
        video = self.session()
        job = EnrichJob(str(video), ME, T0, T0 + 200)
        self.assertEqual(self.enricher(FakeApi(fail=True)).process(job), "retry")
        self.assertEqual(self.enricher(FakeApi(history=[])).process(job), "retry")

    def test_queue_survives_a_restart(self):
        e = self.enricher(FakeApi())
        e.enqueue(self.tmp / "a.mp4", ME, T0, T0 + 100)
        self.assertEqual(len(self.enricher(FakeApi()).jobs), 1)

    def test_no_split_adds_the_matches_to_the_session(self):
        video = self.session()
        e = self.enricher(FakeApi(history=[row()]), split=False)
        with patch.object(e, "ffmpeg", type("F", (), {"duration": staticmethod(lambda _v: 200.0)})()):
            self.assertEqual(e.process(EnrichJob(str(video), ME, T0, T0 + 200)), "done")
        data = Sidecar.read(video)
        self.assertEqual((data["game"]["matches"], data["game"]["result"]), (1, "1W 0L"))
        self.assertIn("bookmark", [m["type"] for m in data["markers"]])

    @unittest.skipUnless(FF, "ffmpeg not found")
    def test_session_is_cut_into_a_match_video(self):
        video = self.tmp / "DL_session.mp4"
        subprocess.run([FF.exe, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                        "-t", "200", "-c:v", "libx264", "-g", "10", str(video)], check=True)
        Sidecar.write(video, Sidecar.build(video.name, "deadlock", {"title": "Deadlock"},
                                           [{"type": "bookmark", "label": "Bookmark 1", "involves_me": True,
                                             "importance": 2, "video_time": 70.0, "event": {}}]))
        e = self.enricher(FakeApi(history=[row()]), ffmpeg=FF)
        self.assertEqual(e.process(EnrichJob(str(video), ME, T0, T0 + 200)), "done")
        made = sorted(self.tmp.glob("DL_*_Haze_Win_3-1-2.mp4"))
        self.assertEqual(len(made), 1)
        self.assertFalse(video.exists(), "the session is replaced by its match videos")
        data = Sidecar.read(made[0])
        self.assertEqual(data["game"]["title"], "Haze")
        bookmark = next(m for m in data["markers"] if m["type"] == "bookmark")
        self.assertAlmostEqual(bookmark["video_time"], 70 - 50, delta=0.01)   # match starts at 60, 10 s lead-in


class WatcherTests(TempDirTest):
    def test_saved_session_is_queued_for_lookup(self):
        clock, procs = FakeClock(), FakeProcesses()
        rec = FakeRecorder(self.tmp, clock)
        queued = []

        class Enricher:
            def enqueue(self, *args):
                queued.append(args)
        w = DeadlockWatcher(AutoSettings(), rec, monitor=procs, enricher=Enricher(), account=lambda: ME)
        with patch("Games.session_watcher.time.monotonic", clock):
            procs.up = True
            w.tick()
            procs.up = False
            for _ in range(20):
                clock.advance(1)
                w.tick()
        self.assertEqual(len(queued), 1)
        path, account, start, end = queued[0]
        self.assertEqual((path, account), (rec.stops[0]["path"], ME))
        self.assertLessEqual(start, end)

    def test_unknown_steam_account_keeps_the_session(self):
        w = DeadlockWatcher(AutoSettings(), None, enricher=object(), account=lambda: None)
        w.after_save(Path("x.mp4"), T0, T0 + 1)   # just logs; nothing to queue


if __name__ == "__main__":
    unittest.main()
