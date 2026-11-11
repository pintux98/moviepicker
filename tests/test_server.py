import contextlib
import http.client
import io
import re
import sqlite3
import tempfile
from pathlib import Path
import json
import socket
import threading
import time
import unittest
from unittest import mock

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


def mem(test, cls=Stats):
    return test.enterContext(contextlib.closing(cls(":memory:")))


class ApiBase(unittest.TestCase):
    tmdb = FakeTmdb()

    def setUp(self):
        server.clock = time.time
        self.srv = server.make_server("127.0.0.1", 0, self.tmdb, mem(self))
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

    def test_solo_room_starts_at_genres_and_is_private(self):
        s, d = self.call("POST", "/api/rooms", {"name": "Me", "solo": True})
        self.assertEqual(s, 200)
        st = self.state(d["code"], d["pid"])[1]
        self.assertEqual((st["phase"], st["solo"]), ("genres", True))
        self.assertEqual(self.call("POST", "/api/rooms/%s/join" % d["code"], {"name": "Bob"})[0], 409)

    def test_metrics_only_on_private_port(self):
        app = self.srv.RequestHandlerClass.app
        msrv = server.make_metrics_server("127.0.0.1", 0, app)
        threading.Thread(target=msrv.serve_forever, daemon=True).start()
        try:
            self.call("GET", "/")
            self.call("HEAD", "/")
            c0 = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            c0.request("GET", "/")
            c0.getresponse().read()
            c0.close()
            self.call("POST", "/api/rooms", {"name": "Me", "solo": True}, ip="2.2.2.1")
            code, a = self.create(ip="2.2.2.2")
            self.call("POST", "/api/rooms/%s/join" % code, {"name": "Bob"})
            c = http.client.HTTPConnection("127.0.0.1", msrv.server_address[1], timeout=5)
            c.request("GET", "/metrics")
            r = c.getresponse()
            text = r.read().decode()
            c.close()
            self.assertEqual(r.status, 200)
            for line in ('moviepicker_visits_total 1', 'moviepicker_rooms_created_total{mode="solo"} 1',
                         'moviepicker_rooms_created_total{mode="group"} 1', 'moviepicker_players_joined_total 3',
                         'moviepicker_rooms_open 2', 'moviepicker_games_finished_total 0',
                         'moviepicker_swipes_total{vote="yes"} 0'):
                self.assertIn(line + "\n", text)
            self.assertEqual(self.call("GET", "/metrics")[0], 404)
        finally:
            msrv.shutdown()
            msrv.server_close()

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

    def test_ipv6_48_open_rooms_capped(self):
        for n in range(5):
            self.create(ip="2001:db8:1:%d::1" % n)
            self.create(ip="2001:db8:1:%d::2" % n)
        self.assertEqual(self.call("POST", "/api/rooms", {"name": "x"}, ip="2001:db8:1:5::1")[0], 429)
        self.create(ip="2001:db8:2::1")

    def test_ipv6_48_wrong_codes_capped(self):
        code, _ = self.create()
        for n in range(5):
            for _ in range(20):
                self.assertEqual(self.call("POST", "/api/rooms/ZZZZZZ/join", {"name": "g"}, ip="2001:db8:1:%d::1" % n)[0], 404)
        self.assertEqual(self.call("POST", "/api/rooms/%s/join" % code, {"name": "g"}, ip="2001:db8:1:5::1")[0], 429)
        self.assertEqual(self.call("POST", "/api/rooms/%s/join" % code, {"name": "h"}, ip="2001:db8:2::1")[0], 200)

    def test_bad_cf_header_uses_peer(self):
        self.create(ip="garbage1")
        self.create(ip="garbage2")
        self.assertEqual(self.call("POST", "/api/rooms", {"name": "x"}, ip="garbage3")[0], 429)

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

    def test_assets_are_versioned_and_page_never_cached(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/")
        r = c.getresponse()
        body = r.read().decode()
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        m = re.search(r'/static/app\.js\?v=([0-9a-f]{10})"', body)
        self.assertIsNotNone(m, body)
        self.assertRegex(body, r'/static/style\.css\?v=[0-9a-f]{10}"')
        c.request("GET", "/static/app.js?v=" + m.group(1))
        r = c.getresponse()
        r.read()
        self.assertIn("immutable", r.getheader("Cache-Control"))
        c.close()

    def test_head_requests_answer_like_get_without_body(self):
        for path in ("/", "/static/app.js"):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            c.request("HEAD", path)
            r = c.getresponse()
            self.assertEqual((r.status, r.read()), (200, b""), path)
            self.assertGreater(int(r.getheader("Content-Length")), 0)
            c.close()

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
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        self.srv = server.make_server("127.0.0.1", 0, FakeTmdb(), mem(self, BrokenStats))
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
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stderr(io.StringIO()):
            s = server.open_stats(str(Path(d) / "no" / "such" / "stats.db"))
            s.record_pick(Movie(1, "M", 2000, 7.0, "o", None, [16]), 1.0)
            self.assertEqual(s.top()[0]["picked"], 1)
            s.close()


class Limits(unittest.TestCase):
    def setUp(self):
        self.saved = server.MAX_CONNECTIONS, server.REQUEST_DEADLINE
        self.socks = []
        self.srv = None

    def tearDown(self):
        for s in self.socks:
            s.close()
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
        server.MAX_CONNECTIONS, server.REQUEST_DEADLINE = self.saved

    def start(self):
        self.srv = server.make_server("127.0.0.1", 0, FakeTmdb(), mem(self))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def connect(self):
        s = socket.create_connection(self.srv.server_address, timeout=5)
        self.socks.append(s)
        return s

    def get(self):
        c = http.client.HTTPConnection(*self.srv.server_address, timeout=5)
        try:
            c.request("GET", "/")
            return c.getresponse().status
        except ConnectionError:
            return None
        finally:
            c.close()

    def test_slow_request_cut_at_deadline(self):
        server.REQUEST_DEADLINE = 1
        self.start()
        s = self.connect()
        s.settimeout(0.3)
        began, gone = time.monotonic(), False
        while not gone and time.monotonic() - began < 5:
            try:
                s.sendall(b"G")
                gone = s.recv(1) == b""
            except TimeoutError:
                pass
            except ConnectionError:
                gone = True
        self.assertTrue(gone)
        self.assertLess(time.monotonic() - began, 4)
        self.assertEqual(self.get(), 200)

    def test_connections_capped(self):
        server.MAX_CONNECTIONS = 2
        self.start()
        busy = [self.connect(), self.connect()]
        self.assertEqual(self.connect().recv(1), b"")
        for s in busy:
            s.close()
        began, status = time.monotonic(), None
        while status != 200 and time.monotonic() - began < 5:
            status = self.get()
        self.assertEqual(status, 200)

    def test_metrics_server_capped(self):
        server.MAX_CONNECTIONS = 1
        self.srv = server.make_metrics_server("127.0.0.1", 0, server.App(FakeTmdb(), mem(self)))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.connect()
        self.assertEqual(self.connect().recv(1), b"")


class AppTests(unittest.TestCase):
    def test_janitor_sweeps_once_per_second(self):
        app = server.App(FakeTmdb(), mem(self))
        room = app.rooms[app.create("Ann", "1.1.1.1", 1000)["code"]]
        ticks = []
        room.tick = ticks.append
        app.janitor(1002)
        app.janitor(1002.5)
        self.assertEqual(ticks, [1002])


class Refills(unittest.TestCase):
    def setUp(self):
        self.saved = server.MAX_REFILLS
        server.MAX_REFILLS = 2
        self.app = server.App(FakeTmdb(), mem(self))
        self.rooms = []
        for i in range(3):
            pid = self.app.create("Ann", "1.1.1.%d" % i, 1000, solo=True)["pid"]
            room = list(self.app.rooms.values())[-1]
            room.force_start(pid, 1000)
            self.rooms.append(room)

    def tearDown(self):
        server.MAX_REFILLS = self.saved

    def refill(self, *rooms):
        with mock.patch.object(server.threading, "Thread") as t:
            for room in rooms:
                self.app.maybe_refill(room, 1000)
        return t

    def test_one_refill_in_flight_per_room(self):
        self.assertEqual(self.refill(self.rooms[0], self.rooms[0]).call_count, 1)

    def test_refills_capped_across_rooms(self):
        t = self.refill(*self.rooms)
        self.assertEqual(t.call_count, 2)
        kw = t.call_args_list[0].kwargs
        kw["target"](*kw["args"])
        self.assertEqual(len(self.rooms[0].deck), 20)
        self.assertEqual(self.refill(self.rooms[2]).call_count, 1)

    def test_failed_thread_start_frees_slot(self):
        with mock.patch.object(server.threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
            for room in self.rooms[:2]:
                with self.assertRaises(RuntimeError):
                    self.app.maybe_refill(room, 1000)
        self.assertFalse(self.app.meta[self.rooms[0].code]["refilling"])
        self.assertEqual(self.refill(*self.rooms).call_count, 2)


class ApiTmdbDown(ApiBase):
    tmdb = FakeTmdb(fail=True)

    def setUp(self):
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        super().setUp()

    def test_tmdb_failure_keeps_room_alive(self):
        code, a = self.create()
        self.act(code, a, type="start")
        self.act(code, a, type="force_start")
        d = self.wait_state(code, a, lambda d: d["notice"] == "Couldn't load more movies, retrying…")
        self.assertEqual(d["phase"], "swiping")


if __name__ == "__main__":
    unittest.main()
