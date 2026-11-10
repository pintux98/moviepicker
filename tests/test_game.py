import unittest

from game import AWAY, IDLE_CLOSE, MAX_LEAD, MAX_PLAYERS, RELAX_AFTER, GameError, Movie, Room, threshold


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


def movies(n, start=1):
    return [Movie(i, "M%d" % i, 2000, 7.0 + (i % 10) / 10, "o", None, [16]) for i in range(start, start + n)]


def swiping_room(n, cards=40):
    r, ps = room_with(n)
    r.start(ps[0].id, 0)
    r.start(ps[0].id, 0)
    r.add_cards(movies(cards))
    return r, ps


def vote_all(r, ps, yes_count, now=1.0):
    """Everyone in ps votes on their current card; the first yes_count say yes."""
    for i, p in enumerate(ps):
        r.swipe(p.id, max(p.cursor, r.resolved), i < yes_count, now)


class Swiping(unittest.TestCase):
    def test_two_players_match_needs_both(self):
        r, ps = swiping_room(2)
        vote_all(r, ps, 2)
        self.assertEqual([m.id for m in r.shortlist], [1])
        lm = r.state_for(ps[0].id, 1)["last_match"]
        self.assertEqual((lm["title"], lm["yes"], lm["of"]), ("M1", 2, 2))
        vote_all(r, ps, 1)
        self.assertEqual([m.id for m in r.shortlist], [1])

    def test_four_players_need_three(self):
        r, ps = swiping_room(4)
        vote_all(r, ps, 3)
        self.assertEqual(len(r.shortlist), 1)
        vote_all(r, ps, 2)
        self.assertEqual(len(r.shortlist), 1)

    def test_away_player_does_not_block(self):
        r, ps = swiping_room(3)
        now = AWAY + 1
        r.seen(ps[0].id, now)
        r.seen(ps[1].id, now)
        vote_all(r, ps[:2], 2, now)
        self.assertEqual(r.resolved, 1)
        self.assertEqual(len(r.shortlist), 1)

    def test_swipe_is_idempotent(self):
        r, (a, b) = swiping_room(2)
        r.swipe(a.id, 0, True, 1)
        r.swipe(a.id, 0, False, 1)
        self.assertEqual(r.votes[0], {a.id: True})
        r.swipe(b.id, 5, True, 1)
        self.assertNotIn(5, r.votes)

    def test_max_lead(self):
        r, (a, b) = swiping_room(2)
        for i in range(MAX_LEAD):
            r.swipe(a.id, i, True, 1)
        s = r.state_for(a.id, 1)
        self.assertTrue(s["ahead"])
        self.assertEqual(s["cards"], [])
        r.swipe(a.id, MAX_LEAD, True, 1)
        self.assertEqual(a.cursor, MAX_LEAD)
        self.assertFalse(r.state_for(b.id, 1)["ahead"])
        self.assertEqual(r.state_for(b.id, 1)["cards"][0]["index"], 0)

    def test_relax_after_25_misses(self):
        r, ps = swiping_room(4, cards=60)
        for _ in range(RELAX_AFTER):
            vote_all(r, ps, 0)
        self.assertTrue(r.relaxed)
        self.assertEqual(r.notice, "Loosening up: majority now counts")
        vote_all(r, ps, 2)
        self.assertEqual(len(r.shortlist), 0)

    def test_relaxed_five_players_three_yes_match(self):
        r, ps = swiping_room(5, cards=60)
        for _ in range(RELAX_AFTER):
            vote_all(r, ps, 0)
        vote_all(r, ps, 3)
        self.assertEqual(len(r.shortlist), 1)

    def test_auto_tournament_at_4(self):
        r, ps = swiping_room(2)
        for _ in range(4):
            vote_all(r, ps, 2)
        self.assertEqual(r.phase, "tournament")

    def test_late_joiner_starts_at_current_card(self):
        r, ps = swiping_room(4)
        for _ in range(7):
            vote_all(r, ps, 0)
        late = r.add_player("late", 1)
        self.assertEqual(late.cursor, 7)
        vote_all(r, ps, 4)          # 4 of 5 yes, threshold(5) = 4 -> match only once late votes too
        self.assertEqual(r.resolved, 7)
        r.swipe(late.id, 7, False, 1)
        self.assertEqual(r.resolved, 8)
        self.assertEqual(len(r.shortlist), 1)

    def test_needs_cards(self):
        r, (a, b) = swiping_room(2, cards=12)
        a.cursor = 3
        self.assertTrue(r.needs_cards(1))
        r.add_cards(movies(18, start=13))
        self.assertFalse(r.needs_cards(1))
        r.add_cards(movies(5, start=1))  # duplicates ignored
        self.assertEqual(len(r.deck), 30)


if __name__ == "__main__":
    unittest.main()
