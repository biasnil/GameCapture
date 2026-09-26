"""Saving a finished recording while Windows has the files locked (the bug that lost highlights)."""
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import TempDirTest

from Capture.ffmpeg import FFmpeg
from Capture.recorder import Recorder
from Core.config import AppConfig
from Core.library import RecordingLibrary

FF = FFmpeg.locate()
_real_unlink = Path.unlink


@unittest.skipUnless(FF, "ffmpeg not found (run Tools/get_ffmpeg.py)")
class FinalizeTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.cfg = AppConfig()
        self.cfg.recording.output_dir = str(self.tmp)
        self.cfg.log_dir = str(self.tmp / "logs")
        self.rec = Recorder(self.cfg, FF, "x264")
        self.mkv = self.tmp / "LoL_2026-09-26_17-40-28.mkv"
        subprocess.run([FF.exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=160x90:rate=10", "-t", "5", "-c:v", "libx264", "-preset", "ultrafast",
                        str(self.mkv)], check=True)
        self.rec._mkv = self.mkv

    def locked(self, *names):
        """Make Path.unlink fail like Windows does for files another program has open."""
        def unlink(path, missing_ok=False):
            if path.name in names:
                raise PermissionError(32, "The process cannot access the file", str(path))
            return _real_unlink(path, missing_ok=missing_ok)
        return patch.object(Path, "unlink", unlink)

    def test_locked_mkv_does_not_abort_the_save(self):
        with self.locked(self.mkv.name), patch("Capture.recorder.time.sleep"):
            final = self.rec._finish("Sion_Lose_6-5-8", [(1.0, "Killed Kirin")], 5.0)
        self.assertEqual(final.name, "LoL_2026-09-26_17-40-28_Sion_Lose_6-5-8.mp4")
        self.assertTrue(final.exists())
        self.assertFalse(list(self.tmp.glob("*.chapters.txt")), "chapters file must be cleaned up")
        self.assertIn(self.mkv, self.rec.busy_files, "locked leftover must be hidden from the library")
        names = [e.video.name for e in RecordingLibrary(self.tmp).scan(exclude=self.rec.busy_files)]
        self.assertEqual(names, [final.name], "library must show one recording, not two")
        self.rec._retry_pending_deletes()  # lock released later -> leftover goes away
        self.assertFalse(self.mkv.exists())
        self.assertEqual(self.rec.busy_files, set())

    def test_files_stay_hidden_while_converting(self):
        seen = []
        real_remux = FF.remux

        def remux(src, dst, chapters=None):
            seen.append({p.name for p in self.rec.busy_files})
            return real_remux(src, dst, chapters)
        self.rec._busy = {self.mkv}
        with patch.object(FF, "remux", remux):
            self.rec._finish("X", None, 5.0)
        self.assertEqual(self.rec.busy_files, set(), "everything visible again once saved")
        self.assertIn(self.mkv.name, seen[0])
        self.assertIn("LoL_2026-09-26_17-40-28_X.mp4", seen[0])

    def test_unexpected_error_keeps_the_mkv(self):
        with patch.object(FF, "remux", side_effect=RuntimeError("disk full")):
            final = self.rec._finish("X", None, 5.0)
        self.assertEqual(final, self.mkv)
        self.assertFalse(self.rec.is_finalizing)
