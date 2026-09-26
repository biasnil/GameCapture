import json

from helpers import TempDirTest

from Capture.chapters import ChapterWriter
from Core.clips import Segment
from Core.formatting import Format
from Core.library import RecordingLibrary
from Core.sidecar import Sidecar


class LibraryTests(TempDirTest):
    def test_scan_and_game_identity(self):
        (self.tmp / "match.mp4").write_bytes(b"x")
        Sidecar.path_for(self.tmp / "match.mp4").write_text(json.dumps(
            {"game": {"champion": "Ahri", "result": "Win", "kills": 1, "deaths": 0, "assists": 2, "mode": "ARAM"},
             "markers": [{"video_time": 5, "label": "Killed X", "involves_me": True, "type": "kill"}]}))
        for name in ("manual.mkv", "half.tmp.mkv", "recording_now.mkv"):
            (self.tmp / name).write_bytes(b"x")
        entries = {e.video.name: e for e in RecordingLibrary(self.tmp).scan(exclude=self.tmp / "recording_now.mkv")}
        self.assertEqual(set(entries), {"match.mp4", "manual.mkv"})
        match, manual = entries["match.mp4"], entries["manual.mkv"]
        self.assertEqual(match.game_name, "League of Legends")   # v1 sidecar migrated
        self.assertEqual((match.kda, match.mode_name), ("1/0/2", "ARAM"))
        self.assertTrue(match.editable)
        self.assertEqual(manual.game_name, "Manual recording")
        self.assertFalse(manual.editable)

    def test_favourite_saves_in_new_format(self):
        video = self.tmp / "m.mp4"
        video.write_bytes(b"x")
        Sidecar.path_for(video).write_text(json.dumps(
            {"markers": [{"video_time": 5, "label": "K", "involves_me": True, "type": "kill"}]}))
        entry = RecordingLibrary(self.tmp).scan()[0]
        entry.markers[0]["favorite"] = True
        RecordingLibrary.save(entry)
        on_disk = json.loads(Sidecar.path_for(video).read_text())
        self.assertTrue(on_disk["markers"][0]["favorite"])
        self.assertEqual(on_disk["schema"], Sidecar.SCHEMA_VERSION)


class HelperTests(TempDirTest):
    def test_segments_merge_and_clamp(self):
        marks = [{"video_time": 3, "label": "A"}, {"video_time": 8, "label": "B"}, {"video_time": 60, "label": "C"}]
        segs = Segment.from_markers(marks, pre=10, post=5, duration=62)
        self.assertEqual(len(segs), 2)
        self.assertEqual((segs[0].start, segs[0].end, segs[0].label), (0.0, 13, "A + B"))
        self.assertEqual(segs[1].end, 62)

    def test_formatting(self):
        self.assertEqual(Format.safe_filename("Kai'Sa_Win_3-1-2"), "KaiSa_Win_3-1-2")
        self.assertEqual(Format.safe_filename("Nunu & Willump"), "NunuWillump")
        self.assertEqual(Format.slug("Killed Bob + Double kill"), "Killed_Bob_Double_kill")
        self.assertEqual(Format.duration(75), "01:15")
        self.assertEqual(Format.duration(3725), "1:02:05")

    def test_chapter_file(self):
        text = ChapterWriter.write(self.tmp / "v.mkv", [(30.0, "a=b"), (10.0, "Kill")], 60).read_text()
        self.assertTrue(text.startswith(";FFMETADATA1"))
        self.assertIn("title=Start", text)
        self.assertIn("title=a\\=b", text)
        self.assertIn("END=60000", text)
