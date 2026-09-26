"""The recording engine (recorder + game watchers + hotkeys), shared by the console and the UI."""
from __future__ import annotations

import logging
from typing import Callable

from Capture.ffmpeg import FFmpeg
from Capture.pipeline import VideoPipeline
from Capture.recorder import Recorder
from Core.condense import HighlightCondenser
from Core.config import AppConfig
from Core.sidecar import Sidecar
from Games.base import GameWatcher
from Games.league_watcher import LeagueMatchWatcher

log = logging.getLogger("gamecapture")


class Engine:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.ffmpeg: FFmpeg | None = None
        self.encoder: str | None = None
        self.recorder: Recorder | None = None
        self.pipeline: VideoPipeline | None = None
        self.watcher: LeagueMatchWatcher | None = None   # League + TFT
        self.watchers: list[GameWatcher] = []
        self.deadlock = None   # DeadlockEnricher (per-match videos after a session)
        self._hotkeys = None

    def prepare(self) -> bool:
        """Find ffmpeg and a working encoder."""
        ffmpeg = FFmpeg.locate(self.cfg.ffmpeg_path)
        if ffmpeg is None:
            log.error("ffmpeg not found. Run:  python Tools\\get_ffmpeg.py")
            return False
        log.info("FFmpeg: %s (%s)", ffmpeg.exe, ffmpeg.version())
        from Capture.orphans import OrphanCaptures
        OrphanCaptures.cleanup(ffmpeg.exe)  # a crashed run may have left one recording in the background
        if not ffmpeg.has_ddagrab():
            log.error("This ffmpeg build has no 'ddagrab' screen capture. Run Tools\\get_ffmpeg.py for one that does.")
            return False
        log.info("Probing encoders on monitor %d...", self.cfg.capture.monitor)
        encoder = ffmpeg.pick_encoder(self.cfg.capture.encoder, self.cfg.capture.monitor)
        if encoder is None:
            log.error("No encoder could capture monitor %d - check 'monitor' in Settings", self.cfg.capture.monitor)
            return False
        if encoder == "x264":
            log.warning("Using SOFTWARE encoding (x264) - expect an FPS hit in game. Update GPU drivers?")
        else:
            log.info("Encoder: %s (%s) - hardware encoding OK", encoder, FFmpeg.encoder(encoder).codec)
        self.ffmpeg, self.encoder = ffmpeg, encoder
        self.pipeline = VideoPipeline(ffmpeg, encoder)
        self.pipeline.plan(self.cfg.capture)  # probe resizing now, not when the match starts
        return True

    def auto_enabled(self, game_id: str = "league") -> bool:
        """Checked live by the watchers, so toggling in Settings needs no restart."""
        return self.cfg.auto.enabled and self.cfg.game(game_id).enabled

    def build_watchers(self) -> list[GameWatcher]:
        from Games.cs2 import CS2Installer, CS2Watcher
        from Games.processes import ProcessMonitor
        from Games.registry import GameRegistry
        from Games.session_watcher import ProcessSessionWatcher
        enabled = self.auto_enabled
        try:
            from Games.processes import LeagueProcesses
            processes = LeagueProcesses()
        except ImportError:
            log.warning("psutil not installed - Session mode and process-detected games are unavailable")
            processes = None
        league = LeagueMatchWatcher(self.cfg.auto, self.recorder, enabled=enabled,
                                    mode=lambda: self.cfg.game("league").mode, processes=processes,
                                    condenser=HighlightCondenser(self.ffmpeg, self.cfg.clips))
        cs2 = CS2Watcher(self.cfg.auto, self.recorder, enabled=enabled)
        opts = self.cfg.game("cs2").options
        opts.setdefault("gsi_port", 3021)
        opts.setdefault("gsi_token", CS2Installer.new_token())
        cs2.listen(int(opts["gsi_port"]), opts["gsi_token"])
        watchers: list[GameWatcher] = [league, cs2]
        if processes is not None:
            from Games.deadlock import DeadlockEnricher, DeadlockWatcher, SteamAccount
            for game in GameRegistry.SESSION_GAMES:
                names = self.cfg.game(game.id).processes or game.processes
                if game is GameRegistry.DEADLOCK:
                    self.deadlock = DeadlockEnricher(self.cfg.recording.resolved_output_dir(), self.ffmpeg,
                                                     lambda: self.cfg.game("deadlock"), self.recorder.discard)
                    self.deadlock.start()
                    watchers.append(DeadlockWatcher(
                        self.cfg.auto, self.recorder, enabled=enabled, monitor=ProcessMonitor(names),
                        enricher=self.deadlock,
                        account=lambda: self.cfg.game("deadlock").options.get("account_id")
                        or SteamAccount.active_account_id()))
                    continue
                watchers.append(ProcessSessionWatcher(game, self.cfg.auto, self.recorder, enabled=enabled,
                                                      monitor=ProcessMonitor(names)))
        self.watcher = league
        return watchers

    def bookmark(self) -> float | None:
        """Bookmark hotkey: mark this moment as a highlight in whatever is recording."""
        return self.recorder.bookmark() if self.recorder is not None else None

    def toggle_manual(self) -> None:
        """Record button / hotkey. A manual recording keeps its bookmarks as highlights."""
        rec = self.recorder
        if rec is None:
            return
        if not rec.is_recording:
            rec.start()
            return
        owned = any(w.live_status() is not None for w in self.watchers)
        if owned:
            rec.stop()  # the game's watcher notices and handles it
            return
        marks = rec.take_bookmarks()
        path = rec.stop()
        if path is not None and marks and not Sidecar.path_for(path).exists():
            markers = [{"type": "bookmark", "label": f"Bookmark {i}", "involves_me": True, "importance": 2,
                        "video_time": round(t, 2), "event": {}} for i, t in enumerate(marks, 1)]
            GameWatcher.write_sidecar(path, "", {"title": "Manual recording"}, markers)

    def start(self, on_quit: Callable[[], None] | None = None) -> bool:
        if not self.prepare():
            return False
        self.recorder = Recorder(self.cfg, self.ffmpeg, self.encoder, self.pipeline)
        self.watchers = self.build_watchers()
        for w in self.watchers:
            w.start()
        try:
            from Capture.hotkeys import HotkeyListener
            bindings = {self.cfg.hotkeys.toggle: self.toggle_manual, self.cfg.hotkeys.bookmark: self.bookmark}
            if on_quit is not None:
                bindings[self.cfg.hotkeys.quit] = on_quit
            self._hotkeys = HotkeyListener(bindings)
            self._hotkeys.start()
        except Exception as exc:
            log.warning("Global hotkeys unavailable: %s", exc)
        return True

    def tick(self) -> None:
        if self.recorder is not None:
            self.recorder.check_alive()

    def status(self) -> dict:
        rec = self.recorder
        live = next((st for st in (w.live_status() for w in self.watchers) if st), None)
        s = self.watcher.session if self.watcher else None
        return {
            "ready": rec is not None,
            "auto": self.cfg.auto.enabled and any(self.cfg.game(g).enabled for g in self.cfg.games),
            "recording": bool(rec and rec.is_recording),
            "finalizing": bool(rec and rec.is_finalizing),
            "elapsed": rec.elapsed() if rec else 0.0,
            "bookmarks": len(getattr(rec, "_bookmarks", [])) if rec else 0,
            "error": getattr(rec, "last_error", None) if rec else None,
            "audio_warning": getattr(rec, "audio_warning", None) if rec and rec.is_recording else None,
            "in_match": live is not None,
            "champion": live["title"] if live else None,
            "mode": s.mode if s else None,
            "kda": live["kda"] if live else None,
            "highlights": live["highlights"] if live else 0,
            "mode": self.cfg.game("league").mode,
            "session_matches": (len(self.watcher.session_run.matches)
                                if self.watcher and self.watcher.session_run else None),
        }

    def shutdown(self) -> None:
        """Each step is independent: one failing must never skip saving the recording."""
        steps = [("hotkeys", lambda: self._hotkeys and self._hotkeys.stop())]
        steps += [(f"{w.GAME.name} watcher", w.shutdown) for w in self.watchers]  # saves a match in progress
        if self.watcher is not None and self.watcher not in self.watchers:
            steps.append(("match watcher", self.watcher.shutdown))
        steps.append(("recorder", self._stop_recorder))
        steps.append(("Deadlock lookups", lambda: self.deadlock and self.deadlock.stop()))
        for name, step in steps:
            try:
                step()
            except Exception:
                log.exception("Shutdown: %s failed - continuing", name)

    def _stop_recorder(self) -> None:
        if self.recorder is not None and self.recorder.is_recording:
            log.info("Stopping active recording before exit")
            self.recorder.stop()
