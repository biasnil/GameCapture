"""Editor elements (text / images on the video), timeline zoom and the Sessions highlight filters."""
import os
import subprocess
import unittest
from pathlib import Path

from helpers import TempDirTest

from Capture.ffmpeg import FFmpeg
from Core.editor import MIN_CLIP, EditProject, Element, Overlay, ProjectExporter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

FF = FFmpeg.locate()


class ElementModelTests(unittest.TestCase):
    def test_elements_roundtrip_and_timing(self):
        p = EditProject()
        e = p.add_element(Element("text", start=2, end=2, text="GG"))
        self.assertAlmostEqual(e.duration, MIN_CLIP)             # never zero-length
        p.move_element(e.id, start=-5)
        self.assertEqual(e.start, 0)
        p.move_element(e.id, end=6)
        self.assertEqual([x.id for x in p.elements_at(3)], [e.id])
        self.assertEqual(p.elements_at(6), [])                    # end is exclusive
        again = EditProject.from_dict(p.to_dict())
        self.assertEqual((again.elements[0].text, again.elements[0].end), ("GG", 6))
        p.remove_element(e.id)
        self.assertEqual(p.elements, [])

    def test_names(self):
        self.assertEqual(Element("text", text="  ").name, "Text")
        self.assertEqual(Element("image", path="C:/x/logo.png").name, "logo.png")

    def test_overlays_are_layered_on_the_joined_video(self):
        p = EditProject()
        p.add_source("a.mp4", 10)
        p.add_clip("a.mp4", 0, 10)
        args = ProjectExporter(None).build_args(
            p, Path("o.mp4"), "x264", {"a.mp4": {"width": 1280, "height": 720, "audio": False}},
            [Overlay("t.png", 10, 20, 1, 4), Overlay("l.png", 1000, 30, 0, 10)])
        graph = args[args.index("-filter_complex") + 1]
        self.assertIn("[v][1:v]overlay=x=10:y=20:enable='between(t,1.000,4.000)'[o0]", graph)
        self.assertIn("[o0][2:v]overlay=x=1000:y=30", graph)
        self.assertEqual(args[args.index("-map") + 1], "[vout]")


@unittest.skipIf(QApplication is None, "PyQt6 not installed")
class RenderAndExportTests(TempDirTest):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_text_scales_with_the_frame(self):
        from UI.elements import ElementRenderer
        e = Element("text", text="Ace!", size=0.1)
        small, big = ElementRenderer.image(e, 640, 360), ElementRenderer.image(e, 1920, 1080)
        self.assertGreater(big.height(), small.height() * 2.5)
        self.assertFalse(small.isNull())

    def test_missing_image_shows_a_placeholder(self):
        from UI.elements import ElementRenderer
        img = ElementRenderer.image(Element("image", path=str(self.tmp / "gone.png"), size=0.2), 1000, 500)
        self.assertEqual(img.width(), 200)

    @unittest.skipUnless(FF, "ffmpeg not found")
    def test_image_element_is_burned_into_the_export_only_while_it_shows(self):
        from PyQt6.QtGui import QColor, QImage
        from UI.elements import ElementRenderer
        video = self.tmp / "black.mp4"
        subprocess.run([FF.exe, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=black:size=320x180:rate=10",
                        "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
        logo = self.tmp / "red.png"
        red = QImage(40, 40, QImage.Format.Format_ARGB32)
        red.fill(QColor("#ff0000"))
        red.save(str(logo))
        p = EditProject("Logo")
        p.add_source(video, 4)
        p.add_clip(video, 0, 4)
        p.add_element(Element("image", start=0, end=2, x=0.5, y=0.5, size=0.25, path=str(logo)))
        folder = self.tmp / "elements"
        folder.mkdir()
        out = ProjectExporter(FF).export(p, self.tmp / "out.mp4", "x264",
                                         render_overlays=lambda w, h: ElementRenderer.overlays(p, w, h, folder))
        for at, expect_red in ((1.0, True), (3.0, False)):
            frame = self.tmp / f"f{at}.png"
            FF.extract_frame(out, frame, at, width=320)
            pixel = QImage(str(frame)).pixelColor(160, 90)
            self.assertEqual(pixel.red() > 150 and pixel.green() < 80, expect_red, f"at {at}s: {pixel.name()}")


@unittest.skipIf(QApplication is None, "PyQt6 not installed")
class ZoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_zoom_widens_the_timeline_within_limits(self):
        from UI.edit_timeline import EditTimeline, TimelineView
        from UI.frames import FrameCache
        view = TimelineView(EditTimeline(FrameCache(Path("."), None)))
        view.resize(800, view.height())
        view.show()
        self.app.processEvents()
        base = view.timeline.width()
        view.zoom_by(4)
        self.assertEqual(view.timeline.width(), int(view.viewport().width() * 4))
        view.set_zoom(10_000)
        self.assertLessEqual(view.timeline.width(), TimelineView.MAX_WIDTH)
        view.set_zoom(0.1)
        self.assertEqual(view.timeline.width(), base)          # never narrower than the view


@unittest.skipIf(QApplication is None, "PyQt6 not installed")
class HighlightFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    MARKERS = [{"type": "kill", "involves_me": True}, {"type": "multikill", "involves_me": True},
               {"type": "death", "involves_me": True}, {"type": "objective", "involves_me": True},
               {"type": "kill", "involves_me": False}, {"type": "game_end", "involves_me": False}]

    def test_chips_count_each_kind(self):
        from UI.highlight_filter import HighlightFilter
        f = HighlightFilter()
        f.set_markers(self.MARKERS)
        self.assertEqual([c.text() for c in f._chips.values()],
                         ["Kills  2", "Deaths  1", "Objectives  1", "Everyone  1"])

    def test_hiding_and_solo(self):
        from UI.highlight_filter import HighlightFilter
        f = HighlightFilter()
        f.set_markers(self.MARKERS)
        mine = self.MARKERS[:4]
        self.assertEqual(sum(map(f.accepts, self.MARKERS)), 4)   # others + match end hidden by default
        f._chips["deaths"].setChecked(False)
        self.assertEqual([m["type"] for m in mine if f.accepts(m)], ["kill", "multikill", "objective"])
        f.solo("objectives")
        self.assertEqual([m["type"] for m in mine if f.accepts(m)], ["objective"])
        f.solo("objectives")                                     # again: everything back
        self.assertEqual(sum(map(f.accepts, mine)), 4)
        f._chips["everyone"].setChecked(True)
        self.assertTrue(f.accepts(self.MARKERS[4]))
        self.assertFalse(f.accepts(self.MARKERS[5]))             # match end is never a list item


if __name__ == "__main__":
    unittest.main()
