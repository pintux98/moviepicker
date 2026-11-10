import http.client
import json
import threading
import time
import unittest

import server
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
        self.srv = server.make_server("127.0.0.1", 0, self.tmdb)
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
        text = r.read().decode()
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

    def test_rate_limit_rooms(self):
        for _ in range(5):
            self.create(ip="1.2.3.4")
        s, d = self.call("POST", "/api/rooms", {"name": "x"}, ip="1.2.3.4")
        self.assertEqual(s, 429)
        self.create(ip="5.6.7.8")

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
        for path in ("/", "/r/ABCD"):
            s, body = self.call("GET", path)
            self.assertEqual(s, 200)
            self.assertIn("Moviepicker", body)

    def test_unknown_player_403(self):
        code, a = self.create()
        self.assertEqual(self.state(code, "nobody")[0], 403)


class ApiTmdbDown(ApiBase):
    tmdb = FakeTmdb(fail=True)

    def test_tmdb_failure_keeps_room_alive(self):
        code, a = self.create()
        self.act(code, a, type="start")
        self.act(code, a, type="start")
        d = self.wait_state(code, a, lambda d: d["notice"] == "Couldn't load more movies, retrying…")
        self.assertEqual(d["phase"], "swiping")


if __name__ == "__main__":
    unittest.main()
