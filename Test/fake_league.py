"""Dev tool: pretends to be League's Live Client Data API so you can test GameCapture end to end
without launching League. Plays a scripted ~2.5 minute match in real time, then goes silent.

  Terminal 1:   python Test\\fake_league.py
  Terminal 2:   $env:GAMECAPTURE_LIVE_URL = "http://127.0.0.1:2998/liveclientdata/"
                python main.py
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scenario import Scenario


class FakeLeagueServer:
    def __init__(self, port: int = 2998, delay: float = 5.0, linger: float = 8.0, loop: bool = False,
                 gap: float = 15.0, scenario: Scenario | None = None) -> None:
        self.scenario = scenario or Scenario.quick()
        self.delay, self.linger, self.loop, self.gap = delay, linger, loop, gap
        self.started = time.monotonic()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                code, body = server.respond(self.path)
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True

    @property
    def port(self) -> int:
        return self.httpd.server_port

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/liveclientdata/"

    def game_time(self) -> float | None:
        """Current game time, or None when 'League' isn't in a match."""
        t = time.monotonic() - self.started - self.delay
        cycle = self.scenario.end_time + self.linger
        if self.loop and t > cycle + self.gap:
            self.started = time.monotonic() - self.delay  # next match
            t = 0.0
        return None if t < 0 or t > cycle else t

    def respond(self, path: str) -> tuple[int, object]:
        t = self.game_time()
        endpoint = path.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        if t is None:
            return 404, {"errorCode": "RESOURCE_NOT_FOUND", "message": "not in game"}
        s = self.scenario
        handlers = {"gamestats": lambda: s.game_stats(t), "activeplayername": s.active_player_name,
                    "playerlist": lambda: s.player_list(t), "eventdata": lambda: {"Events": s.events_until(t)}}
        if endpoint not in handlers:
            return 404, {"errorCode": "RESOURCE_NOT_FOUND", "message": endpoint}
        return 200, handlers[endpoint]()

    def start(self) -> "FakeLeagueServer":
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def ticker(self) -> None:
        """Prints events as they 'happen' until Ctrl+C."""
        shown = 0
        while True:
            t = self.game_time()
            events = self.scenario.events_until(t) if t is not None else []
            if len(events) < shown:
                shown = 0
            for ev in events[shown:]:
                print(f"\r  {ev['EventTime']:6.1f}s  {ev['EventName']:<16}")
            shown = len(events)
            print(f"\r[{f'in game {t:5.1f}s' if t is not None else 'no match   '}]", end="", flush=True)
            time.sleep(0.5)


def main() -> int:
    ap = argparse.ArgumentParser(description="Fake League Live Client Data API for testing")
    ap.add_argument("--port", type=int, default=2998)
    ap.add_argument("--delay", type=float, default=5.0, help="seconds before the match 'starts'")
    ap.add_argument("--loop", action="store_true", help="play matches back to back")
    args = ap.parse_args()
    server = FakeLeagueServer(args.port, args.delay, loop=args.loop).start()
    print(f"Fake League API on {server.base_url}  (Ctrl+C to stop)")
    print(f"Match starts in {args.delay:.0f} s, lasts {server.scenario.end_time:.0f} s. "
          f"Player {server.scenario.riot_id}\n")
    try:
        server.ticker()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
