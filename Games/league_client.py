"""Client for League's Live Client Data API (https://127.0.0.1:2999).

Only answers while a match is running. The game uses a self-signed certificate, which
Riot's docs say is fine to skip verifying locally. Every call returns None instead of
raising, so "no game running" is just a None."""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request


class LeagueLiveClient:
    BASE = "https://127.0.0.1:2999/liveclientdata/"
    ENV_OVERRIDE = "GAMECAPTURE_LIVE_URL"  # dev only: point at Test/fake_league.py

    def __init__(self, timeout: float = 1.0, base: str | None = None) -> None:
        self.timeout = timeout
        self.base = base or os.environ.get(self.ENV_OVERRIDE) or self.BASE
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # Empty ProxyHandler: never send localhost traffic through a system proxy.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))

    def _get(self, path: str):
        try:
            with self._opener.open(self.base + path, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            exc.close()
            return None  # 404: still loading, or not in game
        except (urllib.error.URLError, OSError, ValueError):
            return None  # nothing listening, or garbled reply
        if isinstance(data, dict) and "errorCode" in data:
            return None
        return data

    def game_stats(self) -> dict | None:
        """{'gameMode': 'CLASSIC', 'gameTime': 123.4, ...}"""
        return self._get("gamestats")

    def active_player_name(self) -> str | None:
        """'Name#TAG' for the local player; None when spectating or in a replay."""
        name = self._get("activeplayername")
        return name if isinstance(name, str) and name else None

    def player_list(self) -> list | None:
        return self._get("playerlist")

    def events(self) -> list:
        data = self._get("eventdata")
        return data.get("Events", []) if isinstance(data, dict) else []
