"""Real ffmpeg: clip export, highlight reel and chapter remux. Skipped if ffmpeg isn't installed."""
import re
import subprocess
import unittest

from helpers import TempDirTest

from Capture.chapters import ChapterWriter
from Capture.ffmpeg import FFmpeg
from Core.clips import ClipExporter, Segment

FF = FFmpeg.locate()


@unittest.skipUnless(FF, "ffmpeg not found (run Tools/get_ffmpeg.py)")
class FfmpegTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.video = self.tmp / "match.mkv"
        subprocess.run([FF.exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=320x180:rate=30", "-f", "lavfi", "-i", "sine=f=440", "-t", "30",
                        "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", str(self.video)], check=True)
        self.segs = [Segment(2, 8, "Kill"), Segment(15, 20, "Double kill")]
        self.exporter = ClipExporter(FF)

    @staticmethod
    def duration(path) -> float:
        h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", FF.run(["-i", str(path)]).stderr).groups()
        return int(h) * 3600 + int(m) * 60 + float(s)

    def test_clips(self):
        paths = self.exporter.export_clips(self.video, self.segs, self.tmp / "Clips")
        self.assertEqual(len(paths), 2)
        self.assertAlmostEqual(self.duration(paths[0]), 6, delta=1.5)
        self.assertAlmostEqual(self.duration(paths[1]), 5, delta=1.5)

    def test_reel(self):
        out = self.exporter.export_reel(self.video, self.segs, self.tmp / "reel.mp4", "x264", 23)
        self.assertAlmostEqual(self.duration(out), 11, delta=0.6)

    def test_chapters_survive_remux(self):
        meta = ChapterWriter.write(self.video, [(5, "Killed Bob"), (20, "Baron STOLEN")], 30)
        out = self.tmp / "final.mp4"
        self.assertTrue(FF.remux(self.video, out, meta))
        info = FF.run(["-i", str(out)]).stderr
        self.assertIn("Chapters:", info)
        self.assertIn("Baron STOLEN", info)

    def test_thumbnail(self):
        jpg = self.tmp / "thumb.jpg"
        self.assertTrue(FF.extract_frame(self.video, jpg, 5))
