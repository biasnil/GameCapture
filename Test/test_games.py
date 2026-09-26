"""New games: TFT (League client), CS2 (Game State Integration) and process-detected sessions
(Apex, Valorant, Deadlock, Marvel Rivals, R.E.P.O.), plus the Bookmark hotkey."""
import json
import urllib.request
from unittest.mock import patch

from fakes import FakeClock, FakeProcesses, FakeRecorder
from helpers import MatchSimulator, TempDirTest
from scenario import Scenario

from Core.config import AutoSettings
from Core.sidecar import Sidecar
from Games.cs2 import CS2Installer, CS2Watcher, GsiServer
from Games.registry import GameRegistry
from Games.session_watcher import ProcessSessionWatcher

ME = "76561198000000001"


class TftTests(TempDirTest):
    def test_tft_match_is_its_own_game(self):
        s = Scenario.quick()
        s.mode = "TFT"
        rec = MatchSimulator(self.tmp, scenario=s).run().recorder
        data = Sidecar.read(rec.stops[0]["path"])
        self.assertEqual((data["game_id"], data["game"]["title"]), ("tft", "Teamfight Tactics"))
        self.assertEqual(rec.stops[0]["tag"], "TFT_Win")
        self.assertEqual(rec.prefix, "TFT")

    def test_tft_can_be_off_while_league_is_on(self):
        s = Scenario.quick()
        s.mode = "TFT"
        sim = MatchSimulator(self.tmp, scenario=s)
        sim.watcher.enabled = lambda game_id: game_id == "league"
        self.assertEqual(sim.run().recorder.stops, [])


class SessionGameTests(TempDirTest):
    def make(self, game=GameRegistry.APEX):
        clock = FakeClock()
        rec = FakeRecorder(self.tmp, clock)
        procs = FakeProcesses()
        w = ProcessSessionWatcher(game, AutoSettings(), rec, monitor=procs)
        return w, rec, procs, clock

    def run_for(self, w, clock, seconds, during=None):
        with patch("Games.session_watcher.time.monotonic", clock):
            for _ in range(int(seconds)):
                w.tick()
                if during:
                    during()
                clock.advance(1)

    def test_records_while_the_game_is_open(self):
        w, rec, procs, clock = self.make()
        self.run_for(w, clock, 5)
        self.assertFalse(rec.is_recording, "nothing while the game is closed")
        procs.up = True
        self.run_for(w, clock, 60, during=lambda: rec.bookmark() if int(clock()) == 1030 else None)
        self.assertTrue(rec.is_recording)
        self.assertEqual(rec.prefix, "Apex")
        procs.up = False
        self.run_for(w, clock, 30)
        self.assertEqual(len(rec.stops), 1)
        data = Sidecar.read(rec.stops[0]["path"])
        self.assertEqual((data["game_id"], data["game"]["title"]), ("apex", "Apex Legends"))
        self.assertEqual([m["type"] for m in data["markers"]], ["bookmark"])

    def test_short_crash_does_not_split_the_session(self):
        w, rec, procs, clock = self.make(GameRegistry.VALORANT)
        procs.up = True
        self.run_for(w, clock, 30)
        procs.up = False
        self.run_for(w, clock, 5)          # restart within the grace period
        procs.up = True
        self.run_for(w, clock, 30)
        self.assertEqual(rec.stops, [])
        self.assertTrue(rec.is_recording)

    def test_disabled_and_manual_stop(self):
        w, rec, procs, clock = self.make(GameRegistry.DEADLOCK)
        w.enabled = lambda gid: False
        procs.up = True
        self.run_for(w, clock, 10)
        self.assertFalse(rec.is_recording)
        w.enabled = lambda gid: True
        self.run_for(w, clock, 3)
        rec.stop()                          # you pressed Stop
        self.run_for(w, clock, 30)
        self.assertFalse(rec.is_recording, "stays stopped until the game is closed")

    def test_every_session_game_has_process_names(self):
        for game in GameRegistry.SESSION_GAMES:
            self.assertTrue(game.processes and all(p.endswith(".exe") for p in game.processes), game.id)


def payload(phase="live", kills=0, deaths=0, assists=0, mvps=0, round_kills=0, hs=0, ct=0, t=0,
            steamid=ME, rnd=1, team="CT", map_name="de_mirage"):
    return {"provider": {"steamid": ME}, "auth": {"token": "tok"},
            "map": {"name": map_name, "mode": "competitive", "phase": phase, "round": rnd,
                    "team_ct": {"score": ct}, "team_t": {"score": t}},
            "player": {"steamid": steamid, "team": team,
                       "state": {"round_kills": round_kills, "round_killhs": hs},
                       "match_stats": {"kills": kills, "deaths": deaths, "assists": assists, "mvps": mvps}}}


class CS2Tests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.rec = FakeRecorder(self.tmp, self.clock)
        self.w = CS2Watcher(AutoSettings(post_roll_seconds=5), self.rec)

    def feed(self, *payloads, gap=5):
        with patch("Games.cs2.time.monotonic", self.clock):
            for p in payloads:
                self.w.handle(p)
                self.w.tick()
                self.clock.advance(gap)

    def settle(self, seconds=10):
        with patch("Games.cs2.time.monotonic", self.clock):
            for _ in range(seconds):
                self.w.tick()
                self.clock.advance(1)

    def test_full_match(self):
        self.feed(payload("warmup"),
                  payload(rnd=1),
                  payload(kills=1, round_kills=1, hs=1, rnd=1),                    # headshot
                  payload(kills=2, round_kills=2, hs=1, rnd=1),                    # double
                  payload(kills=3, round_kills=3, hs=2, mvps=1, ct=1, rnd=1),      # -> triple (same marker), MVP
                  payload(kills=3, deaths=1, mvps=1, rnd=2, ct=1),                 # died
                  payload(kills=9, deaths=5, assists=2, steamid="someone-else", rnd=2),  # spectating: ignore
                  payload(kills=3, deaths=1, assists=1, mvps=1, rnd=3, ct=1, t=1),  # assist
                  payload("gameover", kills=3, deaths=1, assists=1, mvps=1, ct=13, t=9, rnd=22))
        self.settle()
        stop = self.rec.stops[0]
        self.assertEqual(self.rec.prefix, "CS2")
        self.assertEqual(stop["tag"], "Mirage_Win_3-1-1")
        data = Sidecar.read(stop["path"])
        self.assertEqual(data["game_id"], "cs2")
        self.assertEqual((data["game"]["title"], data["game"]["score"], data["game"]["mvps"]), ("Mirage", "13-9", 1))
        labels = [m["label"] for m in data["markers"] if m["involves_me"]]
        # the multikill marker stays where the Double kill happened and is upgraded to Triple
        self.assertEqual(labels, ["Headshot kill", "Kill", "Triple kill", "Headshot kill", "Round MVP", "Died",
                                  "Assist"])

    def test_ace_upgrades_one_marker(self):
        self.feed(payload(), *[payload(kills=k, round_kills=k) for k in range(1, 6)])
        multis = [m for m in self.w.match.markers if m["type"] == "multikill"]
        self.assertEqual([m["label"] for m in multis], ["ACE"])
        self.assertEqual(multis[0]["event"]["KillStreak"], 5)

    def test_leaving_early_saves_unfinished(self):
        self.feed(payload(), payload(kills=1, round_kills=1))
        self.feed({"provider": {"steamid": ME}, "auth": {"token": "tok"}})     # back in the menu
        self.assertTrue(self.rec.stops[0]["tag"].endswith("_Unfinished_1-0-0"))

    def test_game_closed_mid_match(self):
        self.feed(payload())
        self.settle(40)                                                          # no updates at all
        self.assertEqual(len(self.rec.stops), 1)

    def test_disabled(self):
        self.w.enabled = lambda gid: False
        self.feed(payload())
        self.assertFalse(self.rec.is_recording)


class GsiServerTests(TempDirTest):
    def post(self, port, body):
        req = urllib.request.Request(f"http://127.0.0.1:{port}", data=json.dumps(body).encode(), method="POST")
        try:
            return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=2).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_token_is_required(self):
        got = []
        server = GsiServer(0, "secret", got.append).start()
        try:
            self.assertEqual(self.post(server.port, {"auth": {"token": "wrong"}}), 403)
            self.assertEqual(self.post(server.port, {"auth": {"token": "secret"}, "map": {}}), 200)
        finally:
            server.stop()
        self.assertEqual(len(got), 1)


class InstallerTests(TempDirTest):
    def test_finds_cs2_in_a_second_library_and_writes_cfg(self):
        steam, games = self.tmp / "Steam", self.tmp / "Games"
        (steam / "steamapps").mkdir(parents=True)
        cfg_dir = games / CS2Installer.GAME_CFG
        cfg_dir.mkdir(parents=True)
        (games / "steamapps" / "appmanifest_730.acf").write_text("x")
        vdf = '"libraryfolders"\n{\n "0" { "path" "%s" }\n "1" { "path" "%s" }\n}' % (
            str(steam).replace("\\", "\\\\"), str(games).replace("\\", "\\\\"))
        (steam / "steamapps" / "libraryfolders.vdf").write_text(vdf)
        self.assertEqual(CS2Installer.find_cfg_dir(steam), cfg_dir)
        target = CS2Installer.install(3021, "tok123", cfg_dir)
        text = target.read_text()
        self.assertIn('"uri"       "http://127.0.0.1:3021"', text)
        self.assertIn('"token" "tok123"', text)
        self.assertIn('"player_match_stats"  "1"', text)


class BookmarkTests(TempDirTest):
    def test_league_match_keeps_bookmarks(self):
        def mark(sim):
            if 60 <= sim.watcher.client.game_time < 61:
                sim.recorder.bookmark()
        rec = MatchSimulator(self.tmp).run(during=mark).recorder
        data = Sidecar.read(rec.stops[0]["path"])
        self.assertEqual(sum(m["type"] == "bookmark" for m in data["markers"]), 1)

    def test_manual_recording_with_bookmarks_gets_highlights(self):
        from Core.config import AppConfig
        from Core.engine import Engine
        engine = Engine(AppConfig())
        engine.recorder = FakeRecorder(self.tmp, FakeClock())
        engine.toggle_manual()
        engine.recorder.clock.advance(12)
        engine.bookmark()
        engine.recorder.clock.advance(5)
        engine.toggle_manual()
        data = Sidecar.read(engine.recorder.stops[0]["path"])
        self.assertEqual([m["video_time"] for m in data["markers"]], [12])
        self.assertEqual(data["game"]["title"], "Manual recording")
