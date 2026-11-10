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
        r.force_start(a.id, 2)
        self.assertEqual(r.phase, "swiping")

    def test_double_tap_start_does_not_skip_genres(self):
        r, (a, b) = room_with(2)
        r.start(a.id, 1)
        r.start(a.id, 1)
        self.assertEqual(r.phase, "genres")

    def test_genres_continue_when_unready_player_goes_away(self):
        r, (a, b, c) = room_with(3)
        r.start(a.id, 1)
        r.set_ready(a.id, 1)
        r.set_ready(b.id, 1)
        now = AWAY + 2
        r.seen(a.id, now)
        r.seen(b.id, now)
        r.tick(now)
        self.assertEqual(r.phase, "swiping")

    def test_genres_continue_when_unready_player_leaves(self):
        r, (a, b, c) = room_with(3)
        r.start(a.id, 1)
        r.set_ready(a.id, 1)
        r.set_ready(b.id, 1)
        r.leave(c.id, 1)
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
        r.start(a.id, IDLE_CLOSE - 1)
        r.tick(2 * IDLE_CLOSE - 2)
        self.assertEqual(r.phase, "genres")
        r.seen(a.id, 2 * IDLE_CLOSE)
        r.tick(2 * IDLE_CLOSE)
        self.assertEqual(r.phase, "closed")
        with self.assertRaises(GameError) as e:
            r.add_player("late", 2 * IDLE_CLOSE)
        self.assertEqual(e.exception.status, 404)

    def test_nickname_control_chars_stripped(self):
        r = Room("ABCD", 0)
        self.assertEqual(r.add_player("a\u202eb\x00\nc", 0).name, "abc")
        with self.assertRaises(GameError) as e:
            r.add_player("\u202e\x00", 0)
        self.assertEqual(e.exception.status, 400)

    def test_duplicate_name_rejected(self):
        r = Room("ABCD", 0)
        r.add_player("Bob", 0)
        with self.assertRaises(GameError) as e:
            r.add_player(" bob ", 0)
        self.assertEqual(e.exception.status, 409)

    def test_leave_frees_slot_and_hands_off_host(self):
        r, ps = room_with(MAX_PLAYERS)
        r.leave(ps[0].id, 1)
        self.assertEqual(len(r.players), MAX_PLAYERS - 1)
        self.assertEqual(r.host_id, ps[1].id)
        r.add_player("new", 1)
        with self.assertRaises(GameError) as e:
            r.state_for(ps[0].id, 1)
        self.assertEqual(e.exception.status, 403)

    def test_rejected_calls_do_not_count_as_activity(self):
        r, (a,) = room_with(1)
        for call in (lambda: r.start("intruder", 500), lambda: r.add_player("", 500)):
            with self.assertRaises(GameError):
                call()
        r.tick(IDLE_CLOSE)
        self.assertEqual(r.phase, "closed")

    def test_genres_only_before_tournament(self):
        r, ps = room_with(2)
        r.set_genres(ps[0].id, [16], 1)
        r.start(ps[0].id, 1); r.start(ps[0].id, 1)
        r.set_genres(ps[1].id, [35], 1)
        r.phase = "tournament"
        with self.assertRaises(GameError) as e:
            r.set_genres(ps[0].id, [28], 1)
        self.assertEqual(e.exception.status, 409)

    def test_genres_input_validated(self):
        r, (a,) = room_with(1)
        for bad in (["x"], "16", list(range(40)), [None]):
            with self.assertRaises(GameError) as e:
                r.set_genres(a.id, bad, 1)
            self.assertEqual(e.exception.status, 400)

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
    r.force_start(ps[0].id, 0)
    r.add_cards(movies(cards))
    return r, ps


def vote_all(r, ps, yes_count, now=1.0):
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

    def test_lone_player_cannot_decide_for_sleeping_partner(self):
        r, (a, b) = swiping_room(2)
        now = AWAY + 5
        for i in range(MAX_LEAD):
            r.seen(a.id, now)
            r.swipe(a.id, i, True, now)
            r.tick(now)
        self.assertEqual(r.shortlist, [])
        self.assertEqual(r.phase, "swiping")

    def test_swipes_emit_one_vote_event_each(self):
        r, (a, b) = swiping_room(2)
        r.swipe(a.id, 0, True, 1)
        r.swipe(a.id, 0, True, 1)
        r.swipe(b.id, 0, False, 1)
        self.assertEqual([(k, m.id, yes) for k, m, yes in r.drain_events()], [("vote", 1, True), ("vote", 1, False)])
        self.assertEqual(r.drain_events(), [])

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
        vote_all(r, ps, 4)
        self.assertEqual(r.resolved, 7)
        r.swipe(late.id, 7, False, 1)
        self.assertEqual(r.resolved, 8)
        self.assertEqual(len(r.shortlist), 1)

    def test_no_cards_fetched_before_genres_are_in(self):
        r, (a, b) = room_with(2)
        r.start(a.id, 0)
        self.assertEqual(r.phase, "genres")
        self.assertFalse(r.needs_cards(0))

    def test_needs_cards(self):
        r, (a, b) = swiping_room(2, cards=12)
        a.cursor = 3
        self.assertTrue(r.needs_cards(1))
        r.add_cards(movies(18, start=13))
        self.assertFalse(r.needs_cards(1))
        r.add_cards(movies(5, start=1))
        self.assertEqual(len(r.deck), 30)


def tournament_room(matches, players=2):
    r, ps = swiping_room(players)
    for _ in range(matches):
        vote_all(r, ps, players)
    if matches < 4:
        r.start_tournament(ps[0].id, 1)
    return r, ps


def pair_ids(r):
    return tuple(m.id for m in r.pair)


class Tournament(unittest.TestCase):
    def test_bracket_four(self):
        r, (a, b) = tournament_room(4)
        self.assertEqual(r.phase, "tournament")
        self.assertEqual(pair_ids(r), (1, 2))
        self.assertEqual(r.state_for(a.id, 1)["round_name"], "Semi-final 1 of 2")
        r.vote(a.id, 1, 1); r.vote(b.id, 1, 1)
        self.assertEqual(pair_ids(r), (3, 4))
        self.assertEqual(r.state_for(a.id, 1)["round_name"], "Semi-final 2 of 2")
        r.vote(a.id, 4, 1); r.vote(b.id, 4, 1)
        self.assertEqual(pair_ids(r), (1, 4))
        self.assertEqual(r.state_for(a.id, 1)["round_name"], "Final")
        r.vote(a.id, 4, 1); r.vote(b.id, 4, 1)
        self.assertEqual(r.phase, "winner")
        self.assertEqual(r.winner.id, 4)
        self.assertEqual(r.state_for(a.id, 1)["winner"]["title"], "M4")

    def test_bracket_three_bye_goes_to_highest_rated(self):
        r, (a, b) = tournament_room(3)
        self.assertEqual(pair_ids(r), (1, 2))
        self.assertEqual(r.state_for(a.id, 1)["round_name"], "Semi-final")
        r.vote(a.id, 1, 1); r.vote(b.id, 1, 1)
        self.assertEqual(pair_ids(r), (1, 3))
        self.assertEqual(r.state_for(a.id, 1)["round_name"], "Final")

    def test_bracket_two_is_final(self):
        r, (a, b) = tournament_room(2)
        self.assertEqual(pair_ids(r), (1, 2))
        self.assertEqual(r.state_for(a.id, 1)["round_name"], "Final")

    def test_winner_emits_pick_event(self):
        r, (a, b) = tournament_room(1)
        picks = [(k, m.id) for k, m, _ in r.drain_events() if k == "pick"]
        self.assertEqual(picks, [("pick", 1)])

    def test_one_match_wins_directly(self):
        r, (a, b) = tournament_room(1)
        self.assertEqual(r.phase, "winner")
        self.assertEqual(r.winner.id, 1)

    def test_start_tournament_needs_a_match(self):
        r, (a, b) = swiping_room(2)
        with self.assertRaises(GameError) as e:
            r.start_tournament(a.id, 1)
        self.assertEqual(e.exception.status, 409)
        with self.assertRaises(GameError) as e:
            r.start_tournament(b.id, 1)
        self.assertEqual(e.exception.status, 403)

    def test_tie_goes_to_higher_rating(self):
        r, (a, b) = tournament_room(2)
        r.vote(a.id, 1, 1); r.vote(b.id, 2, 1)
        self.assertEqual(r.winner.id, 2)

    def test_double_tap_vote_does_not_spill_into_next_pair(self):
        r, (a, b) = tournament_room(3)
        m = r.state_for(a.id, 1)["match"]
        r.vote(a.id, 1, 1, m)
        r.vote(b.id, 1, 1, m)
        r.vote(b.id, 1, 1, m)
        self.assertEqual(r.state_for(a.id, 1)["round_name"], "Final")
        self.assertEqual(r.pair_votes, {})

    def test_vote_is_idempotent(self):
        r, (a, b) = tournament_room(2)
        r.vote(a.id, 1, 1); r.vote(a.id, 2, 1)
        s = r.state_for(a.id, 1)
        self.assertEqual((s["my_vote"], s["voted"], s["of"]), (2, 1, 2))
        with self.assertRaises(GameError) as e:
            r.vote(a.id, 99, 1)
        self.assertEqual(e.exception.status, 400)

    def test_late_joiner_votes_in_tournament(self):
        r, (a, b) = tournament_room(2)
        late = r.add_player("late", 1)
        r.vote(a.id, 1, 1); r.vote(b.id, 1, 1)
        self.assertEqual(r.phase, "tournament")
        r.vote(late.id, 2, 1)
        self.assertEqual(r.winner.id, 1)

    def test_close_any_phase_host_only(self):
        r, (a, b) = tournament_room(2)
        with self.assertRaises(GameError):
            r.close(b.id, 1)
        r.close(a.id, 1)
        self.assertEqual(r.phase, "closed")


if __name__ == "__main__":
    unittest.main()
