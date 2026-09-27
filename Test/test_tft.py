"""TFT: the TFT client is recorded as a session, then Riot's TFT match history cuts it into one video per
match with placement, Win (top 4) / Loss and the round you went out (no internet needed)."""
import subprocess
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from fakes import FakeClock, FakeProcesses, FakeRecorder
from helpers import MatchSimulator, TempDirTest
from scenario import Scenario

from Capture.ffmpeg import FFmpeg
from Core.config import AutoSettings, GameSettings
from Core.sidecar import Sidecar
from Games.match_enricher import EnrichJob
from Games.registry import GameRegistry
from Games.tft import TftApi, TftApiError, TftEnricher, TftMatch, TftRounds, TftWatcher

FF = FFmpeg.locate()
ME = "puuid-me"
T0 = 1_760_000_000.0   # unix time the session recording started


def match(match_id="EUW1_1", start=T0 + 60, eliminated=40.0, placement=3, win=True, last_round=19, queue=1100):
    return {"metadata": {"match_id": match_id, "participants": [ME, "other"]},
            "info": {"gameCreation": int(start * 1000), "game_datetime": int((start + 50) * 1000),
                     "game_length": 50.0, "queue_id": queue, "participants": [
                         {"puuid": "other", "placement": 1, "win": True, "last_round": 30},
                         {"puuid": ME, "placement": placement, "win": win, "last_round": last_round,
                          "time_eliminated": eliminated, "level": 8, "players_eliminated": 2,
                          "total_damage_to_players": 120}]}}


class FakeApi:
    def __init__(self, matches=(), fail=None):
        self.matches, self.fail = {m["metadata"]["match_id"]: m for m in matches}, fail
        self.asked = []

    def puuid(self, riot_id):
        if self.fail:
            raise self.fail
        self.asked.append(riot_id)
        return ME

    def match_ids(self, _puuid, _start, _end):
        return list(self.matches)

    def match(self, match_id):
        return self.matches.get(match_id)


class RoundTests(unittest.TestCase):
    def test_round_counter_to_stage(self):
        self.assertEqual([TftRounds.stage(r) for r in (1, 4, 5, 11, 12, 19, 0)],
                         ["1-1", "1-4", "2-1", "2-7", "3-1", "4-1", "?"])   # Riot: out in 2-1 = last_round 5

    def test_placements(self):
        self.assertEqual([TftRounds.ordinal(n) for n in (1, 2, 3, 4, 8, 11)], ["1st", "2nd", "3rd", "4th", "8th", "11th"])


class MatchDataTests(unittest.TestCase):
    def test_top_four_is_a_win_not_an_elimination(self):
        m = TftMatch.from_api(match(placement=4, win=True, last_round=26), ME)
        self.assertEqual((m.result, m.place, m.stage, m.mode), ("Win", "4th", "5-1", "Ranked"))
        self.assertEqual((m.start_time, m.end_time), (T0 + 60, T0 + 100))

    def test_bottom_four_is_a_loss(self):
        m = TftMatch.from_api(match(placement=7, win=False, last_round=12), ME)
        self.assertEqual((m.result, m.place, m.stage), ("Loss", "7th", "3-1"))

    def test_missing_win_flag_uses_placement(self):
        data = match(placement=2)
        del data["info"]["participants"][1]["win"]
        self.assertTrue(TftMatch.from_api(data, ME).won)

    def test_not_in_the_match_or_garbled(self):
        self.assertIsNone(TftMatch.from_api(match(), "someone-else"))
        self.assertIsNone(TftMatch.from_api({"unexpected": "shape"}, ME))


class ApiTests(unittest.TestCase):
    def test_no_key_is_a_clear_error(self):
        with self.assertRaises(TftApiError):
            TftApi(lambda: {}).puuid("Me#EUW")

    def test_riot_id_needs_a_tag(self):
        with self.assertRaises(TftApiError):
            TftApi(lambda: {"api_key": "RGAPI-x"}).puuid("NoTag")

    def test_requests_use_the_region_and_key(self):
        seen = []

        class Resp:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self.body

        def fake_open(req, timeout):
            seen.append((req.full_url, req.get_header("X-riot-token")))
            return Resp(b'{"puuid": "p1"}' if "accounts" in req.full_url else b'["SG2_1"]')
        api = TftApi(lambda: {"api_key": "RGAPI-x", "region": "sea"})
        with patch("Games.tft.urllib.request.urlopen", fake_open):
            self.assertEqual(api.puuid("Me Me#SG2"), "p1")
            self.assertEqual(api.match_ids("p1", T0, T0 + 10), ["SG2_1"])
        self.assertTrue(seen[0][0].startswith("https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Me%20Me/SG2"))
        self.assertTrue(seen[1][0].startswith("https://sea.api.riotgames.com/tft/match/v1/matches/by-puuid/p1/ids?"))
        self.assertEqual({key for _, key in seen}, {"RGAPI-x"})

    def test_rejected_key(self):
        def denied(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        with patch("Games.tft.urllib.request.urlopen", denied), self.assertRaises(TftApiError):
            TftApi(lambda: {"api_key": "RGAPI-old"}).puuid("Me#NA1")


class EnricherTests(TempDirTest):
    def enricher(self, api, ffmpeg=None, split=True, now=T0 + 10_000):
        gs = GameSettings(options={"split": split})
        return TftEnricher(self.tmp, ffmpeg, lambda: gs, lambda p: Path(p).unlink(missing_ok=True),
                           api=api, clock=lambda: now)

    def session(self, video=None) -> Path:
        video = video or self.tmp / "TFT_session.mp4"
        if not video.exists():
            video.write_bytes(b"x")
        Sidecar.write(video, Sidecar.build(video.name, "tft", {"title": "Teamfight Tactics"},
                                           [{"type": "bookmark", "label": "Bookmark 1", "involves_me": True,
                                             "importance": 2, "video_time": 70.0, "event": {}}]))
        return video

    def test_offline_bad_key_or_not_yet_listed_is_retried(self):
        job = EnrichJob(str(self.session()), "Me#EUW", T0, T0 + 200)
        self.assertEqual(self.enricher(FakeApi(fail=OSError("offline"))).process(job), "retry")
        self.assertEqual(self.enricher(FakeApi(fail=TftApiError("key expired"))).process(job), "retry")
        self.assertEqual(self.enricher(FakeApi()).process(job), "retry")

    def test_queue_survives_a_restart(self):
        self.enricher(FakeApi()).enqueue(self.tmp / "a.mp4", "Me#EUW", T0, T0 + 100)
        self.assertEqual(self.enricher(FakeApi()).jobs[0].account_id, "Me#EUW")

    def test_no_split_adds_the_matches_to_the_session(self):
        video = self.session()
        e = self.enricher(FakeApi([match(), match("EUW1_2", start=T0 + 110, eliminated=60, placement=6,
                                                  win=False, last_round=15)]), split=False)
        with patch.object(e, "ffmpeg", type("F", (), {"duration": staticmethod(lambda _v: 200.0)})()):
            self.assertEqual(e.process(EnrichJob(str(video), "Me#EUW", T0, T0 + 200)), "done")
        data = Sidecar.read(video)
        self.assertEqual((data["game"]["matches"], data["game"]["result"]), (2, "1W 1L (3rd, 6th)"))
        ends = [m["label"] for m in data["markers"] if m["type"] == "game_end"]
        self.assertEqual(ends, ["3rd place (Win) - out in round 4-1", "6th place (Loss) - out in round 3-4"])
        self.assertIn("bookmark", [m["type"] for m in data["markers"]])

    @unittest.skipUnless(FF, "ffmpeg not found")
    def test_session_is_cut_into_a_match_video(self):
        video = self.tmp / "TFT_session.mp4"
        subprocess.run([FF.exe, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                        "-t", "200", "-c:v", "libx264", "-g", "10", str(video)], check=True)
        self.session(video)
        e = self.enricher(FakeApi([match(placement=1, last_round=33)]), ffmpeg=FF)
        self.assertEqual(e.process(EnrichJob(str(video), "Me#EUW", T0, T0 + 200)), "done")
        made = sorted(self.tmp.glob("TFT_*_1st_Win.mp4"))
        self.assertEqual(len(made), 1)
        self.assertFalse(video.exists(), "the session is replaced by its match videos")
        data = Sidecar.read(made[0])
        self.assertEqual((data["game_id"], data["game"]["result"], data["game"]["last_round"]),
                         ("tft", "Win - 1st", "6-1"))
        self.assertIn("1st place - you won the lobby", [m["label"] for m in data["markers"]])


class WatcherTests(TempDirTest):
    def test_new_tft_client_is_detected(self):
        self.assertIn("TFTClient-Win64-Shipping.exe", GameRegistry.TFT.processes)
        self.assertIn(GameRegistry.TFT, GameRegistry.session_games())

    def test_saved_session_is_queued_for_lookup(self):
        clock, procs = FakeClock(), FakeProcesses()
        rec = FakeRecorder(self.tmp, clock)
        queued = []

        class Enricher:
            def enqueue(self, *args):
                queued.append(args)
        w = TftWatcher(AutoSettings(), rec, monitor=procs, enricher=Enricher(), riot_id=lambda: " Me#EUW ")
        with patch("Games.session_watcher.time.monotonic", clock):
            procs.up = True
            w.tick()
            procs.up = False
            for _ in range(20):
                clock.advance(1)
                w.tick()
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0][:2], (rec.stops[0]["path"], "Me#EUW"))

    def test_without_a_riot_id_the_session_is_kept(self):
        TftWatcher(AutoSettings(), None, enricher=object(), riot_id=lambda: "").after_save(Path("x.mp4"), T0, T0 + 1)

    def test_league_watcher_leaves_a_tft_client_session_alone(self):
        s = Scenario.quick()
        s.mode = "TFT"
        sim = MatchSimulator(self.tmp, scenario=s)
        sim.recorder.start()                      # the TFT client's session recording
        owner = sim.recorder.current_id
        sim.run()
        self.assertEqual(sim.recorder.stops, [], "League's watcher must not stop the TFT session")
        self.assertEqual(sim.recorder.current_id, owner)


if __name__ == "__main__":
    unittest.main()
