"""Owns the ffmpeg capture process: start / stop / toggle, file naming, chapters, remux, health checks."""
from __future__ import annotations

import json
import logging
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from Capture.audio import LoopbackAudio
from Capture.chapters import ChapterWriter
from Capture.ffmpeg import FFmpeg
from Capture.pipeline import VideoPipeline
from Core.config import AppConfig
from Core.formatting import Format

log = logging.getLogger("gamecapture.recorder")


class Recorder:
    def __init__(self, cfg: AppConfig, ffmpeg: FFmpeg, encoder: str,
                 pipeline: VideoPipeline | None = None) -> None:
        self.cfg = cfg
        self.ffmpeg = ffmpeg
        self.encoder = encoder
        self.pipeline = pipeline or VideoPipeline(ffmpeg, encoder)
        # Hotkey worker, console, match watcher and main loop all call in.
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._audio: LoopbackAudio | None = None
        self._mkv: Path | None = None
        self._started = 0.0
        self._id: str | None = None
        self._ff_log_path = cfg.resolved_log_dir() / "ffmpeg.log"
        self._ff_log = None
        self._finalizing = False
        self._busy: set[Path] = set()             # being written / converted right now
        self._pending_file = cfg.recording.resolved_output_dir() / ".pending_deletes.json"
        self._pending_deletes: set[Path] = self._load_pending()  # leftovers Windows wouldn't let us delete yet
        self._held: set[Path] = set()              # finished, but a caller is still post-processing
        self._bookmarks: list[float] = []          # video times marked with the Bookmark hotkey
        self._audio_apps: tuple[str, ...] = ()     # the recorded game's executables (isolated audio)

    # ---------- state ----------

    @property
    def is_recording(self) -> bool:
        return self._proc is not None

    @property
    def current_id(self) -> str | None:
        """Changes with every new recording, so callers can tell recordings apart."""
        return self._id if self._proc is not None else None

    @property
    def is_finalizing(self) -> bool:
        """Recording stopped but the file is still being converted / renamed / post-processed."""
        return self._finalizing or bool(self._held)

    @property
    def busy_files(self) -> set[Path]:
        """Files the library must not show or open yet: the recording in progress, files being
        converted, and leftovers waiting to be deleted. Opening them locks them on Windows."""
        return set(self._busy) | set(self._pending_deletes) | set(self._held)

    def release(self, path: Path | None) -> None:
        """Post-processing of a held file is done: let the library show it."""
        if path is not None:
            self._held.discard(path)

    def discard(self, path: Path) -> None:
        """Delete a file GameCapture made (retries later if Windows has it locked)."""
        self._delete(path)

    def elapsed(self) -> float:
        """Seconds into the current video (about +-0.5 s: ffmpeg's startup time)."""
        return time.monotonic() - self._started if self._proc is not None else 0.0

    # ---------- control ----------

    def bookmark(self) -> float | None:
        """Mark 'now' in the current recording. Returns the video time, or None if not recording."""
        with self._lock:
            if self._proc is None:
                return None
            t = self.elapsed()
            self._bookmarks.append(t)
            log.info("[BOOKMARK] %s", Format.duration(t))
            return t

    def take_bookmarks(self) -> list[float]:
        """Bookmarks of the recording that is running / just stopped (cleared once taken)."""
        marks, self._bookmarks = self._bookmarks, []
        return marks

    def start(self, prefix: str | None = None, audio_apps=None) -> bool:
        """prefix: filename prefix for this recording (per game); default from config.
        audio_apps: exe names of the game being recorded (for isolated game audio).
        If audio can't be connected, records without sound rather than not at all."""
        self._audio_apps = tuple(audio_apps or ())
        with self._lock:
            if self._proc is not None:
                log.info("Already recording")
                return False
            want_audio = self.cfg.capture.audio
            result = self._launch(prefix, use_audio=want_audio)
            if want_audio and result != "ok":
                # Sound is the part most likely to break (devices, ffmpeg versions). A recording
                # without sound beats no recording; if the video side is broken this fails fast too.
                log.warning("Retrying WITHOUT sound - see the Log for why sound failed")
                result = self._launch(prefix, use_audio=False)
            if result == "ok":
                self.last_error = None
            return result == "ok"

    last_error: str | None = None  # why the last start failed (shown in the app)

    def _launch(self, prefix: str | None, use_audio: bool) -> str:
        """One attempt. Returns "ok", "audio" (only the sound failed - worth retrying without it)
        or "failed" (ffmpeg itself failed - the reason is in ffmpeg.log)."""
        cap, rec = self.cfg.capture, self.cfg.recording
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._mkv = rec.resolved_output_dir() / f"{prefix or rec.filename_prefix}_{stamp}.mkv"
        self._bookmarks = []
        self._busy = {self._mkv}

        audio = self._open_audio() if use_audio else None
        tracks = list(getattr(audio, "track_names", ["Audio"])) if audio else []
        ports = [self._free_port() for _ in tracks]
        # No -thread_queue_size here: newer ffmpeg reads each input on its own thread and rejects
        # that option on inputs ("cannot be applied to input url"), which stopped every recording.
        audio_in: list[str] = []
        audio_out: list[str] = []
        for i, port in enumerate(ports):
            audio_in += ["-f", "s16le", "-ar", str(audio.rate), "-ac", str(audio.channels),
                         "-probesize", "32", "-analyzeduration", "0",   # raw PCM: nothing to probe
                         "-i", f"tcp://127.0.0.1:{port}?listen=1"]
            audio_out += ["-map", f"{i + 1}:a"]
        if tracks:
            audio_out += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
            if len(tracks) > 1:
                for i, name in enumerate(tracks):   # title for MKV/editors, handler_name for MP4
                    audio_out += [f"-metadata:s:a:{i}", f"title={name}", f"-metadata:s:a:{i}", f"handler_name={name}"]
                audio_out += ["-disposition:a:0", "default"]
        args = ["-loglevel", "warning", "-y",
                *FFmpeg.capture_input(cap.monitor, cap.fps, cap.draw_mouse), *audio_in,
                "-map", "0:v", *self.pipeline.video_args(cap),
                *audio_out, str(self._mkv)]

        self._ff_log = open(self._ff_log_path, "a", encoding="utf-8")
        self._ff_log.write(f"\n===== {stamp}{'' if audio else ' (no audio)'}\n{subprocess.list2cmdline(args)}\n")
        self._ff_log.flush()
        self._started = time.monotonic()
        self._id = stamp
        self._proc = self.ffmpeg.popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._ff_log)
        self._audio = audio
        if audio:
            proc = self._proc
            try:
                if hasattr(audio, "start_many"):
                    audio.start_many(ports, alive=lambda: proc.poll() is None)
                else:
                    audio.start(ports[0], alive=lambda: proc.poll() is None)
            except Exception as exc:
                died = proc.poll() is not None
                if died:  # ffmpeg itself refused: say what ffmpeg said
                    self._fail(f"ffmpeg stopped before recording started:\n{self._log_tail()}")
                    return "failed"
                log.warning("System audio couldn't be connected (%s)", exc)
                self._discard_attempt()
                return "audio"

        time.sleep(0.5)  # catch instant failures (bad monitor index, encoder busy...)
        if self._proc.poll() is not None:
            self._fail(f"ffmpeg stopped right after starting:\n{self._log_tail()}")
            return "failed"
        log.info("[REC] Recording -> %s%s", self._mkv.name, "" if audio else " (no sound)")
        return "ok"

    def _fail(self, message: str) -> None:
        self.last_error = message.splitlines()[-1] if message else "unknown error"
        log.error(message)
        self._discard_attempt()

    def _discard_attempt(self) -> None:
        """Throw away a start that didn't work: stop ffmpeg, drop the empty file, reset state."""
        proc, audio, ff_log, mkv = self._proc, self._audio, self._ff_log, self._mkv
        self._proc = self._audio = self._ff_log = None
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        for closer in (lambda: audio and audio.stop(), lambda: proc and proc.stdin and proc.stdin.close(),
                       lambda: ff_log and ff_log.close()):
            try:
                closer()
            except Exception:
                pass
        try:
            if mkv is not None and mkv.exists() and mkv.stat().st_size < 1024:
                mkv.unlink()
        except OSError:
            pass
        self._busy.clear()

    def stop(self, tag: str | None = None, chapters: list[tuple[float, str]] | None = None,
             hold: bool = False) -> Path | None:
        """tag is appended to the filename; chapters are (seconds, title) pairs.
        hold=True keeps the finished file hidden until release() (for post-processing)."""
        with self._lock:
            if self._proc is None:
                log.info("Not recording")
                return None
            log.info("[STOP] Stopping after %s", Format.duration(self.elapsed()))
            path = self._teardown(graceful=True, tag=tag, chapters=chapters, hold=hold)
            return path

    def toggle(self) -> None:
        with self._lock:
            self.stop() if self._proc is not None else self.start()

    def log_status(self) -> None:
        with self._lock:
            log.info("Status: %s", f"RECORDING ({Format.duration(self.elapsed())})" if self._proc else "idle")

    def check_alive(self) -> None:
        """Called from the main loop: salvage the file if ffmpeg dies mid-recording,
        and retry deleting leftovers that were in use."""
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._retry_pending_deletes()
            if self._proc is not None and self._proc.poll() is not None:
                log.error("ffmpeg stopped unexpectedly (exit %s):\n%s", self._proc.returncode, self._log_tail())
                self._teardown(graceful=False)
        finally:
            self._lock.release()

    # ---------- internals ----------

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _open_audio(self):
        """Plain 'everything you hear' uses the proven single-source path; anything else
        (mic, per-app isolation, separate tracks, volumes) goes through the mixer."""
        cap = self.cfg.capture
        if not cap.audio:
            return None
        simple = (cap.audio_mode == "system" and not cap.mic and not cap.audio_tracks
                  and cap.system_volume == 100)
        try:
            return LoopbackAudio() if simple else self._open_mixer()
        except Exception as exc:
            log.warning("No audio (%s) - recording video only", exc)
            return None

    def _open_mixer(self):
        from Capture.audio_mixer import AudioMixer
        from Capture.audio_sources import AppAudioSource, MicrophoneSource, SystemAudioSource
        cap = self.cfg.capture
        sources = []
        if cap.audio_mode == "isolated":
            if cap.game_audio:
                names = self._audio_apps or self._running_game_processes()
                if names:
                    sources.append(AppAudioSource("Game", names, cap.game_volume / 100))
                else:
                    log.info("Game audio: no known game is running (manual recording)")
            for app in cap.apps:
                if app.get("enabled", True) and app.get("exe"):
                    label = app["exe"].rsplit(".", 1)[0]
                    sources.append(AppAudioSource(label, (app["exe"],), app.get("volume", 100) / 100))
        else:
            sources.append(SystemAudioSource(cap.system_volume / 100))
        if cap.mic:
            sources.append(MicrophoneSource(cap.mic_volume / 100))
        if not sources:
            raise RuntimeError("no audio sources selected")
        mixer = AudioMixer(sources, separate_tracks=cap.audio_tracks)
        mixer.open_sources()
        log.info("Audio: %s%s", mixer.name, f" (tracks: {', '.join(mixer.track_names)})"
                 if mixer.separate_tracks else "")
        return mixer

    @staticmethod
    def _running_game_processes() -> tuple[str, ...]:
        """Manual recording in isolated mode: use whichever supported game is running."""
        try:
            from Games.processes import ProcessSnapshot
            from Games.registry import GameRegistry
            running = ProcessSnapshot.names()
            for game in GameRegistry.supported():
                if any(p.lower() in running for p in game.processes):
                    return game.processes
        except Exception:
            pass
        return ()

    def _teardown(self, graceful: bool, tag: str | None = None,
                  chapters: list[tuple[float, str]] | None = None, hold: bool = False) -> Path | None:
        duration = self.elapsed()
        proc = self._proc
        if graceful and proc.poll() is None:
            try:
                proc.stdin.write(b"q")  # ffmpeg's clean-exit key: finalises the file
                proc.stdin.flush()
            except OSError:
                pass
        try:
            proc.wait(timeout=20 if graceful else 3)
        except subprocess.TimeoutExpired:
            log.warning("ffmpeg did not exit - killing it")
            proc.kill()
            proc.wait()
        if self._audio is not None:
            self._audio.stop()
        for handle in (proc.stdin, self._ff_log):
            try:
                handle.close()
            except Exception:
                pass
        self._finalizing = True  # set before _proc is cleared: no moment where we look "done"
        self._proc = self._audio = self._ff_log = None
        return self._finish(tag, chapters, duration, hold)

    def _finish(self, tag: str | None, chapters: list[tuple[float, str]] | None, duration: float,
                hold: bool = False) -> Path | None:
        """Convert/rename the finished .mkv. Never raises: a failure here must not lose the match."""
        self._finalizing = True
        final = None
        try:
            final = self._finalize(self._mkv, tag, chapters, duration)
        except Exception:
            log.exception("Finishing the recording failed - the .mkv is kept and still playable")
            final = self._mkv if self._mkv is not None and self._mkv.exists() else None
        finally:
            if hold and final is not None:
                self._held.add(final)  # added before busy clears: never visible half-processed
            self._busy.clear()
            self._finalizing = False
        return final

    def _finalize(self, mkv: Path | None, tag: str | None,
                  chapters: list[tuple[float, str]] | None, duration: float) -> Path | None:
        if mkv is None or not mkv.exists() or mkv.stat().st_size == 0:
            log.warning("No usable output file: %s", mkv)
            return None
        ext = ".mp4" if self.cfg.recording.final_format.lower() == "mp4" else ".mkv"
        dst = mkv.with_name(mkv.stem + (f"_{Format.safe_filename(tag)}" if tag else "") + ext)
        meta = ChapterWriter.write(mkv, chapters, duration) if chapters else None
        self._busy |= {dst}

        final = mkv
        if ext == ".mkv" and meta is None:
            if dst != mkv:
                try:
                    mkv.rename(dst)
                    final = dst
                except OSError as exc:
                    log.warning("Could not rename to %s (%s) - keeping %s", dst.name, exc, mkv.name)
        else:
            target = dst if dst != mkv else mkv.with_name(mkv.stem + ".tmp.mkv")
            self._busy |= {target}
            if self.ffmpeg.remux(mkv, target, meta):
                if target != dst:
                    target.replace(dst)  # same name: swap in the chaptered copy
                    final = dst
                else:
                    final = dst
                    if ext == ".mkv" or not self.cfg.recording.keep_mkv:
                        self._delete(mkv)
            else:
                log.warning("Kept the .mkv instead (still playable)")
        if meta is not None:
            self._delete(meta)
        log.info("Saved %s (%.1f MB)", final, final.stat().st_size / 1_048_576)
        return final

    def _delete(self, path: Path, attempts: int = 6) -> None:
        """Delete, tolerating Windows file locks (a player or thumbnailer may have it open).
        If it's still locked, hide it and keep retrying from check_alive()."""
        for _ in range(attempts):
            try:
                path.unlink(missing_ok=True)
                return
            except PermissionError:
                time.sleep(0.5)
        log.warning("%s is in use by another program - will delete it when it's free", path.name)
        self._pending_deletes.add(path)
        self._save_pending()

    def _retry_pending_deletes(self) -> None:
        changed = False
        for path in list(self._pending_deletes):
            try:
                path.unlink(missing_ok=True)
                self._pending_deletes.discard(path)
                changed = True
                log.info("Deleted leftover %s", path.name)
            except PermissionError:
                pass
        if changed:
            self._save_pending()

    def _load_pending(self) -> set[Path]:
        """Leftovers queued by a previous run stay hidden and still get deleted."""
        try:
            return {Path(p) for p in json.loads(self._pending_file.read_text(encoding="utf-8"))}
        except (OSError, ValueError, TypeError):
            return set()

    def _save_pending(self) -> None:
        try:
            if self._pending_deletes:
                self._pending_file.write_text(json.dumps(sorted(map(str, self._pending_deletes))), encoding="utf-8")
            else:
                self._pending_file.unlink(missing_ok=True)
        except OSError:
            pass

    def _log_tail(self, lines: int = 8) -> str:
        try:
            text = self._ff_log_path.read_text(encoding="utf-8", errors="replace")
            return "\n".join(text.strip().splitlines()[-lines:])
        except OSError:
            return "(no ffmpeg log)"
