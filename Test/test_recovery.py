"""Recovering highlights from a leftover .chapters.txt - uses a real one from a failed save."""
import shutil
from pathlib import Path

from helpers import TempDirTest

from Core.library import RecordingLibrary
from Core.recovery import HighlightRecovery

SAMPLE = Path(__file__).resolve().parent / "sample_chapters.txt"


class RecoveryTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.chapters = self.tmp / "LoL_2026-09-26_17-40-28.chapters.txt"
        shutil.copy(SAMPLE, self.chapters)
        (self.tmp / "LoL_2026-09-26_17-40-28.mkv").write_bytes(b"x")
        (self.tmp / "LoL_2026-09-26_17-40-28_Sion_Lose_6-5-8.mp4").write_bytes(b"x")

    def test_parses_real_chapter_file(self):
        chapters = HighlightRecovery.read_chapters(self.chapters)
        self.assertEqual(len(chapters), 25)
        self.assertEqual(chapters[2], (188.3, "Killed Kirin + First blood (you)"))

    def test_classification(self):
        c = HighlightRecovery.classify
        self.assertEqual(c("Killed by mako")[0], "death")
        self.assertEqual(c("Killed mako")[0], "kill")
        self.assertEqual(c("Chemtech Dragon taken by Lisan al Gaib")[0], "objective")
        self.assertEqual(c("Baron STOLEN by me")[1], 3)
        self.assertEqual(c("Turret destroyed")[0], "structure")
        self.assertEqual(c("Game end - Lose")[0], "game_end")

    def test_recover_attaches_to_mp4_and_counts_kda(self):
        r = HighlightRecovery.recover(self.chapters, mode="CLASSIC")  # champion comes from the .mp4 name
        self.assertTrue(r.video.name.endswith("_Sion_Lose_6-5-8.mp4"))
        self.assertEqual((r.champion, r.result, r.kda), ("Sion", "Lose", "6/5/8"))
        self.assertEqual(r.duplicate_mkv.name, "LoL_2026-09-26_17-40-28.mkv")
        entry = next(e for e in RecordingLibrary(self.tmp).scan() if e.video == r.video)
        self.assertEqual((entry.title, entry.result, entry.kda, entry.mode_name), ("Sion", "Lose", "6/5/8", "Summoner's Rift"))
        self.assertTrue(any(m["label"] == "First blood (you)" for m in entry.my_markers))
        self.assertFalse(any(m["label"] == "Start" for m in entry.markers))

    def test_refuses_to_overwrite(self):
        HighlightRecovery.recover(self.chapters)
        with self.assertRaises(FileExistsError):
            HighlightRecovery.recover(self.chapters)

    def test_without_tagged_mp4_falls_back_to_counting(self):
        (self.tmp / "LoL_2026-09-26_17-40-28_Sion_Lose_6-5-8.mp4").unlink()
        r = HighlightRecovery.recover(self.chapters, champion="Ahri")
        self.assertEqual((r.video.suffix, r.champion, r.result, r.kda), (".mkv", "Ahri", "Lose", "6/5/8"))


import unittest  # noqa: E402

from Capture.chapters import ChapterWriter  # noqa: E402
from Capture.ffmpeg import FFmpeg  # noqa: E402

FF = FFmpeg.locate()


@unittest.skipUnless(FF, "ffmpeg not found")
class RecoverFromVideoTests(TempDirTest):
    def test_rebuilds_json_from_embedded_chapters(self):
        import subprocess
        mkv = self.tmp / "LoL_2026-09-26_18-19-25.mkv"
        mp4 = self.tmp / "LoL_2026-09-26_18-19-25_Ashe_Win_10-8-10.mp4"
        subprocess.run([FF.exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x90",
                        "-t", "30", str(mkv)], check=True)
        meta = ChapterWriter.write(mkv, [(5, "Killed Xarq"), (12, "Killed by Loysenpai"),
                                         (20, "Killed Xarq + Double kill"), (28, "Game end - Win")], 30)
        FF.remux(mkv, mp4, meta)
        mkv.unlink()
        r = HighlightRecovery.recover_from_video(mp4, FF)
        self.assertEqual((r.champion, r.result, r.kda), ("Ashe", "Win", "10/8/10"))
        entry = RecordingLibrary(self.tmp).scan()[0]
        self.assertEqual(entry.title, "Ashe")
        self.assertIn("Double kill", [m["label"] for m in entry.markers])

    def test_plain_video_without_chapters_says_so(self):
        import subprocess
        video = self.tmp / "manual.mp4"
        subprocess.run([FF.exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x90",
                        "-t", "3", str(video)], check=True)
        with self.assertRaises(FileNotFoundError):
            HighlightRecovery.recover_from_video(video, FF)
