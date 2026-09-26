"""LeagueLiveClient talking HTTP to the fake League server (Test/fake_league.py)."""
import time
import unittest

from fake_league import FakeLeagueServer

from Games.league_client import LeagueLiveClient


class LeagueClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeLeagueServer(port=0, delay=0).start()
        cls.client = LeagueLiveClient(base=cls.server.base_url)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_no_match_returns_none(self):
        self.server.delay = 999
        try:
            self.assertIsNone(self.client.game_stats())
            self.assertIsNone(self.client.active_player_name())
            self.assertEqual(self.client.events(), [])
        finally:
            self.server.delay = 0

    def test_in_match(self):
        self.server.started = time.monotonic() - 20  # 20 s into the match
        stats = self.client.game_stats()
        self.assertEqual(stats["gameMode"], "CLASSIC")
        self.assertAlmostEqual(stats["gameTime"], 20, delta=1)
        self.assertEqual(self.client.active_player_name(), "Tester#DEV")
        self.assertIn("FirstBlood", [e["EventName"] for e in self.client.events()])
        self.assertEqual(self.client.player_list()[0]["scores"]["kills"], 1)

    def test_nothing_listening(self):
        self.assertIsNone(LeagueLiveClient(base="http://127.0.0.1:9/liveclientdata/", timeout=0.3).game_stats())
