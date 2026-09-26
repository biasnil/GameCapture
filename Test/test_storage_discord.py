"""Storage auto-delete limit, Discord size fitting, and the clip-length regression."""
import json
import os
import subprocess
import time
import unittest

from helpers import TempDirTest

from Capture.chapters import ChapterWriter
from Capture.ffmpeg import FFmpeg
from Core.clips import ClipExporter, Segment
from Core.config import StorageSettings
from Core.discord import ClipTooLongError, DiscordFitter
from Core.sidecar import Sidecar
from Core.storage import GB, StorageManager

FF = FFmpeg.locate()


class StorageTests(TempDirTest):
    def make(self, name, mb, age_days, favorite=False):
        video = self.tmp / f"{name}.mp4"
        with open(video, "wb") as f:
            f.truncate(int(mb * 1024 * 1024))          # sparse file: real size, no disk used
        Sidecar.path_for(video).write_text(json.dumps(
            {"markers": [{"video_time": 1, "label": "K", "type": "kill", "involves_me": True,
                          **({"favorite": True} if favorite else {})}]}))
        t = time.time() - age_days * 86400
        os.utime(video, (t, t))
        return video

    def manager(self, limit_mb, keep_fav=True, enabled=True):
        settings = StorageSettings(limit_enabled=enabled, limit_gb=limit_mb * 1024 * 1024 / GB, keep_favorites=keep_fav)
        return StorageManager(self.tmp, self.tmp / "Clips", settings)

    def test_deletes_oldest_until_under_limit(self):
        old, mid, new = self.make("old", 30, 3), self.make("mid", 30, 2), self.make("new", 30, 1)
        deleted = self.manager(limit_mb=65).enforce()
        self.assertEqual(deleted, [old])
        self.assertFalse(old.exists() or Sidecar.path_for(old).exists())
        self.assertTrue(mid.exists() and new.exists())

    def test_keeps_favourites_and_busy_files(self):
        fav, busy, plain = self.make("fav", 30, 3, favorite=True), self.make("busy", 30, 2), self.make("plain", 30, 1)
        deleted = self.manager(limit_mb=10).enforce(exclude={busy})
        self.assertEqual(deleted, [plain])
        self.assertTrue(fav.exists() and busy.exists())

    def test_favourites_can_be_released(self):
        fav = self.make("fav", 30, 3, favorite=True)
        self.assertEqual(self.manager(limit_mb=10, keep_fav=False).enforce(), [fav])

    def test_off_means_off(self):
        self.make("a", 30, 1)
        self.assertEqual(self.manager(limit_mb=1, enabled=False).enforce(), [])

    def test_clips_are_never_deleted_and_counted_separately(self):
        clips = self.tmp / "Clips"
        clips.mkdir()
        (clips / "c.mp4").write_bytes(b"x" * 1000)
        self.make("a", 1, 1)
        m = self.manager(limit_mb=0.0001)
        u = m.usage()
        self.assertEqual(u.clips, 1000)
        self.assertGreater(u.recordings, 1024 * 1024)
        m.enforce()
        self.assertTrue((clips / "c.mp4").exists())


class DiscordPlanTests(unittest.TestCase):
    def test_short_clip_keeps_1080p(self):
        kbps, height = DiscordFitter.plan(15, 20)
        self.assertEqual(height, 1080)
        self.assertGreater(kbps, 9000)

    def test_longer_clip_drops_resolution(self):
        self.assertEqual(DiscordFitter.plan(60, 20)[1], 720)
        self.assertEqual(DiscordFitter.plan(150, 20)[1], 480)

    def test_way_too_long_is_refused_clearly(self):
        with self.assertRaises(ClipTooLongError):
            DiscordFitter.plan(1800, 20)

    def test_nitro_limit_gives_more_room(self):
        self.assertGreater(DiscordFitter.plan(60, 50)[0], DiscordFitter.plan(60, 20)[0])


@unittest.skipUnless(FF, "ffmpeg not found (run Tools/get_ffmpeg.py)")
class RealFfmpegTests(TempDirTest):
    def recording(self, seconds=300):
        """Built like a real recording: B-frames, audio, chapters -> .mp4 (has the chapter data track)."""
        mkv, mp4 = self.tmp / "rec.mkv", self.tmp / "rec.mp4"
        subprocess.run([FF.exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30",
                        "-f", "lavfi", "-i", "sine", "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
                        "-bf", "3", "-g", "60", "-c:a", "aac", str(mkv)], check=True)
        meta = ChapterWriter.write(mkv, [(10, "Kill"), (200, "Baron")], seconds)
        self.assertTrue(FF.remux(mkv, mp4, meta))
        return mp4

    def test_clip_is_its_own_length(self):
        """Bug: a 15 s clip showed as match-length in VLC (chapter data track was copied)."""
        clip = ClipExporter(FF).export_clips(self.recording(), [Segment(240, 255, "Turret destroyed")],
                                             self.tmp / "Clips")[0]
        self.assertLess(FF.duration(clip), 20)
        self.assertNotIn("Data:", FF.run(["-i", str(clip)]).stderr)

    def test_fit_for_discord(self):
        src = self.tmp / "big.mp4"   # noisy video at a high bitrate -> too big for a 1 MB 'limit'
        subprocess.run([FF.exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "nullsrc=s=640x360:r=30,geq=random(1)*255:128:128", "-f", "lavfi", "-i", "sine", "-t", "8",
                        "-c:v", "libx264", "-b:v", "8M", "-c:a", "aac", str(src)], check=True)
        self.assertGreater(src.stat().st_size, 1_000_000)
        out = DiscordFitter(FF, "x264").fit(src, limit_mb=1)
        self.assertNotEqual(out, src)
        self.assertLessEqual(out.stat().st_size, DiscordFitter.limit_bytes(1))
        self.assertAlmostEqual(FF.duration(out), 8, delta=0.5)

    def test_small_clip_is_left_alone(self):
        src = self.tmp / "small.mp4"
        subprocess.run([FF.exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x90",
                        "-t", "2", str(src)], check=True)
        self.assertEqual(DiscordFitter(FF).fit(src, 20), src)
