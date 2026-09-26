import json

from helpers import TempDirTest

from Core.sidecar import Sidecar
from Games.registry import GameRegistry


class SidecarTests(TempDirTest):
    def test_roundtrip_has_schema_and_game_id(self):
        video = self.tmp / "a.mp4"
        Sidecar.write(video, Sidecar.build(video.name, GameRegistry.LEAGUE.id, {"champion": "Ahri"}, []))
        data = Sidecar.read(video)
        self.assertEqual(data["schema"], Sidecar.SCHEMA_VERSION)
        self.assertEqual(data["game_id"], "league")
        self.assertFalse(list(self.tmp.glob("*.tmp")), "atomic write left a temp file")

    def test_v1_file_is_upgraded_in_memory_only(self):
        video = self.tmp / "old.mp4"
        Sidecar.path_for(video).write_text(json.dumps({"video": "old.mp4", "game": {}, "markers": []}))
        data = Sidecar.read(video)
        self.assertEqual((data["schema"], data["game_id"]), (2, "league"))
        self.assertNotIn("schema", json.loads(Sidecar.path_for(video).read_text()), "reading must not rewrite")
        Sidecar.write(video, data)
        self.assertEqual(json.loads(Sidecar.path_for(video).read_text())["schema"], 2)

    def test_newer_schema_is_read_only(self):
        video = self.tmp / "future.mp4"
        future = {"schema": 99, "game_id": "league", "new_field": 1, "markers": []}
        Sidecar.path_for(video).write_text(json.dumps(future))
        data = Sidecar.read(video)
        self.assertFalse(Sidecar.is_writable(data))
        with self.assertRaises(PermissionError):
            Sidecar.write(video, data)
        self.assertEqual(json.loads(Sidecar.path_for(video).read_text()), future)

    def test_garbage_or_missing_file(self):
        video = self.tmp / "x.mp4"
        self.assertIsNone(Sidecar.read(video))
        Sidecar.path_for(video).write_text("{not json")
        self.assertIsNone(Sidecar.read(video))

    def test_game_registry(self):
        self.assertEqual(GameRegistry.get("league").name, "League of Legends")
        self.assertIsNone(GameRegistry.get(None))
        self.assertEqual(GameRegistry.get("counter_strike").name, "Counter Strike")
