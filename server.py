import hashlib
import ipaddress
import json
from concurrent.futures import ThreadPoolExecutor
import os
import random
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from game import MAX_ROOMS, GameError, Room
from stats import Stats
from tmdb import MAX_PAGE, Tmdb, build_batch

clock = time.time
STATIC = Path(__file__).resolve().parent / "static"
CODE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ23456789"
CODE_LEN = 6
BODY_MAX = 2048
CREATE_LIMIT, CREATE_WINDOW = 5, 600
OPEN_PER_CLIENT = 2
MISS_LIMIT, MISS_WINDOW = 20, 600
PICK_WINDOW = 86400
REFILL_NOTICE = "Couldn't load more movies, retrying…"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".woff2": "font/woff2", ".png": "image/png", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json"}
CSP = ("default-src 'self'; img-src 'self' https://image.tmdb.org data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")


class App:
    def __init__(self, tmdb: Tmdb, stats: Stats):
        self.tmdb = tmdb
        self.stats = stats
        self.rooms: dict[str, Room] = {}
        self.meta: dict[str, dict] = {}
        self.created: dict[str, list[float]] = {}
        self.misses: dict[str, list[float]] = {}
        self.pending: list[tuple] = []
        self.last_pick: dict[str, float] = {}
        self.lock = threading.Lock()
        self.counts = dict.fromkeys(("visits", "rooms_group", "rooms_solo", "players", "games", "swipes_yes", "swipes_no"), 0)
        self.counts_lock = threading.Lock()

    def count(self, key: str, n: int = 1) -> None:
        with self.counts_lock:
            self.counts[key] += n

    def drain(self, room: Room) -> list[tuple]:
        owner = self.meta.get(room.code, {}).get("owner")
        return [(kind, movie, data, owner) for kind, movie, data in room.drain_events()]

    def record(self, events: list[tuple], now: float) -> None:
        for kind, movie, data, owner in events:
            try:
                if kind == "tally":
                    self.count("swipes_yes", data[0])
                    self.count("swipes_no", data[1])
                    self.stats.record_tally(movie, *data)
                    continue
                self.count("games")
                if now - self.last_pick.get(owner, -PICK_WINDOW) >= PICK_WINDOW:
                    self.last_pick[owner] = now
                    self.stats.record_pick(movie, now)
            except sqlite3.Error as e:
                sys.stderr.write("stats: %r\n" % e)

    def janitor(self, now: float) -> None:
        for code, room in list(self.rooms.items()):
            room.tick(now)
            self.pending += self.drain(room)
            if room.phase == "closed":
                del self.rooms[code]
                self.meta.pop(code, None)
        for log, window in ((self.created, CREATE_WINDOW), (self.misses, MISS_WINDOW)):
            for k in [k for k, ts in log.items() if not ts or now - ts[-1] >= window]:
                del log[k]
        for k in [k for k, t in self.last_pick.items() if now - t >= PICK_WINDOW]:
            del self.last_pick[k]

    def room(self, code: str, now: float, pid: str) -> Room:
        self.janitor(now)
        room = self.rooms.get(code.strip().upper())
        if room is None or pid not in room.players:
            raise GameError("This room is closed", 404)
        return room

    def room_to_join(self, code: str, client: str, now: float) -> Room:
        self.janitor(now)
        misses = [t for t in self.misses.get(client, []) if now - t < MISS_WINDOW]
        if len(misses) >= MISS_LIMIT:
            raise GameError("Too many wrong room codes, try again in a few minutes", 429)
        room = self.rooms.get(code.strip().upper())
        if room is None:
            self.misses[client] = misses + [now]
            raise GameError("This room is closed", 404)
        return room

    def create(self, name, client: str, now: float, solo: bool = False) -> dict:
        self.janitor(now)
        recent = [t for t in self.created.get(client, []) if now - t < CREATE_WINDOW]
        if len(recent) >= CREATE_LIMIT or \
                sum(m.get("owner") == client for m in self.meta.values()) >= OPEN_PER_CLIENT:
            raise GameError("Too many new rooms, try again in a few minutes", 429)
        if len(self.rooms) >= MAX_ROOMS:
            raise GameError("Too many rooms open right now, try again later", 503)
        code = "".join(secrets.choice(CODE_CHARS) for _ in range(CODE_LEN))
        while code in self.rooms:
            code = "".join(secrets.choice(CODE_CHARS) for _ in range(CODE_LEN))
        room = Room(code, now, solo)
        player = room.add_player(name, now)
        if solo:
            room.start(player.id, now)
        self.created[client] = recent + [now]
        self.rooms[code] = room
        self.meta[code] = {"seed": random.randrange(1 << 30), "page": 1, "refilling": False, "owner": client}
        self.count("rooms_solo" if solo else "rooms_group")
        self.count("players")
        return {"code": code, "pid": player.id}

    def metrics(self, now: float) -> str:
        with self.lock:
            open_rooms = len(self.rooms)
            online = sum(len(r.active(now)) for r in self.rooms.values())
        with self.counts_lock:
            c = dict(self.counts)
        lines = [
            ("visits_total", "counter", "Page loads", [("", c["visits"])]),
            ("rooms_created_total", "counter", "Rooms created", [('{mode="group"}', c["rooms_group"]), ('{mode="solo"}', c["rooms_solo"])]),
            ("players_joined_total", "counter", "Players who entered a room", [("", c["players"])]),
            ("games_finished_total", "counter", "Rooms that picked a winner", [("", c["games"])]),
            ("swipes_total", "counter", "Decided card votes", [('{vote="yes"}', c["swipes_yes"]), ('{vote="no"}', c["swipes_no"])]),
            ("rooms_open", "gauge", "Rooms open right now", [("", open_rooms)]),
            ("players_online", "gauge", "Players active in the last minute", [("", online)]),
        ]
        out = []
        for name, kind, help_text, samples in lines:
            out += ["# HELP moviepicker_%s %s" % (name, help_text), "# TYPE moviepicker_%s %s" % (name, kind)]
            out += ["moviepicker_%s%s %d" % (name, labels, value) for labels, value in samples]
        return "\n".join(out) + "\n"

    def action(self, room: Room, pid: str, body: dict, now: float, genre_ids: set[int]) -> None:
        kind = body.get("type")
        if kind == "genres":
            ids = body.get("ids")
            if isinstance(ids, list) and not set(i for i in ids if isinstance(i, int)) <= genre_ids:
                raise GameError("Unknown genre", 400)
            room.set_genres(pid, ids, now)
        elif kind == "leave":
            room.leave(pid, now)
        elif kind == "ready":
            room.set_ready(pid, now)
        elif kind == "start":
            room.start(pid, now)
        elif kind == "force_start":
            room.force_start(pid, now)
        elif kind == "swipe":
            room.swipe(pid, _int(body.get("index")), bool(body.get("yes")), now)
        elif kind == "start_tournament":
            room.start_tournament(pid, now)
        elif kind == "vote":
            match = body.get("match")
            room.vote(pid, _int(body.get("movie_id")), now, None if match is None else _int(match))
        elif kind == "close":
            room.close(pid, now)
        else:
            raise GameError("Unknown action", 400)

    def maybe_refill(self, room: Room, now: float) -> None:
        meta = self.meta.get(room.code)
        if meta and not meta["refilling"] and meta["page"] <= MAX_PAGE and room.needs_cards(now):
            meta["refilling"] = True
            weights = room.genre_weights(now)
            exclude = {m.id for m in room.deck}
            threading.Thread(target=self._refill, args=(room, meta, weights, exclude), daemon=True).start()

    def _refill(self, room: Room, meta: dict, weights: dict, exclude: set) -> None:
        try:
            batch = build_batch(self.tmdb, weights, exclude, meta["seed"], meta["page"])
            if not batch:
                batch = build_batch(self.tmdb, {}, exclude, meta["seed"], meta["page"])
            with ThreadPoolExecutor(8) as pool:
                batch = list(pool.map(self.tmdb.details, batch))
            with self.lock:
                room.add_cards(batch)
                meta["page"] += 1
                if room.notice == REFILL_NOTICE:
                    room.notice = None
        except Exception as e:
            sys.stderr.write("refill %s: %r\n" % (room.code, e))
            with self.lock:
                if room.notice is None:
                    room.notice = REFILL_NOTICE
        finally:
            meta["refilling"] = False


def _int(v) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise GameError("Bad number", 400)
    return v


class Handler(BaseHTTPRequestHandler):
    server_version = "moviepicker"
    sys_version = ""
    timeout = 15
    app: App

    def log_message(self, *a):
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data).encode(), "application/json", {"Cache-Control": "no-store"})

    def _body(self) -> dict:
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise GameError("Send JSON", 415)
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise GameError("Bad request", 400)
        if n < 0:
            raise GameError("Bad request", 400)
        if n > BODY_MAX:
            raise GameError("Request too big", 413)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise GameError("Bad request", 400)
        if not isinstance(data, dict):
            raise GameError("Bad request", 400)
        return data

    def _client(self) -> str:
        raw = self.headers.get("CF-Connecting-IP") or self.client_address[0]
        try:
            ip = ipaddress.ip_address(raw.strip())
        except ValueError:
            return raw[:64]
        return str(ipaddress.ip_network("%s/64" % ip, strict=False)) if ip.version == 6 else str(ip)

    def _api(self, method: str) -> None:
        app, now = self.app, clock()
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        pid = self.headers.get("X-Player", "")
        try:
            if method == "GET" and path == "/api/genres":
                return self._json(200, app.tmdb.genres())
            if method == "GET" and path == "/api/stats":
                try:
                    return self._json(200, {"top": app.stats.top(), "recent": app.stats.recent()})
                except sqlite3.Error as e:
                    sys.stderr.write("stats: %r\n" % e)
                    return self._json(200, {"top": [], "recent": []})
            body = self._body() if method == "POST" else {}
            client = self._client()
            genre_ids = {g["id"] for g in app.tmdb.genres()} if body.get("type") == "genres" else set()
            with app.lock:
                if method == "POST" and path == "/api/rooms":
                    return self._json(200, app.create(body.get("name"), client, now, body.get("solo") is True))
                m = re.fullmatch(r"/api/rooms/([^/]+)(/join|/action)?", path)
                if not m:
                    raise GameError("Not found", 404)
                if m.group(2) == "/join":
                    room = app.room_to_join(m.group(1), client, now)
                else:
                    room = app.room(m.group(1), now, pid)
                if method == "POST" and m.group(2) == "/join":
                    p = room.add_player(body.get("name"), now)
                    app.count("players")
                    out = {"code": room.code, "pid": p.id}
                elif method == "POST" and m.group(2) == "/action":
                    app.action(room, pid, body, now, genre_ids)
                    out = {"ok": True}
                elif method == "GET" and m.group(2) is None:
                    room.seen(pid, now)
                    room.tick(now)
                    out = room.state_for(pid, now)
                else:
                    raise GameError("Not found", 404)
                app.maybe_refill(room, now)
                events, app.pending = app.pending + app.drain(room), []
            app.record(events, now)
            self._json(200, out)
        except GameError as e:
            self._json(e.status, {"error": str(e)})
        except Exception as e:
            sys.stderr.write("error %s %r: %r\n" % (method, path, e))
            self._json(500, {"error": "Something went wrong"})

    def _static(self) -> None:
        parts = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parts.path)
        if path == "/" or re.fullmatch(r"/r/[A-Za-z2-9]{6}", path):
            if self.command == "GET" and self.headers.get("CF-Connecting-IP"):
                self.app.count("visits")
            return self._send(200, index_page(), TYPES[".html"], {"Cache-Control": "no-store"})
        if path.startswith("/static/"):
            f = (STATIC / path[len("/static/"):]).resolve()
            if STATIC not in f.parents or not f.is_file():
                f = None
        elif path in ("/favicon.ico", "/apple-touch-icon.png"):
            f = STATIC / "apple-touch-icon.png"
        else:
            f = None
        if f is None or not f.is_file():
            return self._send(404, b"Not found", "text/plain; charset=utf-8")
        versioned = "v=" in parts.query
        self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"),
                   {"Cache-Control": "public, max-age=31536000, immutable" if versioned else "no-cache"})

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._api("GET")
        else:
            self._static()

    do_HEAD = do_GET

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._api("POST")
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")


def index_page() -> bytes:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css", "qrcode.js", "favicon.svg"):
        digest = hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:10]
        html = html.replace('"/static/%s"' % name, '"/static/%s?v=%s"' % (name, digest))
    return html.encode()


def open_stats(path: str) -> Stats:
    try:
        return Stats(path)
    except sqlite3.Error as e:
        sys.stderr.write("stats: can't open %s (%r), keeping stats in memory only\n" % (path, e))
        return Stats(":memory:")


class MetricsHandler(BaseHTTPRequestHandler):
    server_version = "moviepicker"
    sys_version = ""
    timeout = 15
    app: App

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        body = self.app.metrics(clock()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_metrics_server(host: str, port: int, app: App) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), type("BoundMetrics", (MetricsHandler,), {"app": app}))
    srv.daemon_threads = True
    return srv


def make_server(host: str, port: int, tmdb, stats: Stats) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": App(tmdb, stats)})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv


def main() -> None:
    token = os.environ.get("TMDB_TOKEN")
    if not token:
        sys.exit("TMDB_TOKEN is not set")
    port = int(os.environ.get("PORT", "8000"))
    data = Path(os.environ.get("DATA_DIR", "data"))
    try:
        data.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    print("moviepicker on :%d" % port, flush=True)
    srv = make_server("0.0.0.0", port, Tmdb(token), open_stats(str(data / "stats.db")))
    metrics_port = int(os.environ.get("METRICS_PORT", "8001"))
    threading.Thread(target=make_metrics_server("0.0.0.0", metrics_port, srv.RequestHandlerClass.app).serve_forever,
                     daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
