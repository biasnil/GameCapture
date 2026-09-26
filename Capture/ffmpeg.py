"""Wrapper around the bundled ffmpeg.exe: encoder profiles, probing, remux, thumbnails."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from Core.paths import Paths

log = logging.getLogger("gamecapture.ffmpeg")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console popups on Windows


@dataclass(frozen=True)
class RateControl:
    mode: str = "quality"      # "quality" (constant quality) or "bitrate" (target kbps)
    quality: int = 21
    bitrate_kbps: int = 12000


@dataclass(frozen=True)
class EncoderProfile:
    """How to drive one encoder, for live capture and for re-encoding files.

    ddagrab hands out GPU (D3D11) frames. NVENC and AMF take them directly (zero-copy);
    QSV needs a hwmap; x264 needs the frames downloaded to system memory. Resizing is
    tried on the GPU first (scalers in order), then on the CPU as a last resort."""
    name: str
    codec: str
    native_filter: str | None
    base_opts: tuple[str, ...]
    quality_opts: tuple[str, ...]
    bitrate_opts: tuple[str, ...]
    file_opts: tuple[str, ...]
    gpu_scalers: tuple[str, ...]
    cpu_scaler: str

    @staticmethod
    def _fill(opts: tuple[str, ...], **values) -> list[str]:
        return [o.format(**values) for o in opts]

    def rate_args(self, rc: RateControl) -> list[str]:
        if rc.mode == "bitrate":
            b = int(rc.bitrate_kbps)
            return self._fill(self.bitrate_opts, b=b, m=int(b * 1.5), buf=b * 2)
        return self._fill(self.quality_opts, q=rc.quality)

    def capture_args(self, fps: int, rc: RateControl, video_filter: str | None) -> list[str]:
        args = ["-vf", video_filter] if video_filter else []
        args += ["-c:v", self.codec, *self.base_opts, *self.rate_args(rc)]
        return args + ["-g", str(fps * 2)]  # keyframe every 2 s -> clean highlight cuts

    def file_args(self, quality: int) -> list[str]:
        return ["-c:v", self.codec, *self._fill(self.file_opts, q=quality)]

    def file_bitrate_args(self, kbps: int) -> list[str]:
        """Re-encode a file at (at most) this bitrate - used to fit clips under a size limit."""
        return ["-c:v", self.codec, *self.base_opts, *self._fill(self.bitrate_opts, b=kbps, m=kbps, buf=kbps * 2)]

    def scalers(self, width: int, height: int) -> list[tuple[str, str]]:
        """(where, filter) candidates to resize captured frames to width x height."""
        out = [("gpu", t.format(w=width, h=height)) for t in self.gpu_scalers]
        return out + [("cpu", self.cpu_scaler.format(w=width, h=height))]


class FFmpeg:
    _CPU_SCALE = "hwdownload,format=bgra,scale={w}:{h}:flags=bilinear"
    ENCODERS: dict[str, EncoderProfile] = {p.name: p for p in (
        EncoderProfile(
            "nvenc", "h264_nvenc", None, ("-preset", "p5", "-tune", "hq"),
            ("-rc", "vbr", "-cq", "{q}", "-b:v", "0"),
            ("-rc", "vbr", "-b:v", "{b}k", "-maxrate", "{m}k", "-bufsize", "{buf}k"),
            ("-preset", "p5", "-rc", "vbr", "-cq", "{q}", "-b:v", "0"),
            ("scale_d3d11={w}:{h}", "hwmap=derive_device=cuda,scale_cuda={w}:{h}"), _CPU_SCALE),
        EncoderProfile(
            "amf", "h264_amf", None, ("-quality", "quality"),
            ("-rc", "cqp", "-qp_i", "{q}", "-qp_p", "{q}", "-qp_b", "{q}"),
            ("-rc", "vbr_peak", "-b:v", "{b}k", "-maxrate", "{m}k"),
            ("-quality", "quality", "-rc", "cqp", "-qp_i", "{q}", "-qp_p", "{q}", "-qp_b", "{q}"),
            ("scale_d3d11={w}:{h}",), _CPU_SCALE),
        EncoderProfile(
            "qsv", "h264_qsv", "hwmap=derive_device=qsv,format=qsv", ("-preset", "slow"),
            ("-global_quality", "{q}"), ("-b:v", "{b}k", "-maxrate", "{m}k"),
            ("-preset", "slow", "-global_quality", "{q}"),
            ("hwmap=derive_device=qsv,format=qsv,scale_qsv=w={w}:h={h}",), _CPU_SCALE + ",format=nv12"),
        EncoderProfile(
            "x264", "libx264", "hwdownload,format=bgra,format=yuv420p", ("-preset", "veryfast"),
            ("-crf", "{q}"), ("-b:v", "{b}k", "-maxrate", "{m}k", "-bufsize", "{buf}k"),
            ("-preset", "medium", "-crf", "{q}"),
            (), _CPU_SCALE + ",format=yuv420p"),
    )}
    AUTO_ORDER = ("nvenc", "amf", "qsv", "x264")
    # Only the picture and sound. Recordings also carry their chapters as a data track that spans
    # the whole match; copying it into a clip makes players think the clip is match-length.
    AV_ONLY = ("-map", "0:v:0", "-map", "0:a?", "-dn")      # keeps every audio track (clips)
    AV_MIX = ("-map", "0:v:0", "-map", "0:a:0?", "-dn")     # picture + the mix track only (Discord)

    def __init__(self, exe: str) -> None:
        self.exe = exe

    # ---------- construction ----------

    @classmethod
    def locate(cls, configured: str = "") -> "FFmpeg | None":
        """config path -> Bin/ffmpeg.exe -> PATH."""
        for candidate in (configured, str(Paths.BIN / "ffmpeg.exe")):
            if candidate and Path(candidate).is_file():
                return cls(candidate)
        found = shutil.which("ffmpeg")
        return cls(found) if found else None

    @classmethod
    def encoder(cls, name: str) -> EncoderProfile:
        return cls.ENCODERS.get(name, cls.ENCODERS["x264"])

    # ---------- process helpers ----------

    def run(self, args: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
        return subprocess.run([self.exe, "-hide_banner", *args], capture_output=True, text=True,
                              timeout=timeout, creationflags=NO_WINDOW, encoding="utf-8", errors="replace")

    def popen(self, args: list[str], **kwargs) -> subprocess.Popen:
        return subprocess.Popen([self.exe, "-hide_banner", *args], creationflags=NO_WINDOW, **kwargs)

    # ---------- queries ----------

    def version(self) -> str:
        lines = self.run(["-version"]).stdout.splitlines()
        return lines[0] if lines else "unknown"

    def has_ddagrab(self) -> bool:
        return " ddagrab " in self.run(["-filters"]).stdout

    def duration(self, video: Path) -> float | None:
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", self.run(["-i", str(video)]).stderr)
        return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None

    def has_audio(self, video: Path) -> bool:
        return "Audio:" in self.run(["-i", str(video)]).stderr

    @staticmethod
    def capture_input(monitor: int, fps: int, draw_mouse: bool) -> list[str]:
        return ["-f", "lavfi", "-i", f"ddagrab=output_idx={monitor}:framerate={fps}:draw_mouse={int(draw_mouse)}"]

    def probe_encoder(self, name: str, monitor: int, video_filter: str | None = None,
                      use_native_filter: bool = True) -> tuple[bool, str]:
        """Encode a few real captured frames to nowhere; proves capture + filter + encoder work."""
        profile = self.encoder(name)
        vf = profile.native_filter if use_native_filter else video_filter
        args = ["-loglevel", "error", *self.capture_input(monitor, 30, False), "-frames:v", "3",
                *profile.capture_args(30, RateControl(), vf), "-f", "null", "-"]
        try:
            r = self.run(args)
        except subprocess.TimeoutExpired:
            return False, "timed out"
        return r.returncode == 0, (r.stderr.strip().splitlines() or [""])[-1]

    def probe_filter(self, name: str, monitor: int, video_filter: str) -> bool:
        ok, err = self.probe_encoder(name, monitor, video_filter, use_native_filter=False)
        if not ok:
            log.debug("Filter '%s' unavailable with %s: %s", video_filter, name, err)
        return ok

    def screen_size(self, monitor: int) -> tuple[int, int] | None:
        """Pixel size of a monitor as ddagrab sees it."""
        try:
            r = self.run([*self.capture_input(monitor, 10, False), "-frames:v", "1", "-f", "null", "-"])
        except subprocess.TimeoutExpired:
            return None
        m = re.search(r"Stream #0:0.*?Video:.*?(\d{3,5})x(\d{3,5})", r.stderr)
        return (int(m[1]), int(m[2])) if m else None

    def pick_encoder(self, preferred: str, monitor: int) -> str | None:
        order = list(self.AUTO_ORDER)
        if preferred != "auto":
            if preferred in self.ENCODERS:
                order = [preferred] + [e for e in order if e != preferred]
            else:
                log.warning("Unknown encoder '%s' in config - using auto", preferred)
        for name in order:
            ok, err = self.probe_encoder(name, monitor)
            if ok:
                if preferred not in ("auto", name):
                    log.warning("Encoder '%s' failed - fell back to '%s'", preferred, name)
                return name
            log.debug("Encoder %s unavailable: %s", name, err)
        return None

    # ---------- file operations ----------

    def remux(self, src: Path, dst: Path, chapters_file: Path | None = None) -> bool:
        """Stream copy (no re-encode) into dst, optionally embedding chapters from an FFMETADATA file."""
        args = ["-loglevel", "error", "-y", "-i", str(src)]
        if chapters_file is not None:
            args += ["-f", "ffmetadata", "-i", str(chapters_file),
                     "-map", "0", "-map_metadata", "1", "-map_chapters", "1"]
        else:
            args += ["-map", "0"]  # every stream - without this ffmpeg keeps only ONE audio track
        args += ["-c", "copy"]
        if dst.suffix.lower() == ".mp4":
            args += ["-movflags", "+faststart"]  # index at the front for instant seeking
        args.append(str(dst))
        r = self.run(args, timeout=600)
        if r.returncode != 0:
            log.error("Remux failed: %s", r.stderr.strip()[-300:])
        return r.returncode == 0 and dst.exists()

    def extract_frame(self, video: Path, out: Path, at: float, width: int = 320) -> bool:
        try:
            self.run(["-loglevel", "error", "-y", "-ss", f"{max(at, 0):.2f}", "-i", str(video),
                      "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(out)], timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return out.exists() and out.stat().st_size > 0
