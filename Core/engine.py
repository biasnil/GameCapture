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
from Games.registry import GameRegistry

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
        opts.setdefault("gsi_token", CS2Installer.installed_token() or CS2Installer.new_token())
        cs2.listen(int(opts["gsi_port"]), opts["gsi_token"])
        self.watcher = league
        watchers: list[GameWatcher] = [league, cs2]
        self._psutil = processes is not None
        if self._psutil:
            watchers += self._session_watchers()
        return watchers

    def _session_watcher(self, game) -> GameWatcher:
        from Games.processes import ProcessMonitor
        from Games.session_watcher import ProcessSessionWatcher
        names = self.cfg.game(game.id).processes or game.processes
        return ProcessSessionWatcher(game, self.cfg.auto, self.recorder, enabled=self.auto_enabled,
                                     monitor=ProcessMonitor(names))

    def _session_watchers(self) -> list[GameWatcher]:
        """One process watcher per game without a match API - built-in or added by you."""
        from Games.registry import GameRegistry
        GameRegistry.load_custom(self.cfg.games)
        return [self._session_watcher(g) for g in GameRegistry.session_games()]

    def sync_game_watchers(self) -> None:
        """Settings changed the game list (a game added / removed, an .exe renamed): update the
        watchers now, without a restart. A game that is being recorded keeps its watcher until it closes."""
        from Games.registry import GameRegistry
        from Games.session_watcher import ProcessSessionWatcher
        if self.recorder is None or not getattr(self, "_psutil", False):
            return
        GameRegistry.load_custom(self.cfg.games)
        wanted = {g.id: g for g in GameRegistry.session_games()}
        keep: list[GameWatcher] = []
        for w in self.watchers:
            if not isinstance(w, ProcessSessionWatcher):
                keep.append(w)
                continue
            game = wanted.get(w.GAME.id)
            names = {n.lower() for n in (self.cfg.game(game.id).processes or game.processes)} if game else set()
            if w.session is not None or (game is not None and names == w.monitor.names):
                keep.append(w)
                wanted.pop(w.GAME.id, None)
                continue
            w.shutdown()  # removed, or its .exe changed (it's rebuilt below)
        for game in wanted.values():
            if not any(w.GAME.id == game.id for w in keep):
                watcher = self._session_watcher(game)
                watcher.start()
                keep.append(watcher)
                log.info("Now watching for %s (%s)", game.name, ", ".join(watcher.monitor.names))
        self.watchers = keep

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
        return {
            "ready": rec is not None,
            "auto": self.cfg.auto.enabled and any(self.cfg.game(g.id).enabled for g in GameRegistry.supported()),
            "recording": bool(rec and rec.is_recording),
            "finalizing": bool(rec and rec.is_finalizing),
            "elapsed": rec.elapsed() if rec else 0.0,
            "bookmarks": len(getattr(rec, "_bookmarks", [])) if rec else 0,
            "error": getattr(rec, "last_error", None) if rec else None,
            "audio_warning": getattr(rec, "audio_warning", None) if rec and rec.is_recording else None,
            "in_match": live is not None,
            "champion": live["title"] if live else None,
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
        for name, step in steps:
            try:
                step()
            except Exception:
                log.exception("Shutdown: %s failed - continuing", name)

    def _stop_recorder(self) -> None:
        if self.recorder is not None and self.recorder.is_recording:
            log.info("Stopping active recording before exit")
            self.recorder.stop()
