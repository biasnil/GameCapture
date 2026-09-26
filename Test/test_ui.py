"""UI regressions, run headless (skipped if PyQt6 isn't installed)."""
import os
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None


class _NoThumbs:
    """ThumbnailCache stand-in: never has a thumbnail, never emits."""
    class _Signal:
        def connect(self, _slot):
            pass
    ready = _Signal()

    def get(self, *_args):
        return None


@unittest.skipIf(QApplication is None, "PyQt6 not installed")
class CardStripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def entry(self, name="Ashe"):
        from Core.library import RecordingEntry
        return RecordingEntry(Path(f"{name}.mp4"), {"game": {"champion": name, "result": "Win",
                                                             "kills": 10, "deaths": 8, "assists": 10}},
                              datetime(2026, 9, 26, 18, 55), 100.0)

    def test_empty_text_hidden_once_a_recording_arrives(self):
        """Bug: 'No recordings yet...' stayed painted over the first card."""
        from UI.cards import CardStrip
        strip = CardStrip(_NoThumbs())
        strip.show()
        strip.set_entries([], None)
        self.assertTrue(strip._empty.isVisible())
        strip.set_entries([self.entry()], None)
        self.assertFalse(strip._empty.isVisible())
        self.assertEqual(len(strip.cards), 1)
        strip.set_entries([], None)            # all deleted again -> message comes back
        self.assertTrue(strip._empty.isVisible())

    def test_old_cards_disappear_immediately_on_refresh(self):
        from UI.cards import CardStrip
        strip = CardStrip(_NoThumbs())
        strip.show()
        strip.set_entries([self.entry("Ashe")], None)
        old = list(strip.cards.values())[0]
        strip.set_entries([self.entry("Ahri")], None)
        self.assertFalse(old.isVisible())
