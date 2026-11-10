import os
import tempfile
import unittest

from game import Movie
from stats import Stats


def movie(i, rating=7.0):
    return Movie(i, "M%d" % i, 2000 + i, rating, "about M%d" % i, "https://image.tmdb.org/p%d.jpg" % i, [16], 90, "Netflix")


class StatsStore(unittest.TestCase):
    def setUp(self):
        self.s = Stats(":memory:")

    def test_empty(self):
        self.assertEqual((self.s.top(), self.s.recent()), ([], []))

    def test_votes_and_picks_are_counted(self):
        m = movie(1)
        self.s.record_tally(m, 2, 0)
        self.s.record_tally(m, 0, 1)
        self.s.record_pick(m, 100.0)
        [row] = self.s.top()
        self.assertEqual((row["title"], row["picked"], row["likes"], row["dislikes"]), ("M1", 1, 2, 1))
        self.assertEqual((row["overview"], row["runtime"], row["provider"]), ("about M1", 90, "Netflix"))

    def test_movies_never_picked_are_not_listed(self):
        self.s.record_tally(movie(1), 1, 0)
        self.assertEqual((self.s.top(), self.s.recent()), ([], []))

    def test_top_orders_by_picks_then_most_recent(self):
        for i, (picks, last) in {1: (1, 50.0), 2: (3, 10.0), 3: (1, 90.0)}.items():
            for k in range(picks):
                self.s.record_pick(movie(i), last - k)
        self.assertEqual([r["id"] for r in self.s.top()], [2, 3, 1])

    def test_top_and_recent_limit_to_five(self):
        for i in range(1, 9):
            self.s.record_pick(movie(i), float(i))
        self.assertEqual(len(self.s.top()), 5)
        self.assertEqual([r["id"] for r in self.s.recent()], [8, 7, 6, 5, 4])

    def test_recent_lists_each_movie_once(self):
        self.s.record_pick(movie(1), 1.0)
        self.s.record_pick(movie(2), 2.0)
        self.s.record_pick(movie(1), 3.0)
        self.assertEqual([r["id"] for r in self.s.recent()], [1, 2])

    def test_metadata_is_refreshed(self):
        self.s.record_pick(movie(1), 1.0)
        newer = movie(1)
        newer.provider = "Disney Plus"
        self.s.record_tally(newer, 1, 0)
        self.assertEqual(self.s.top()[0]["provider"], "Disney Plus")

    def test_exact_pick_time_is_not_exposed(self):
        self.s.record_pick(movie(1), 123.456)
        self.assertNotIn("last_picked", self.s.top()[0])
        self.assertNotIn("last_picked", self.s.recent()[0])

    def test_survives_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "stats.db")
            s = Stats(path)
            s.record_pick(movie(1), 1.0)
            s.close()
            again = Stats(path)
            self.assertEqual(again.top()[0]["picked"], 1)
            again.close()


if __name__ == "__main__":
    unittest.main()
