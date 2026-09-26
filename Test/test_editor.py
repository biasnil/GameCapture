"""Video editor: timeline editing maths, project files, and (if ffmpeg is present) a real export."""
import subprocess
import unittest
from pathlib import Path

from helpers import TempDirTest

from Capture.ffmpeg import FFmpeg
from Core.editor import MIN_CLIP, EditProject, ExportSettings, ProjectExporter, ProjectStore

FF = FFmpeg.locate()


def project(*durations: float) -> EditProject:
    """One source per duration, each on the timeline once, whole."""
    p = EditProject("Best kills")
    for i, d in enumerate(durations):
        p.add_source(f"v{i}.mp4", d, markers=[{"type": "kill", "label": "Kill", "involves_me": True,
                                                "video_time": d / 2}])
        p.add_clip(f"v{i}.mp4", 0, d, f"clip {i}")
    return p


class TimelineTests(unittest.TestCase):
    def test_locate_maps_timeline_time_to_clip_and_source_time(self):
        p = project(10, 4)
        self.assertEqual(p.duration, 14)
        self.assertEqual(p.locate(3), (0, 3))
        self.assertEqual(p.locate(12), (1, 2))
        self.assertEqual(p.locate(99), (1, 4))      # past the end = end of the last clip
        self.assertIsNone(EditProject().locate(1))

    def test_split_cuts_the_clip_under_the_playhead(self):
        p = project(10, 4)
        self.assertTrue(p.split(6))
        self.assertEqual([(c.start, c.end) for c in p.clips], [(0, 6), (6, 10), (0, 4)])
        self.assertEqual(p.duration, 14)            # splitting never changes the length
        self.assertFalse(p.split(6))                # right on a cut: nothing to split
        self.assertFalse(p.split(MIN_CLIP / 2))     # would leave a sliver

    def test_trim_is_clamped_to_the_source_and_a_minimum_length(self):
        p = project(10)
        p.trim(0, start=-5, end=50)
        self.assertEqual((p.clips[0].start, p.clips[0].end), (0, 10))
        p.trim(0, start=9.99)
        self.assertAlmostEqual(p.clips[0].duration, MIN_CLIP)

    def test_duplicate_move_delete(self):
        p = project(10, 4, 6)
        self.assertEqual(p.duplicate(0), 1)
        self.assertEqual([c.label for c in p.clips], ["clip 0", "clip 0", "clip 1", "clip 2"])
        self.assertNotEqual(p.clips[0].id, p.clips[1].id)
        self.assertEqual(p.move(3, 0), 0)
        self.assertEqual([c.label for c in p.clips], ["clip 2", "clip 0", "clip 0", "clip 1"])
        p.delete(1)
        self.assertEqual(p.clip_offset(2), 6 + 10)

    def test_removing_a_video_removes_its_clips(self):
        p = project(10, 4)
        p.remove_source("v0.mp4")
        self.assertEqual([c.source for c in p.clips], ["v1.mp4"])

    def test_markers_follow_the_clip(self):
        p = project(10)
        self.assertEqual(len(p.markers_in(p.clips[0])), 1)
        p.trim(0, end=4)                            # the kill at 5 s is cut off
        self.assertEqual(p.markers_in(p.clips[0]), [])

    def test_clip_is_clamped_to_its_source(self):
        p = project(10)
        clip = p.add_clip("v0.mp4", 8, 30)
        self.assertEqual(clip.end, 10)
        self.assertIsNone(p.add_clip("v0.mp4", 10, 12))   # nothing left


class HighlightClipTests(unittest.TestCase):
    def match(self) -> EditProject:
        """A 120 s match: kills at 20 and 23 s (one moment), a death at 50 s, a bookmark at 90 s."""
        p = EditProject()
        p.add_source("m.mp4", 120, markers=[
            {"type": "kill", "label": "Kill", "involves_me": True, "video_time": 20},
            {"type": "multikill", "label": "Double kill", "involves_me": True, "video_time": 23},
            {"type": "death", "label": "Died", "involves_me": True, "video_time": 50},
            {"type": "kill", "label": "Their kill", "involves_me": False, "video_time": 60},
            {"type": "bookmark", "label": "Bookmark 1", "involves_me": True, "video_time": 90},
        ])
        return p

    def test_each_highlight_becomes_its_own_clip(self):
        p = self.match()
        self.assertEqual(p.add_highlights("m.mp4", pre=10, post=5), 2)
        self.assertEqual([(c.start, c.end, c.label) for c in p.clips],
                         [(10, 28, "Kill + Double kill"), (80, 95, "Bookmark 1")])

    def test_keep_highlights_ripples_the_rest_up(self):
        p = self.match()
        p.add_clip("m.mp4", 0, 120, "whole match")
        p.add_clip("m.mp4", 100, 110, "after")
        self.assertEqual(p.keep_highlights(0, pre=10, post=5), 2)
        self.assertEqual([c.label for c in p.clips], ["Kill + Double kill", "Bookmark 1", "after"])
        self.assertEqual(p.clip_offset(2), 18 + 15)          # no gaps: the last clip moved up

    def test_keep_highlights_stays_inside_the_clip(self):
        p = self.match()
        p.add_clip("m.mp4", 15, 92)                          # cuts through both highlight windows
        self.assertEqual(p.keep_highlights(0, pre=10, post=5), 2)
        self.assertEqual([(c.start, c.end) for c in p.clips], [(15, 28), (80, 92)])

    def test_clip_without_highlights_is_left_alone(self):
        p = self.match()
        p.add_clip("m.mp4", 30, 70)
        self.assertEqual(p.keep_highlights(0, pre=2, post=2), 0)
        self.assertEqual([(c.start, c.end) for c in p.clips], [(30, 70)])


class ProjectStoreTests(TempDirTest):
    def test_roundtrip_newest_first(self):
        store = ProjectStore(self.tmp / "Projects")
        a, b = project(10), project(4)
        a.export = ExportSettings(resolution="720p", fps=30)
        b.name = "Second"
        store.save(a)
        store.save(b)
        loaded = store.list()
        self.assertEqual([p.name for p in loaded], ["Second", "Best kills"])
        again = store.load(a.id)
        self.assertEqual((again.export.resolution, again.export.fps), ("720p", 30))
        self.assertEqual([(c.start, c.end, c.label) for c in again.clips], [(0, 10, "clip 0")])
        self.assertEqual(again.sources[0].markers[0]["label"], "Kill")
        store.delete(a.id)
        self.assertEqual([p.id for p in store.list()], [b.id])

    def test_broken_file_is_skipped(self):
        store = ProjectStore(self.tmp)
        (self.tmp / "junk.json").write_text("{not json")
        store.save(project(3))
        self.assertEqual(len(store.list()), 1)


class ExportArgsTests(unittest.TestCase):
    def test_output_size_never_upscales(self):
        ex = ProjectExporter(None)
        p = project(5)
        probes = {"v0.mp4": {"width": 1280, "height": 720, "audio": True}}
        p.export.resolution = "1080p"
        self.assertEqual(ex.output_size(p, probes), (1280, 720))
        p.export.resolution = "480p"
        self.assertEqual(ex.output_size(p, probes), (852, 480))

    def test_silent_clip_gets_silence_so_sound_stays_in_sync(self):
        p = project(5, 3)
        probes = {"v0.mp4": {"width": 640, "height": 360, "audio": True},
                  "v1.mp4": {"width": 640, "height": 360, "audio": False}}
        args = ProjectExporter(None).build_args(p, Path("out.mp4"), "x264", probes)
        graph = args[args.index("-filter_complex") + 1]
        self.assertIn("[0:a:0]", graph)
        self.assertNotIn("[1:a:0]", graph)
        self.assertIn("anullsrc", graph)
        self.assertIn("concat=n=2:v=1:a=1", graph)

    def test_empty_timeline_is_refused(self):
        with self.assertRaises(ValueError):
            ProjectExporter(None).build_args(EditProject(), Path("x.mp4"), "x264", {})


@unittest.skipUnless(FF, "ffmpeg not found")
class RealExportTests(TempDirTest):
    def make_video(self, name: str, size: str, seconds: int, audio: bool) -> Path:
        out = self.tmp / name
        args = [FF.exe, "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30"]
        if audio:
            args += ["-f", "lavfi", "-i", "sine=frequency=440", "-shortest"]
        args += ["-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
        subprocess.run(args, check=True, capture_output=True)
        return out

    def test_mixed_sizes_and_silent_video_join_into_one(self):
        a = self.make_video("a.mp4", "640x360", 4, audio=True)
        b = self.make_video("b.mp4", "320x240", 3, audio=False)
        p = EditProject("Mix")
        for v in (a, b):
            p.add_source(v, FF.duration(v))
            p.add_clip(v, 0, FF.duration(v))
        p.split(2)
        p.move(1, 2)                                # a[0-2], b, a[2-4]
        seen = []
        out = ProjectExporter(FF).export(p, self.tmp / "out" / "mix.mp4", "x264", lambda f, _m: seen.append(f))
        info = ProjectExporter(FF).probe(out)
        self.assertAlmostEqual(info["duration"], 7, delta=0.25)
        self.assertEqual((info["width"], info["height"]), (640, 360))
        self.assertTrue(info["audio"])
        self.assertEqual(seen[-1], 1.0)

    def test_missing_video_is_reported(self):
        p = EditProject()
        p.add_source(self.tmp / "gone.mp4", 5)
        p.add_clip(self.tmp / "gone.mp4", 0, 5)
        with self.assertRaises(FileNotFoundError):
            ProjectExporter(FF).export(p, self.tmp / "x.mp4")


if __name__ == "__main__":
    unittest.main()


class PerMomentTests(unittest.TestCase):
    """Bug: a Highlights-mode video (highlights back to back) came out as ONE long clip, because the
    padded windows overlap and used to be merged. Now every moment is its own clip."""

    @staticmethod
    def m(t, label="Kill", kind="kill"):
        return {"type": kind, "label": label, "involves_me": True, "video_time": t}

    def test_back_to_back_highlights_stay_separate_clips(self):
        from Core.clips import Segment
        segs = Segment.per_moment([self.m(10), self.m(22), self.m(35), self.m(47)], pre=10, post=5, duration=60)
        self.assertEqual(len(segs), 4)
        for a, b in zip(segs, segs[1:]):
            self.assertEqual(a.end, b.start)               # touching, never overlapping: no footage twice
        self.assertEqual((segs[0].start, segs[0].end), (0, 16))   # edge halfway between 10 and 22

    def test_a_multikill_is_one_moment(self):
        from Core.clips import Segment
        segs = Segment.per_moment([self.m(20), self.m(22, "Double kill", "multikill"), self.m(60)], pre=5, post=5)
        self.assertEqual([s.label for s in segs], ["Kill + Double kill", "Kill"])

    def test_edges_prefer_the_real_cuts_of_a_highlights_video(self):
        from Core.clips import Segment
        segs = Segment.per_moment([self.m(10), self.m(22)], pre=10, post=5, cuts=[13.5, 40])
        self.assertEqual((segs[0].end, segs[1].start), (13.5, 13.5))

    def test_far_apart_highlights_keep_their_padding(self):
        from Core.clips import Segment
        segs = Segment.per_moment([self.m(20), self.m(90)], pre=10, post=5)
        self.assertEqual([(s.start, s.end) for s in segs], [(10, 25), (80, 95)])

    def test_editor_splits_a_highlights_video_into_moments(self):
        p = EditProject()
        p.add_source("hl.mp4", 135, markers=[self.m(t) for t in (8, 20, 33, 47, 60, 74, 88, 101, 114)])
        p.add_clip("hl.mp4", 0, 135)
        self.assertEqual(p.keep_highlights(0, pre=10, post=5), 9)
        self.assertAlmostEqual(p.duration, 119, delta=0.01)   # 0 -> 119 (last kill + 5 s): nothing lost or doubled
        self.assertTrue(all(a.end == b.start for a, b in zip(p.clips, p.clips[1:])))
