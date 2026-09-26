"""Counter-Strike 2 via Valve's official Game State Integration (GSI).

CS2 reads `gamestate_integration_*.cfg` from its cfg folder at launch and then POSTs JSON about
the match to our local server: map + phase + score, and - while you're alive - your own stats.
We diff those stats to find kills, headshots, multikills, deaths, assists and round MVPs.
It's a documented Valve feature, not a hook into the game, so it's VAC-safe."""
from __future__ import annotations

import json
import logging
import queue
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from Core.config import AutoSettings
from Games.base import GameWatcher
from Games.registry import GameRegistry

log = logging.getLogger("gamecapture.cs2")

MULTIKILL = {2: "Double kill", 3: "Triple kill", 4: "Quadra kill", 5: "ACE"}


# ======================================================================== install

class CS2Installer:
    """Finds CS2 through Steam and writes our GSI config file into its cfg folder."""
    APP_ID = "730"
    CFG_NAME = "gamestate_integration_gamecapture.cfg"
    GAME_CFG = Path("steamapps/common/Counter-Strike Global Offensive/game/csgo/cfg")

    @staticmethod
    def steam_path() -> Path | None:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Valve\\Steam") as key:
                return Path(winreg.QueryValueEx(key, "SteamPath")[0])
        except (ImportError, OSError):
            default = Path(r"C:\\Program Files (x86)\\Steam")
            return default if default.exists() else None

    @classmethod
    def library_folders(cls, steam: Path) -> list[Path]:
        folders = [steam]
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return folders
        for raw in re.findall(r'"path"\s+"([^"]+)"', text):
            path = Path(raw.replace("\\\\", "\\"))
            if path not in folders:
                folders.append(path)
        return folders

    @classmethod
    def find_cfg_dir(cls, steam: Path | None = None) -> Path | None:
        steam = steam or cls.steam_path()
        if steam is None:
            return None
        for lib in cls.library_folders(steam):
            if (lib / "steamapps" / f"appmanifest_{cls.APP_ID}.acf").exists() and (lib / cls.GAME_CFG).is_dir():
                return lib / cls.GAME_CFG
        return None

    @classmethod
    def config_text(cls, port: int, token: str) -> str:
        return f'''"GameCapture"
{{
    "uri"       "http://127.0.0.1:{port}"
    "timeout"   "5.0"
    "buffer"    "0.1"
    "throttle"  "0.25"
    "heartbeat" "10.0"
    "auth"
    {{
        "token" "{token}"
    }}
    "data"
    {{
        "provider"            "1"
        "map"                 "1"
        "round"               "1"
        "player_id"           "1"
        "player_state"        "1"
        "player_match_stats"  "1"
    }}
}}
'''

    @classmethod
    def install(cls, port: int, token: str, cfg_dir: Path | None = None) -> Path:
        cfg_dir = cfg_dir or cls.find_cfg_dir()
        if cfg_dir is None:
            raise FileNotFoundError("Couldn't find CS2 through Steam - is it installed?")
        target = cfg_dir / cls.CFG_NAME
        target.write_text(cls.config_text(port, token), encoding="utf-8")
        log.info("CS2 integration installed: %s (restart CS2 to load it)", target)
        return target

    @classmethod
    def installed_file(cls, cfg_dir: Path | None = None) -> Path | None:
        cfg_dir = cfg_dir or cls.find_cfg_dir()
        target = cfg_dir / cls.CFG_NAME if cfg_dir else None
        return target if target and target.exists() else None

    @staticmethod
    def new_token() -> str:
        return secrets.token_hex(16)

    @classmethod
    def installed_token(cls, cfg_dir: Path | None = None) -> str | None:
        """The token in an already-installed file, so a fresh config keeps working with it."""
        try:
            installed = cls.installed_file(cfg_dir)
            text = installed.read_text(encoding="utf-8") if installed else ""
        except OSError:
            return None
        m = re.search(r'"token"\s+"([0-9a-fA-F]+)"', text)
        return m[1] if m else None


# ======================================================================== receive

class GsiServer:
    """Tiny local HTTP server CS2 posts to. Rejects anything without our token."""

    def __init__(self, port: int, token: str, on_payload) -> None:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                try:
                    body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                    data = json.loads(body.decode("utf-8"))
                except (ValueError, OSError):
                    self.send_response(400)
                    self.end_headers()
                    return
                if (data.get("auth") or {}).get("token") != server.token:
                    self.send_response(403)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                server.on_payload(data)

            def log_message(self, *_args) -> None:
                pass

        self.token = token
        self.on_payload = on_payload
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True

    @property
    def port(self) -> int:
        return self.httpd.server_port

    def start(self) -> "GsiServer":
        threading.Thread(target=self.httpd.serve_forever, name="cs2-gsi", daemon=True).start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# ======================================================================== watch

@dataclass
class CS2Match:
    recording_id: str | None
    map: str
    mode: str
    team: str = ""
    markers: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)       # last seen own match_stats
    round_kills: int = 0
    round_hs: int = 0
    round: int = -1
    last_multikill: dict | None = None   # upgraded in place: Double -> Triple -> ... within a round
    score: tuple[int, int] = (0, 0)                 # (mine, theirs)
    result: str | None = None
    ended_at: float | None = None
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def title(self) -> str:
        name = self.map.split("_", 1)[-1] if "_" in self.map else self.map
        return name.replace("_", " ").title() or "CS2 match"


class CS2Watcher(GameWatcher):
    GAME = GameRegistry.CS2
    IN_MATCH = ("warmup", "live", "intermission", "gameover")
    SILENCE = 30.0  # no updates this long (heartbeat is 10 s) = CS2 closed or you left

    def __init__(self, settings: AutoSettings, recorder, enabled=lambda game_id: True) -> None:
        super().__init__(settings, recorder, enabled)
        self.inbox: queue.Queue[dict] = queue.Queue()
        self.match: CS2Match | None = None
        self.server: GsiServer | None = None

    def listen(self, port: int, token: str) -> None:
        try:
            self.server = GsiServer(port, token, self.inbox.put).start()
            log.info("CS2 integration listening on port %d", port)
        except OSError as exc:
            log.error("CS2 integration can't use port %d (%s) - change it in Settings > Counter-Strike 2", port, exc)

    def shutdown(self) -> None:
        super().shutdown()
        if self.server is not None:
            self.server.stop()

    # ---------- loop ----------

    def tick(self) -> None:
        while True:
            try:
                self.handle(self.inbox.get_nowait())
            except queue.Empty:
                break
        m, now = self.match, time.monotonic()
        if m is None:
            return
        if self.recorder.current_id != m.recording_id:
            log.info("CS2 recording stopped manually")
            self.match = None
        elif m.ended_at is not None and now - m.ended_at >= self.settings.post_roll_seconds:
            self._end("match over")
        elif now - m.last_seen > self.SILENCE:
            self._end("CS2 stopped sending updates")

    def finish(self, reason: str) -> None:
        if self.match is not None:
            self._end(reason)

    def live_status(self) -> dict | None:
        m = self.match
        if m is None:
            return None
        st = m.stats
        return {"title": m.title, "kda": f"{st.get('kills', 0)}/{st.get('deaths', 0)}/{st.get('assists', 0)}",
                "highlights": sum(1 for x in m.markers if x["involves_me"])}

    # ---------- payloads ----------

    def handle(self, data: dict) -> None:
        mp = data.get("map")
        m = self.match
        if m is not None:
            m.last_seen = time.monotonic()
        if not mp:                                   # main menu
            if m is not None and m.ended_at is None:
                self._end("left the match")
            return
        phase = mp.get("phase", "")
        if m is None:
            if phase in ("warmup", "live") and self.enabled(self.GAME.id) and not self.recorder.is_recording:
                if self.start_recording(self.GAME.prefix) and self.recorder.is_recording:
                    self.match = m = CS2Match(self.recorder.current_id, mp.get("name", ""), mp.get("mode", ""),
                                              last_seen=time.monotonic())
                    self._marker("game_start", "Match start", False, 0)
                    log.info("CS2 match: %s (%s)", m.title, m.mode)
            if m is None:
                return
        if mp.get("round", m.round) != m.round:
            m.round, m.last_multikill = mp.get("round", m.round), None
        me = (data.get("provider") or {}).get("steamid")
        player = data.get("player") or {}
        if player.get("steamid") == me:              # only our own stats (not a spectated teammate)
            m.team = player.get("team", m.team)
            self._diff(m, player)
        self._update_score(m, mp)
        if phase == "gameover" and m.ended_at is None:
            mine, theirs = m.score
            m.result = "Win" if mine > theirs else "Lose" if theirs > mine else "Tie"
            m.ended_at = time.monotonic()
            self._marker("game_end", f"Match end - {m.result} {mine}-{theirs}", False, 0)

    def _update_score(self, m: CS2Match, mp: dict) -> None:
        ct, t = (mp.get("team_ct") or {}).get("score", 0), (mp.get("team_t") or {}).get("score", 0)
        if m.team == "CT":
            m.score = (ct, t)
        elif m.team == "T":
            m.score = (t, ct)

    def _diff(self, m: CS2Match, player: dict) -> None:
        stats = player.get("match_stats") or {}
        state = player.get("state") or {}
        old = m.stats
        if old:
            new_kills = stats.get("kills", 0) - old.get("kills", 0)
            round_kills, round_hs = state.get("round_kills", 0), state.get("round_killhs", 0)
            for _ in range(max(0, new_kills)):
                hs = round_hs > m.round_hs
                self._marker("kill", "Headshot kill" if hs else "Kill", True, 2)
                m.round_hs = round_hs
            if new_kills > 0 and round_kills >= 2 and round_kills > m.round_kills:
                label = MULTIKILL.get(round_kills, f"{round_kills} kills")
                importance = 4 if round_kills >= 4 else 3
                if m.last_multikill is not None:          # same round: Double kill becomes Triple kill
                    m.last_multikill.update(label=label, importance=importance, event={"KillStreak": round_kills})
                else:
                    m.last_multikill = self._marker("multikill", label, True, importance, {"KillStreak": round_kills})
            for _ in range(max(0, stats.get("deaths", 0) - old.get("deaths", 0))):
                self._marker("death", "Died", True, 1)
            for _ in range(max(0, stats.get("assists", 0) - old.get("assists", 0))):
                self._marker("assist", "Assist", True, 1)
            if stats.get("mvps", 0) > old.get("mvps", 0):
                self._marker("objective", "Round MVP", True, 2)
        m.round_kills = state.get("round_kills", 0)
        m.round_hs = state.get("round_killhs", 0)
        if stats:
            m.stats = dict(stats)

    def _marker(self, kind: str, label: str, mine: bool, importance: int, event: dict | None = None) -> dict:
        m = self.match
        marker = {"type": kind, "label": label, "involves_me": mine, "importance": importance,
                  "event": event or {}, "video_time": round(self.recorder.elapsed(), 2)}
        m.markers.append(marker)
        if mine:
            log.info("[MARK] %s", label)
        return marker

    def _end(self, reason: str) -> None:
        m, self.match = self.match, None
        if not self.recorder.is_recording or self.recorder.current_id != m.recording_id:
            return
        result = m.result or "Unfinished"
        st = m.stats
        kda = (st.get("kills", 0), st.get("deaths", 0), st.get("assists", 0))
        log.info("CS2 match over (%s): %s %s %d/%d/%d", reason, m.title, result, *kda)
        markers = m.markers + self.bookmark_markers()
        chapters = self.chapters(markers) if self.settings.chapters else None
        path = self.stop_recording(f"{m.title.replace(' ', '')}_{result}_{kda[0]}-{kda[1]}-{kda[2]}", chapters)
        if path is None:
            return
        game = {"title": m.title, "map": m.map, "mode": m.mode, "result": result,
                "kills": kda[0], "deaths": kda[1], "assists": kda[2], "mvps": st.get("mvps", 0),
                "score": f"{m.score[0]}-{m.score[1]}"}
        self.write_sidecar(path, self.GAME.id, game, markers)
