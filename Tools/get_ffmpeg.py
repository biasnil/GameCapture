"""One-time setup: download a Windows FFmpeg build (with ddagrab + NVENC/AMF/QSV) into Bin/."""
from __future__ import annotations

import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from Core.paths import Paths  # noqa: E402


class FFmpegDownloader:
    """Downloads a *stable release* build, not the daily 'master' one: master changes every day and
    once removed an option GameCapture used, which stopped every recording."""
    BASE = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    CANDIDATES = ("ffmpeg-n8.1-latest-win64-gpl-8.1.zip",    # tried in order
                  "ffmpeg-n9.0-latest-win64-gpl-9.0.zip",
                  "ffmpeg-master-latest-win64-gpl.zip")

    def __init__(self, target_dir: Path = Paths.BIN) -> None:
        self.target_dir = target_dir

    @staticmethod
    def _progress(blocks: int, block_size: int, total: int) -> None:
        if total > 0:
            print(f"\rDownloading ffmpeg... {min(100, blocks * block_size * 100 // total)}%", end="", flush=True)

    def run(self) -> int:
        self.target_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "ffmpeg.zip"
            for name in self.CANDIDATES:
                try:
                    print(f"Downloading {name}")
                    urllib.request.urlretrieve(self.BASE + name, zip_path, self._progress)
                    print()
                    break
                except urllib.error.HTTPError as exc:
                    print(f"\n  not available ({exc.code}) - trying the next one")
            else:
                print("No FFmpeg build could be downloaded")
                return 1
            with zipfile.ZipFile(zip_path) as zf:
                member = next((n for n in zf.namelist() if n.endswith("/bin/ffmpeg.exe")), None)
                if member is None:
                    print("ffmpeg.exe not found in the archive")
                    return 1
                with zf.open(member) as src, open(self.target_dir / "ffmpeg.exe", "wb") as dst:
                    shutil.copyfileobj(src, dst)
        print(f"Installed {self.target_dir / 'ffmpeg.exe'}")
        return 0


if __name__ == "__main__":
    sys.exit(FFmpegDownloader().run())