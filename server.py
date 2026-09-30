"""Local tracker for BasketNews Fantasy draft leagues.

    python3 server.py      ->  http://127.0.0.1:8124

Serves the single-page UI from ./static and the JSON API (backend/payloads) live, proxying
the public BasketNews GraphQL backend (which does not allow browser CORS calls). Tracked
leagues live in ./leagues.json. Things the public API does not keep -- past-round lineups
and injury history -- are recorded under ./data while the server runs (backend/history).
The public site is the same API written to files by export.py.
"""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from backend import config, log, pipeline
from backend.config import BACKGROUND_EVERY, HOST, PORT
from backend.errors import NotFound, UpstreamError
from backend.i18n import LANG, L
from backend.payloads.draft import draft_payload
from backend.payloads.games import games_payload
from backend.payloads.injuries import injuries_payload
from backend.payloads.leagues import add_league, leagues_payload, remove_league
from backend.payloads.player import player_payload, proballers_redirect
from backend.payloads.players import players_payload
from backend.payloads.records import records_payload
from backend.payloads.standings import rounds_payload, standings_payload
from backend.payloads.team import team_payload
from backend.payloads.transfers import transfers_payload
from backend.util import write_json


def background_loop():
    while True:
        try:
            pipeline.refresh_tracked_leagues()
        except Exception as exc:  # keep the loop alive whatever happens upstream
            log.get("fetch").exception("background refresh failed: %s", exc)
        time.sleep(BACKGROUND_EVERY)


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


def _round_param(query):
    val = query.get("round", [None])[0]
    return int(val) if val not in (None, "") else None


ID = r"([0-9a-f]{24})"


class Handler(BaseHTTPRequestHandler):
    server_version = "FantasyTracker/2.0"

    def log_message(self, fmt, *args):
        log.get("http").info(fmt, *args)

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def _dispatch(self, routes):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        LANG.set("en" if query.get("lang", [""])[0] == "en" else "lt")
        for pattern, fn in routes:
            m = re.fullmatch(pattern, url.path)
            if m:
                try:
                    self._send(200, fn(m, query))
                    pipeline.store()  # lineups / injury changes this request fetched
                except NotFound as exc:
                    self._send(404, {"error": str(exc)})
                except ValueError as exc:
                    self._send(400, {"error": str(exc)})
                except UpstreamError as exc:
                    self._send(502, {"error": str(exc)})
                return True
        return False

    def _redirect(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        m = re.fullmatch(rf"/go/proballers/{ID}/{ID}", urlparse(self.path).path)
        if m:
            try:
                self._redirect(proballers_redirect(m[1], m[2]))
            except (NotFound, UpstreamError) as exc:
                self._send(404, {"error": str(exc)})
            return
        routes = [
            (r"/api/leagues", lambda m, q: leagues_payload()),
            (rf"/api/league/{ID}/standings", lambda m, q: standings_payload(m[1], _round_param(q))),
            (rf"/api/league/{ID}/rounds", lambda m, q: rounds_payload(m[1], _round_param(q))),
            (rf"/api/league/{ID}/free-agents", lambda m, q: players_payload(m[1], "free")),
            (rf"/api/league/{ID}/players", lambda m, q: players_payload(m[1], "all")),
            (rf"/api/league/{ID}/games", lambda m, q: games_payload(m[1], _round_param(q))),
            (rf"/api/league/{ID}/records", lambda m, q: records_payload(m[1], _round_param(q))),
            (rf"/api/league/{ID}/draft", lambda m, q: draft_payload(m[1])),
            (rf"/api/league/{ID}/injuries", lambda m, q: injuries_payload(m[1])),
            (rf"/api/league/{ID}/transfers", lambda m, q: transfers_payload(m[1])),
            (rf"/api/league/{ID}/team/{ID}", lambda m, q: team_payload(m[1], m[2], _round_param(q))),
            (rf"/api/league/{ID}/player/{ID}", lambda m, q: player_payload(m[1], m[2])),
        ]
        if self._dispatch(routes):
            return
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            self._send(404, {"error": L("Nežinomas adresas", "Unknown address")})
            return
        self._serve_static(path)

    def do_POST(self):
        routes = [
            (r"/api/leagues", lambda m, q: {"league": add_league(self._json_body().get("url"))}),
        ]
        if not self._dispatch(routes):
            self._send(404, {"error": L("Nežinomas adresas", "Unknown address")})

    def do_DELETE(self):
        routes = [(rf"/api/leagues/{ID}", lambda m, q: remove_league(m[1]) or {"ok": True})]
        if not self._dispatch(routes):
            self._send(404, {"error": L("Nežinomas adresas", "Unknown address")})

    def _serve_static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (config.STATIC_DIR / rel).resolve()
        if config.STATIC_DIR not in target.parents or not target.is_file():
            target = config.STATIC_DIR / "index.html"
        self._send(200, target.read_bytes(), CONTENT_TYPES.get(target.suffix, "application/octet-stream"))


def main():
    log.setup()
    if not config.LEAGUES_FILE.exists():
        write_json(config.LEAGUES_FILE, [])
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Thread(target=background_loop, daemon=True).start()
    log.get("http").info("Fantasy tracker: http://%s:%s", HOST, PORT)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
