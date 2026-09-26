"""Recover a match's highlights from a leftover .chapters.txt (when saving the highlights failed).

  python Tools\\recover_highlights.py "C:\\Users\\you\\Videos\\GameCapture\\LoL_2026-09-26_17-40-28.chapters.txt"
  python Tools\\recover_highlights.py "C:\\Users\\you\\Videos\\GameCapture\\LoL_..._Ashe_Win_10-8-10.mp4"

Champion, result and K/D/A are read from the saved .mp4's name when it has them.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from Capture.ffmpeg import FFmpeg  # noqa: E402
from Core.recovery import HighlightRecovery  # noqa: E402


class RecoverHighlightsCLI:
    @staticmethod
    def main(argv: list[str] | None = None) -> int:
        ap = argparse.ArgumentParser(description="Rebuild highlights from a leftover .chapters.txt")
        ap.add_argument("source", type=Path, help="a leftover .chapters.txt, or a recording .mp4 whose .json is missing")
        ap.add_argument("--champion", default=None, help="override the champion (normally read from the .mp4 name)")
        ap.add_argument("--mode", default="", help="game mode id, e.g. CLASSIC or ARAM (optional)")
        ap.add_argument("--overwrite", action="store_true", help="replace an existing .json")
        args = ap.parse_args(argv)
        try:
            if args.source.suffix.lower() in (".mp4", ".mkv"):
                ffmpeg = FFmpeg.locate()
                if ffmpeg is None:
                    print("Error: ffmpeg not found - run Tools\\get_ffmpeg.py")
                    return 1
                r = HighlightRecovery.recover_from_video(args.source, ffmpeg, args.champion, args.mode, args.overwrite)
            else:
                r = HighlightRecovery.recover(args.source, args.champion, args.mode, args.overwrite)
        except (FileNotFoundError, FileExistsError) as exc:
            print(f"Error: {exc}")
            return 1
        print(f"Recovered {r.markers} highlights -> {r.sidecar.name}  ({r.champion})")
        print(f"Result {r.result}, K/D/A {r.kda}")
        if r.duplicate_mkv is not None:
            print(f"\n{r.duplicate_mkv.name} is a duplicate of {r.video.name} (same match, before conversion).")
            print("Delete it from the app (trash icon) or Explorer to free the space.")
        if args.source.suffix.lower() == ".txt":
            print(f"You can now delete {args.source.name}.")
        return 0


if __name__ == "__main__":
    sys.exit(RecoverHighlightsCLI.main())
