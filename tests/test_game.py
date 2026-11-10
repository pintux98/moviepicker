import unittest

from game import AWAY, IDLE_CLOSE, MAX_PLAYERS, GameError, Room, threshold


def room_with(n, now=0.0):
    r = Room("ABCD", now)
    ps = [r.add_player("p%d" % i, now) for i in range(n)]
    return r, ps


class Basics(unittest.TestCase):
    def test_threshold(self):
        for n, want in [(1, 1), (2, 2), (3, 2), (4, 3), (5, 4), (10, 7)]:
            self.assertEqual(threshold(n, False), want, n)
        for n, want in [(2, 2), (4, 3), (5, 3), (10, 6)]:
            self.assertEqual(threshold(n, True), want, n)

    def test_first_player_is_host_and_room_caps_at_10(self):
        r, ps = room_with(MAX_PLAYERS)
        self.assertEqual(r.host_id, ps[0].id)
        with self.assertRaises(GameError) as e:
            r.add_player("eleven", 0)
        self.assertEqual(e.exception.status, 409)

    def test_nickname_rules(self):
        r = Room("ABCD", 0)
        self.assertEqual(r.add_player("  <b>Bob</b>  ", 0).name, "<b>Bob</b>")
        self.assertEqual(len(r.add_player("x" * 200, 0).name), 20)
        with self.assertRaises(GameError) as e:
            r.add_player("   ", 0)
        self.assertEqual(e.exception.status, 400)

    def test_start_flow(self):
        r, (a, b) = room_with(2)
        with self.assertRaises(GameError) as e:
            r.start(b.id, 1)
        self.assertEqual(e.exception.status, 403)
        r.start(a.id, 1)
        self.assertEqual(r.phase, "genres")
        r.set_ready(a.id, 2)
        self.assertEqual(r.phase, "genres")
        r.set_ready(b.id, 3)
        self.assertEqual(r.phase, "swiping")

    def test_host_can_force_swiping(self):
        r, (a, b) = room_with(2)
        r.start(a.id, 1)
        r.start(a.id, 2)
        self.assertEqual(r.phase, "swiping")

    def test_away_player_not_counted_then_returns(self):
        r, (a, b) = room_with(2)
        r.seen(a.id, AWAY + 1)
        self.assertEqual([p.id for p in r.active(AWAY + 1)], [a.id])
        r.seen(b.id, AWAY + 2)
        self.assertEqual(len(r.active(AWAY + 2)), 2)

    def test_host_handoff(self):
        r, (a, b, c) = room_with(3)
        r.seen(b.id, AWAY + 1)
        r.seen(c.id, AWAY + 1)
        r.tick(AWAY + 1)
        self.assertEqual(r.host_id, b.id)

    def test_idle_close(self):
        r, (a, b) = room_with(2)
        r.seen(a.id, IDLE_CLOSE - 1)
        r.tick(IDLE_CLOSE - 1)
        self.assertEqual(r.phase, "lobby")
        r.start(a.id, IDLE_CLOSE - 1)  # an action resets the clock
        r.tick(2 * IDLE_CLOSE - 2)
        self.assertEqual(r.phase, "genres")
        r.seen(a.id, 2 * IDLE_CLOSE)  # polling alone doesn't keep it alive
        r.tick(2 * IDLE_CLOSE)
        self.assertEqual(r.phase, "closed")
        with self.assertRaises(GameError) as e:
            r.add_player("late", 2 * IDLE_CLOSE)
        self.assertEqual(e.exception.status, 404)

    def test_genre_weights(self):
        r, (a, b) = room_with(2)
        r.set_genres(a.id, [16, 35], 1)
        r.set_genres(b.id, [16], 1)
        self.assertEqual(r.genre_weights(1), {16: 2, 35: 1})

    def test_state_for_lists_players(self):
        r, (a, b) = room_with(2)
        s = r.state_for(b.id, 0)
        self.assertEqual(s["code"], "ABCD")
        self.assertEqual(s["phase"], "lobby")
        self.assertFalse(s["host"])
        self.assertEqual([p["name"] for p in s["players"]], ["p0", "p1"])
        self.assertTrue(s["players"][0]["host"])
        self.assertEqual(s["max_players"], MAX_PLAYERS)


if __name__ == "__main__":
    unittest.main()
