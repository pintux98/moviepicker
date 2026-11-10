import http.client
import sqlite3
import tempfile
from pathlib import Path
import json
import threading
import time
import unittest

import server
from stats import Stats
from game import Movie


class FakeTmdb:
    def __init__(self, fail=False):
        self.fail = fail

    def genres(self):
        return [{"id": 16, "name": "Animation"}, {"id": 35, "name": "Comedy"}]

    def discover(self, genre_ids, page, companies=None):
        if self.fail:
            raise OSError("tmdb down")
        if companies or page > 2:
            return []
        return [Movie(page * 100 + i, "M%d" % (page * 100 + i), 2000, 7.0 + i / 100, "o", None, [16])
                for i in range(20)]

    def details(self, m):
        m.runtime, m.provider = 90, "Netflix"
        return m


class ApiBase(unittest.TestCase):
    tmdb = FakeTmdb()

    def setUp(self):
        server.clock = time.time
        self.srv = server.make_server("127.0.0.1", 0, self.tmdb, Stats(":memory:"))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        server.clock = time.time

    def call(self, method, path, body=None, pid=None, ip="9.9.9.9", raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"CF-Connecting-IP": ip, "Content-Type": "application/json"}
        if pid:
            headers["X-Player"] = pid
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        c.request(method, path, body=data, headers=headers)
        r = c.getresponse()
        text = r.read().decode("utf-8", "replace")
        c.close()
        try:
            return r.status, json.loads(text)
        except ValueError:
            return r.status, text

    def create(self, name="Ann", ip="9.9.9.9"):
        s, d = self.call("POST", "/api/rooms", {"name": name}, ip=ip)
        self.assertEqual(s, 200, d)
        return d["code"], d["pid"]

    def act(self, code, pid, **body):
        return self.call("POST", "/api/rooms/%s/action" % code, body, pid=pid)

    def state(self, code, pid):
        return self.call("GET", "/api/rooms/" + code, pid=pid)

    def wait_state(self, code, pid, cond, timeout=3):
        end = time.time() + timeout
        while time.time() < end:
            s, d = self.state(code, pid)
            if s == 200 and cond(d):
                return d
            time.sleep(0.05)
        self.fail("condition not met, last state: %r" % (d,))


class Api(ApiBase):
    def solo_game(self, ip):
        code, a = self.create(ip=ip)
        self.call("POST", "/api/rooms/%s/action" % code, {"type": "start"}, pid=a, ip=ip)
        self.call("POST", "/api/rooms/%s/action" % code, {"type": "force_start"}, pid=a, ip=ip)
        d = self.wait_state(code, a, lambda d: d["cards"])
        self.call("POST", "/api/rooms/%s/action" % code, {"type": "swipe", "index": d["cards"][0]["index"], "yes": True}, pid=a, ip=ip)
        self.call("POST", "/api/rooms/%s/action" % code, {"type": "start_tournament"}, pid=a, ip=ip)
        self.call("POST", "/api/rooms/%s/action" % code, {"type": "close"}, pid=a, ip=ip)

    def test_one_counted_pick_per_creator_per_day(self):
        self.solo_game("3.3.3.3")
        self.solo_game("3.3.3.3")
        picks = lambda: sum(m["picked"] for m in self.call("GET", "/api/stats")[1]["top"])
        self.assertEqual(picks(), 1)
        self.solo_game("3.3.3.4")
        self.assertEqual(picks(), 2)

    def test_stats_empty(self):
        self.assertEqual(self.call("GET", "/api/stats"), (200, {"top": [], "recent": []}))

    def test_genres(self):
        s, d = self.call("GET", "/api/genres")
        self.assertEqual((s, d[0]["name"]), (200, "Animation"))

    def test_full_flow_two_players(self):
        code, a = self.create()
        s, j = self.call("POST", "/api/rooms/%s/join" % code, {"name": "Bob"})
        b = j["pid"]
        self.assertEqual(self.act(code, a, type="start")[0], 200)
        for p in (a, b):
            self.assertEqual(self.act(code, p, type="genres", ids=[16])[0], 200)
            self.assertEqual(self.act(code, p, type="ready")[0], 200)
        for _ in range(4):
            d = self.wait_state(code, a, lambda d: d["cards"] or d["phase"] != "swiping")
            idx = d["cards"][0]["index"]
            self.assertEqual(d["cards"][0]["provider"], "Netflix")
            for p in (a, b):
                self.act(code, p, type="swipe", index=idx, yes=True)
        for _ in range(3):
            d = self.state(code, a)[1]
            self.assertEqual(d["phase"], "tournament")
            for p in (a, b):
                self.act(code, p, type="vote", movie_id=d["pair"][0]["id"])
        d = self.state(code, b)[1]
        self.assertEqual(d["phase"], "winner")
        self.assertIsNotNone(d["winner"]["title"])
        self.assertEqual(self.act(code, b, type="close")[0], 403)
        s, st = self.call("GET", "/api/stats")
        self.assertEqual([m["title"] for m in st["recent"]], [d["winner"]["title"]])
        self.assertEqual(st["top"][0]["picked"], 1)
        self.assertEqual(st["top"][0]["likes"], 2)
        self.assertEqual(self.act(code, a, type="close")[0], 200)
        s, d = self.state(code, a)
        self.assertEqual((s, d["error"]), (404, "This room is closed"))

    def test_reload_keeps_identity(self):
        code, a = self.create()
        server.clock = lambda: time.time() + 30
        s, d = self.state(code, a)
        self.assertEqual(s, 200)
        self.assertEqual(d["me"]["name"], "Ann")
        self.assertFalse(d["players"][0]["away"])

    def test_join_normalizes_code(self):
        code, a = self.create()
        s, d = self.call("POST", "/api/rooms/%s/join" % (" " + code.lower() + " ").replace(" ", "%20"), {"name": "Bob"})
        self.assertEqual((s, d["code"]), (200, code))

    def test_unknown_room_404(self):
        s, d = self.call("POST", "/api/rooms/ZZZZ/join", {"name": "Bob"})
        self.assertEqual((s, d["error"]), (404, "This room is closed"))

    def test_open_rooms_per_ip_capped(self):
        for _ in range(2):
            self.create(ip="1.2.3.4")
        s, d = self.call("POST", "/api/rooms", {"name": "x"}, ip="1.2.3.4")
        self.assertEqual(s, 429)
        self.create(ip="5.6.7.8")

    def test_ipv6_bucketed_by_64(self):
        self.create(ip="2001:db8:0:1::1")
        self.create(ip="2001:db8:0:1::2")
        self.assertEqual(self.call("POST", "/api/rooms", {"name": "x"}, ip="2001:db8:0:1::3")[0], 429)
        self.create(ip="2001:db8:0:2::1")

    def test_room_guessing_rate_limited(self):
        code, a = self.create(ip="7.7.7.7")
        for i in range(20):
            self.assertEqual(self.call("POST", "/api/rooms/QQQ%s/join" % "ABCDEFGHJKLMNPQRSTUV"[i], {"name": "g"}, ip="6.6.6.6")[0], 404)
        self.assertEqual(self.call("POST", "/api/rooms/%s/join" % code, {"name": "g"}, ip="6.6.6.6")[0], 429)
        self.assertEqual(self.call("POST", "/api/rooms/%s/join" % code, {"name": "ok"}, ip="8.8.8.8")[0], 200)

    def test_polling_closed_rooms_never_locks_out_members(self):
        code, a = self.create(ip="4.4.4.4")
        for _ in range(25):
            self.assertEqual(self.call("GET", "/api/rooms/ZZZZZZ", pid="x", ip="4.4.4.4")[0], 404)
        self.assertEqual(self.call("GET", "/api/rooms/" + code, pid=a, ip="4.4.4.4")[0], 200)

    def test_unknown_genres_rejected(self):
        code, a = self.create()
        for ids in ([10 ** 30], [99999], [-5]):
            self.assertEqual(self.act(code, a, type="genres", ids=ids)[0], 400, ids)
        self.assertEqual(self.act(code, a, type="genres", ids=[16])[0], 200)

    def test_leave_action(self):
        code, a = self.create()
        s, j = self.call("POST", "/api/rooms/%s/join" % code, {"name": "Bob"})
        self.assertEqual(self.act(code, j["pid"], type="leave")[0], 200)
        self.assertEqual(self.state(code, j["pid"])[0], 404)
        self.assertEqual(len(self.state(code, a)[1]["players"]), 1)

    def test_content_length_tricks_400(self):
        for cl in ("-1", "abc"):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            c.putrequest("POST", "/api/rooms")
            c.putheader("Content-Type", "application/json")
            c.putheader("Content-Length", cl)
            c.endheaders()
            c.send(b'{"name": "x"}')
            self.assertEqual(c.getresponse().status, 400, cl)
            c.close()

    def test_post_requires_json_content_type(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/api/rooms", body=b'{"name": "x"}', headers={"Content-Type": "text/plain"})
        self.assertEqual(c.getresponse().status, 415)
        c.close()

    def test_handler_has_socket_timeout(self):
        self.assertEqual(server.Handler.timeout, 15)

    def test_refill_stops_after_tmdb_page_limit(self):
        app = self.srv.RequestHandlerClass.app
        code, a = self.create()
        self.act(code, a, type="start")
        self.act(code, a, type="force_start")
        time.sleep(0.3)
        with app.lock:
            room = app.rooms[code]
            app.meta[code]["page"] = 501
            room.deck = room.deck[:1]
            app.maybe_refill(room, server.clock())
            self.assertFalse(app.meta[code]["refilling"])

    def test_room_full_409(self):
        code, a = self.create()
        for i in range(9):
            self.assertEqual(self.call("POST", "/api/rooms/%s/join" % code, {"name": "p%d" % i})[0], 200)
        self.assertEqual(self.call("POST", "/api/rooms/%s/join" % code, {"name": "late"})[0], 409)

    def test_bad_json_400(self):
        self.assertEqual(self.call("POST", "/api/rooms", raw=b"{nope")[0], 400)

    def test_body_too_big_413(self):
        self.assertEqual(self.call("POST", "/api/rooms", raw=b"x" * 5000)[0], 413)

    def test_static_traversal_404(self):
        self.assertEqual(self.call("GET", "/static/../server.py")[0], 404)
        self.assertEqual(self.call("GET", "/static/%2e%2e/server.py")[0], 404)

    def test_index_and_room_link_serve_page(self):
        for path in ("/", "/r/K7XQ2P", "/r/k7xq2p"):
            s, body = self.call("GET", path)
            self.assertEqual(s, 200)
            self.assertIn("Moviepicker", body)

    def test_room_code_is_six_letters_and_digits(self):
        codes = {self.create(ip="10.0.0.%d" % i)[0] for i in range(1, 30)}
        for code in codes:
            self.assertRegex(code, r"^[A-Z2-9]{6}$")
        self.assertTrue(any(c.isdigit() for c in "".join(codes)))

    def test_icons(self):
        for path in ("/favicon.ico", "/apple-touch-icon.png", "/static/favicon.svg"):
            self.assertEqual(self.call("GET", path)[0], 200, path)

    def test_non_member_gets_404_like_unknown_room(self):
        code, a = self.create()
        self.assertEqual(self.state(code, "nobody")[0], 404)


class BrokenStats(Stats):
    def record_tally(self, *a):
        raise sqlite3.OperationalError("database or disk is full")

    record_pick = record_tally

    def top(self, *a):
        raise sqlite3.OperationalError("database is locked")


class ApiStatsBroken(ApiBase):
    def setUp(self):
        server.clock = time.time
        self.srv = server.make_server("127.0.0.1", 0, FakeTmdb(), BrokenStats(":memory:"))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def test_stats_failure_never_breaks_the_game(self):
        code, a = self.create()
        self.act(code, a, type="start")
        self.act(code, a, type="force_start")
        d = self.wait_state(code, a, lambda d: d["cards"])
        self.assertEqual(self.act(code, a, type="swipe", index=d["cards"][0]["index"], yes=True)[0], 200)
        self.assertEqual(self.act(code, a, type="start_tournament")[0], 200)
        self.assertEqual(self.state(code, a)[1]["phase"], "winner")
        self.assertEqual(self.call("GET", "/api/stats"), (200, {"top": [], "recent": []}))


class OpenStats(unittest.TestCase):
    def test_unwritable_path_falls_back_to_memory(self):
        with tempfile.TemporaryDirectory() as d:
            s = server.open_stats(str(Path(d) / "no" / "such" / "stats.db"))
            s.record_pick(Movie(1, "M", 2000, 7.0, "o", None, [16]), 1.0)
            self.assertEqual(s.top()[0]["picked"], 1)
            s.close()


class ApiTmdbDown(ApiBase):
    tmdb = FakeTmdb(fail=True)

    def test_tmdb_failure_keeps_room_alive(self):
        code, a = self.create()
        self.act(code, a, type="start")
        self.act(code, a, type="force_start")
        d = self.wait_state(code, a, lambda d: d["notice"] == "Couldn't load more movies, retrying…")
        self.assertEqual(d["phase"], "swiping")


if __name__ == "__main__":
    unittest.main()
