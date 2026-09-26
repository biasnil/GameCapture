"""Finds screen-capture ffmpeg processes left behind by a GameCapture that crashed or was killed.

They keep recording the desktop in the background (and keep the file locked) until stopped."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("gamecapture.orphans")


class OrphanCaptures:
    @staticmethod
    def find(ffmpeg_exe: str) -> list:
        try:
            import psutil
        except ImportError:
            return []
        ours = str(Path(ffmpeg_exe).resolve()).lower()
        found = []
        for proc in psutil.process_iter(["name", "exe", "cmdline"]):
            try:
                exe = (proc.info.get("exe") or "").lower()
                cmd = " ".join(proc.info.get("cmdline") or [])
            except Exception:
                continue
            if exe and Path(exe).resolve().as_posix().lower() == Path(ours).as_posix().lower() and "ddagrab" in cmd:
                found.append(proc)
        return found

    @classmethod
    def cleanup(cls, ffmpeg_exe: str) -> list[str]:
        """Stop orphaned captures. The .mkv they wrote stays playable (MKV survives a hard stop)."""
        stopped = []
        for proc in cls.find(ffmpeg_exe):
            output = (proc.info.get("cmdline") or ["?"])[-1]
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    continue
            log.warning("Stopped a leftover recording from a previous run: %s", Path(output).name)
            stopped.append(output)
        return stopped
