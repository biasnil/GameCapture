"""Any game: games you add yourself, and settings kept in the app data folder."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fakes import FakeClock, FakeProcesses, FakeRecorder
from helpers import TempDirTest

from Core.config import AppConfig, GameSettings
from Core.paths import Paths
from Games.registry import GameRegistry
from Games.session_watcher import ProcessSessionWatcher


class CustomGameTests(TempDirTest):
    def tearDown(self):
        GameRegistry.load_custom({})   # don't leak test games into other tests
        super().tearDown()

    def cfg_with(self, name="My Indie Game", exe="MyIndie.exe") -> AppConfig:
        cfg = AppConfig()
        cfg.games[GameRegistry.custom_id(name)] = GameSettings(processes=[exe], name=name, custom=True)
        return cfg

    def test_custom_game_is_registered_and_recorded_while_running(self):
        cfg = self.cfg_with()
        GameRegistry.load_custom(cfg.games)
        game = GameRegistry.get("custom_my_indie_game")
        self.assertTrue(game.custom and game.supported)
        self.assertEqual((game.processes, game.prefix), (("MyIndie.exe",), "MyIndieGame"))
        self.assertIn(game, GameRegistry.session_games())

        clock, procs = FakeClock(), FakeProcesses()
        rec = FakeRecorder(self.tmp, clock)
        w = ProcessSessionWatcher(game, cfg.auto, rec, monitor=procs)
        with patch("Games.session_watcher.time.monotonic", clock):
            procs.up = True
            w.tick()
            self.assertTrue(rec.is_recording)
            self.assertEqual(rec.prefix, "MyIndieGame")
            procs.up = False
            for _ in range(20):
                clock.advance(1)
                w.tick()
        self.assertFalse(rec.is_recording)
        self.assertEqual(len(rec.stops), 1)

    def test_removed_custom_game_is_unregistered(self):
        cfg = self.cfg_with()
        GameRegistry.load_custom(cfg.games)
        del cfg.games["custom_my_indie_game"]
        GameRegistry.load_custom(cfg.games)
        self.assertFalse(GameRegistry.known("custom_my_indie_game"))
        GameRegistry.unregister("league")          # built-in games can never be removed
        self.assertTrue(GameRegistry.known("league"))

    def test_custom_games_survive_a_config_roundtrip(self):
        path = self.tmp / "config.json"
        self.cfg_with("Speed Runner", "speedrun.exe").save(path)
        cfg = AppConfig.load(path)
        self.assertEqual(list(cfg.custom_games()), ["custom_speed_runner"])
        self.assertEqual(cfg.game("custom_speed_runner").processes, ["speedrun.exe"])

    def test_ids_are_safe_and_readable(self):
        self.assertEqual(GameRegistry.custom_id("Baldur's Gate 3"), "custom_baldur_s_gate_3")
        self.assertEqual(GameRegistry.custom_id("!!!"), "custom_game")

    def test_engine_adds_and_drops_watchers_without_a_restart(self):
        from Core.engine import Engine
        cfg = self.cfg_with()
        cfg.auto.enabled = False                   # watchers run, but never record in a test
        cfg.recording.output_dir = str(self.tmp)
        engine = Engine(cfg)
        engine.recorder = FakeRecorder(self.tmp, FakeClock())
        engine._psutil = True
        engine.watchers = []
        try:
            engine.sync_game_watchers()
            ids = [w.GAME.id for w in engine.watchers]
            self.assertIn("custom_my_indie_game", ids)
            self.assertIn("apex", ids)
            first = next(w for w in engine.watchers if w.GAME.id == "custom_my_indie_game")
            engine.sync_game_watchers()            # nothing changed: same watcher kept
            self.assertIn(first, engine.watchers)
            cfg.game("custom_my_indie_game").processes = ["MyIndie-Win64.exe"]
            engine.sync_game_watchers()            # renamed .exe: rebuilt
            new = next(w for w in engine.watchers if w.GAME.id == "custom_my_indie_game")
            self.assertIsNot(new, first)
            self.assertEqual(new.monitor.names, {"myindie-win64.exe"})
            del cfg.games["custom_my_indie_game"]
            engine.sync_game_watchers()            # removed
            self.assertNotIn("custom_my_indie_game", [w.GAME.id for w in engine.watchers])
            self.assertIsNotNone(engine.deadlock)  # Deadlock gets its match-lookup watcher
        finally:
            for w in engine.watchers:
                w.shutdown()
            if engine.deadlock:
                engine.deadlock.stop()


class AppDataTests(TempDirTest):
    def test_settings_live_in_the_user_profile(self):
        with patch.dict(os.environ, {"GAMECAPTURE_HOME": ""}, clear=False), \
                patch("Core.paths.sys.platform", "win32"), \
                patch.dict(os.environ, {"APPDATA": str(self.tmp / "Roaming")}):
            from Core.paths import _data_dir
            self.assertEqual(_data_dir(), self.tmp / "Roaming" / "GameCapture")

    def test_override_folder(self):
        with patch.dict(os.environ, {"GAMECAPTURE_HOME": str(self.tmp / "portable")}):
            from Core.paths import _data_dir
            self.assertEqual(_data_dir(), self.tmp / "portable")

    def test_old_config_is_copied_once_and_kept(self):
        legacy, target = self.tmp / "old" / "config.json", self.tmp / "AppData" / "config.json"
        legacy.parent.mkdir()
        legacy.write_text(json.dumps({"capture": {"fps": 30}}))
        self.assertTrue(Paths.migrate_legacy_config(target, legacy))
        self.assertEqual(AppConfig.load(target).capture.fps, 30)
        self.assertTrue(legacy.exists())
        legacy.write_text(json.dumps({"capture": {"fps": 144}}))
        self.assertFalse(Paths.migrate_legacy_config(target, legacy))   # never overwrites your settings
        self.assertEqual(AppConfig.load(target).capture.fps, 30)

    def test_relative_folders_are_inside_the_app_data_folder(self):
        self.assertEqual(Paths.resolve("Logs"), Paths.DATA / "Logs")
        absolute = self.tmp / "Logs"     # a real absolute path on every OS ("/abs" has no drive on Windows)
        self.assertEqual(Paths.resolve(absolute), absolute)

    def test_save_creates_the_folder(self):
        path = self.tmp / "new" / "deeper" / "config.json"
        AppConfig().save(path)
        self.assertTrue(path.exists())
        self.assertFalse(path.with_suffix(".tmp").exists())


if __name__ == "__main__":
    unittest.main()
