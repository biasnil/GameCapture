"""Editor elements (text / images on the video), timeline zoom and the Sessions highlight filters."""
import os
import subprocess
import unittest
from pathlib import Path

from helpers import TempDirTest

from Capture.ffmpeg import FFmpeg
from Core.editor import MIN_CLIP, EditProject, Element, Overlay, ProjectExporter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # off-screen Qt doesn't look in the Windows font folder by itself
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
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


@unittest.skipIf(QApplication is None, "PyQt6 not installed")
class FontAndAnimationTests(TempDirTest):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_font_style_is_saved_and_used(self):
        from UI.elements import ElementRenderer
        e = Element("text", text="Pentakill", font="DejaVu Serif", bold=False, italic=True)
        again = EditProject.from_dict({**EditProject(elements=[e]).to_dict()}).elements[0]
        self.assertEqual((again.font, again.bold, again.italic), ("DejaVu Serif", False, True))
        font = ElementRenderer.font(e, 40)
        self.assertEqual((font.family(), font.bold(), font.italic(), font.pixelSize()),
                         ("DejaVu Serif", False, True, 40))
        bold = ElementRenderer.image(Element("text", text="Pentakill", bold=True), 1280, 720)
        thin = ElementRenderer.image(Element("text", text="Pentakill", bold=False), 1280, 720)
        self.assertGreater(bold.width(), thin.width())

    def make_gif(self) -> Path:
        """1 s red then 1 s blue, looping."""
        gif = self.tmp / "blink.gif"
        subprocess.run([FF.exe, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=red:size=40x40:rate=1:d=1",
                        "-f", "lavfi", "-i", "color=blue:size=40x40:rate=1:d=1",
                        "-filter_complex", "[0][1]concat=n=2:v=1,split[a][b];[a]palettegen[p];[b][p]paletteuse",
                        "-loop", "0", str(gif)], check=True)
        return gif

    @unittest.skipUnless(FF, "ffmpeg not found")
    def test_gif_frames_follow_time_and_loop(self):
        from UI.elements import ElementRenderer
        e = Element("image", path=str(self.make_gif()))
        self.assertTrue(ElementRenderer.is_animated(e))
        self.assertEqual(ElementRenderer.loop_ms(e), 2000)
        self.assertEqual([ElementRenderer.frame_index(e, t) for t in (0.2, 1.2, 2.2, 3.5)], [0, 1, 0, 1])
        self.assertFalse(ElementRenderer.is_animated(Element("image", path=str(self.tmp / "none.gif"))))

    @unittest.skipUnless(FF, "ffmpeg not found")
    def test_gif_animates_in_the_export(self):
        from PyQt6.QtGui import QImage
        from UI.elements import ElementRenderer
        video = self.tmp / "black.mp4"
        subprocess.run([FF.exe, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=black:size=320x180:rate=10",
                        "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
        p = EditProject()
        p.add_source(video, 6)
        p.add_clip(video, 0, 6)
        p.add_element(Element("image", start=1, end=5, x=0.5, y=0.5, size=0.3, path=str(self.make_gif())))
        folder = self.tmp / "el"
        folder.mkdir()
        overlays = ElementRenderer.overlays(p, 320, 180, folder, fps=10)
        self.assertEqual((overlays[0].fps, len(list(folder.glob("element_00_*.png")))), (10, 20))
        out = ProjectExporter(FF).export(p, self.tmp / "out.mp4", "x264", render_overlays=lambda w, h: overlays)
        self.assertAlmostEqual(FF.duration(out), 6, delta=0.3)   # the endless loop doesn't make it longer
        seen = []
        for at in (0.5, 1.5, 2.5, 3.5, 5.5):   # before, red, blue, red again (looped), after
            frame = self.tmp / f"f{at}.png"
            FF.extract_frame(out, frame, at, width=320)
            c = QImage(str(frame)).pixelColor(160, 90)
            seen.append("red" if c.red() > 150 and c.blue() < 90 else "blue" if c.blue() > 150 and c.red() < 90
                        else "none")
        self.assertEqual(seen, ["none", "red", "blue", "red", "none"])
