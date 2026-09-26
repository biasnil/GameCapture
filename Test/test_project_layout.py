"""Guards the project rules: max one folder deep, every UI icon exists as SVG, app icon is raster."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IGNORED = {".git", ".venv", "venv", "__pycache__", ".idea", ".vscode", "Logs"}


class LayoutTests(unittest.TestCase):
    def test_at_most_one_subfolder_deep(self):
        for folder in ROOT.iterdir():
            if not folder.is_dir() or folder.name in IGNORED:
                continue
            nested = [p.name for p in folder.iterdir() if p.is_dir() and p.name not in IGNORED]
            self.assertEqual(nested, [], f"{folder.name}/ contains sub-folders: {nested}")

    def test_every_icon_used_in_code_exists(self):
        assets = ROOT / "Assets"
        code = "\n".join(p.read_text(encoding="utf-8") for d in ("UI", "Theme") for p in (ROOT / d).glob("*.py"))
        names = set(re.findall(r'"((?:nav|marker)_[a-z_]+)"', code))
        names |= set(re.findall(r'(?:IconButton|IconTextButton|Icons\.(?:icon|pixmap))\(\s*"([a-z_]+)"', code))
        missing = sorted(n for n in names if not (assets / f"{n}.svg").exists())
        self.assertEqual(missing, [])

    def test_app_icon_is_raster(self):
        self.assertTrue((ROOT / "Assets" / "app_icon.png").exists())
        self.assertTrue((ROOT / "Assets" / "app_icon.ico").exists())
        self.assertFalse((ROOT / "Assets" / "app_icon.svg").exists())
