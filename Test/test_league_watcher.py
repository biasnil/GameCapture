from helpers import MatchSimulator, TempDirTest
from scenario import Scenario

from Core.config import AutoSettings
from Core.sidecar import Sidecar


class FullMatchTests(TempDirTest):
    def setUp(self):
        super().setUp()
        sim = MatchSimulator(self.tmp).run()
        self.watcher, self.rec = sim.watcher, sim.recorder
        self.stop = self.rec.stops[0]
        self.data = Sidecar.read(self.stop["path"])
        self.markers = self.data["markers"]

    def by_label(self, text):
        return [m for m in self.markers if text in m["label"]]

    def test_records_exactly_once_and_names_file(self):
        self.assertEqual(len(self.rec.stops), 1)
        self.assertEqual(self.stop["tag"], "Ahri_Win_3-1-1")
        self.assertIsNone(self.watcher.session)

    def test_sidecar_is_versioned(self):
        self.assertEqual(self.data["schema"], Sidecar.SCHEMA_VERSION)
        self.assertEqual(self.data["game_id"], "league")
        self.assertEqual(self.data["game"]["result"], "Win")

    def test_marker_types(self):
        types = {m["type"] for m in self.markers if m["involves_me"]}
        for t in ("kill", "multikill", "death", "assist", "objective", "structure", "ace", "first_blood"):
            self.assertIn(t, types)
        self.assertEqual(self.by_label("Double kill")[0]["event"]["KillStreak"], 2)
        baron = self.by_label("Baron")[0]
        self.assertIn("STOLEN", baron["label"])
        self.assertEqual(baron["importance"], 3)
        self.assertTrue(self.by_label("Fire Dragon")[0]["involves_me"])
        self.assertFalse(self.by_label("Enemy1 killed Ally1")[0]["involves_me"])

    def test_video_times_line_up_with_game_time(self):
        for m in self.markers:
            self.assertAlmostEqual(m["video_time"], m["game_time"], delta=1.5)

    def test_post_roll(self):
        self.assertGreaterEqual(self.stop["duration"], 150 + 5)
        self.assertLess(self.stop["duration"], 150 + 5 + 3)

    def test_chapters_merge_same_moment(self):
        titles = [t for _, t in self.stop["chapters"]]
        self.assertTrue(any("Killed Enemy3" in t and "Double kill" in t for t in titles), titles)
        times = [t for t, _ in self.stop["chapters"]]
        self.assertEqual(times, sorted(times))


class EdgeCaseTests(TempDirTest):
    def test_skipped_mode_is_not_recorded(self):
        s = Scenario.quick()
        s.mode = "PRACTICETOOL"
        self.assertEqual(MatchSimulator(self.tmp, scenario=s).run().recorder.stops, [])

    def test_crash_without_gameend_saves_after_grace(self):
        stops = MatchSimulator(self.tmp, crash_at=60).run().recorder.stops
        self.assertEqual(len(stops), 1)
        self.assertTrue(stops[0]["tag"].startswith("Ahri_Unfinished_"))
        self.assertGreaterEqual(stops[0]["duration"], 60 + 15)

    def test_spectator_is_not_recorded(self):
        sim = MatchSimulator(self.tmp)
        sim.watcher.client.active_player_name = lambda: None
        self.assertEqual(sim.run().recorder.stops, [])

    def test_manual_stop_mid_match_is_respected(self):
        def stop_at_50(sim):
            if sim.recorder.is_recording and 50 <= sim.watcher.client.game_time < 51:
                sim.recorder.stop()
        stops = MatchSimulator(self.tmp).run(during=stop_at_50).recorder.stops
        self.assertEqual(len(stops), 1, "watcher must not restart or re-finalize")
        self.assertIsNone(stops[0]["tag"])
        self.assertFalse(list(self.tmp.glob("*.json")))

    def test_started_mid_match_drops_earlier_events(self):
        stops = MatchSimulator(self.tmp, delay=-100).run().recorder.stops  # app launched 100 s in
        markers = Sidecar.read(stops[0]["path"])["markers"]
        self.assertTrue(all(m["game_time"] >= 99 for m in markers))
        self.assertTrue(any("Baron" in m["label"] for m in markers))

    def test_end_screen_does_not_start_a_second_recording(self):
        self.assertEqual(len(MatchSimulator(self.tmp, linger=120).run().recorder.stops), 1)

    def test_back_to_back_matches(self):
        def next_match(sim):
            if sim.watcher.client.game_time > 200:   # first game closed; queue into a new one
                sim.watcher.client.start = sim.clock() + 10
        stops = MatchSimulator(self.tmp).run(during=next_match).recorder.stops
        self.assertEqual(len(stops), 2)
        self.assertTrue(all(s["tag"] == "Ahri_Win_3-1-1" for s in stops))

    def test_rename_off(self):
        sim = MatchSimulator(self.tmp, settings=AutoSettings(rename_with_result=False, post_roll_seconds=5)).run()
        self.assertIsNone(sim.recorder.stops[0]["tag"])
