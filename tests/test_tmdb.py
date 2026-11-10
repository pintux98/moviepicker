import unittest

import tmdb
from tmdb import Tmdb, build_batch


def result(id, genres, rating=7.0, title=None):
    return {"id": id, "title": title or "M%d" % id, "release_date": "2010-05-01", "vote_average": rating,
            "overview": "o", "poster_path": "/p%d.jpg" % id, "genre_ids": genres}


class FakeFetch:
    def __init__(self, discover=None, studio=None, details=None):
        self.calls = []
        self.discover, self.studio, self.details = discover or [], studio or [], details or {}

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        if path == "genre/movie/list":
            return {"genres": [{"id": 16, "name": "Animation"}, {"id": 35, "name": "Comedy"}]}
        if path == "discover/movie":
            return {"results": self.studio if "with_companies" in params else self.discover}
        return self.details


class TmdbClient(unittest.TestCase):
    def test_discover_params(self):
        f = FakeFetch(discover=[result(1, [16])])
        movies = Tmdb("t", fetch=f, today="2026-11-10").discover([16, 35], 2)
        path, params = f.calls[0]
        self.assertEqual(path, "discover/movie")
        for k, v in {"with_original_language": "en", "with_runtime.gte": 60, "vote_count.gte": 200,
                     "release_date.lte": "2026-11-10", "with_genres": "16|35", "language": "en-US",
                     "page": 2, "sort_by": "popularity.desc"}.items():
            self.assertEqual(params[k], v, k)
        m = movies[0]
        self.assertEqual((m.id, m.title, m.year, m.rating), (1, "M1", 2010, 7.0))
        self.assertEqual(m.poster, "https://image.tmdb.org/t/p/w500/p1.jpg")

    def test_discover_without_genres_has_no_genre_filter(self):
        f = FakeFetch()
        Tmdb("t", fetch=f).discover([], 1)
        self.assertNotIn("with_genres", f.calls[0][1])

    def test_details_reads_ro_provider(self):
        t = Tmdb("t", fetch=FakeFetch(discover=[result(1, [16])], details={
            "runtime": 96, "watch/providers": {"results": {"RO": {"flatrate": [{"provider_name": "Netflix"}]}}}}))
        m = t.details(t.discover([16], 1)[0])
        self.assertEqual((m.runtime, m.provider), (96, "Netflix"))
        t2 = Tmdb("t", fetch=FakeFetch(discover=[result(1, [16])], details={"runtime": 0}))
        m2 = t2.details(t2.discover([16], 1)[0])
        self.assertEqual((m2.runtime, m2.provider), (None, None))

    def test_cache_hits(self):
        f = FakeFetch()
        t = Tmdb("t", fetch=f)
        t.genres(); t.genres()
        self.assertEqual(len(f.calls), 1)

    def test_cache_bounded(self):
        t = Tmdb("t", fetch=lambda path, params: {"x": 1})
        for i in range(tmdb.CACHE_MAX + 50):
            t._get("p", {"i": i})
        self.assertLessEqual(len(t.cache), tmdb.CACHE_MAX)

    def test_details_cache_keeps_only_what_we_use(self):
        f = FakeFetch(discover=[result(1, [16])], details={"runtime": 96, "big": "x" * 1000,
                      "watch/providers": {"results": {"RO": {"flatrate": [{"provider_name": "Netflix"}]}}}})
        t = Tmdb("t", fetch=f)
        m = t.discover([16], 1)[0]
        t.details(m); t.details(m)
        self.assertEqual(sum(1 for path, _ in f.calls if path.startswith("movie/")), 1)
        self.assertNotIn("big", repr(t.cache))


class Deck(unittest.TestCase):
    def test_build_batch_prefers_overlap(self):
        f = FakeFetch(discover=[result(1, [16], 9.0), result(2, [16, 35], 6.0)])
        batch = build_batch(Tmdb("t", fetch=f), {16: 1, 35: 1}, set(), seed=1, page=1)
        self.assertEqual([m.id for m in batch], [2, 1])

    def test_build_batch_animation_every_fourth(self):
        base = [result(i, [28], 7.0) for i in range(1, 21)]
        studio = [result(100 + i, [16], 8.0) for i in range(10)]
        f = FakeFetch(discover=base, studio=studio)
        batch = build_batch(Tmdb("t", fetch=f), {16: 1, 28: 1}, {5, 101}, seed=1, page=1)
        ids = [m.id for m in batch]
        self.assertLessEqual(len(ids), 20)
        for pos in (3, 7, 11):
            self.assertGreaterEqual(ids[pos], 100, ids)
        self.assertNotIn(5, ids)
        self.assertNotIn(101, ids)
        studio_call = [p for path, p in f.calls if "with_companies" in p][0]
        self.assertEqual((studio_call["with_companies"], studio_call["with_genres"]), ("3|2|521", "16"))

    def test_no_animation_without_animation_or_family(self):
        f = FakeFetch(discover=[result(i, [28]) for i in range(1, 6)], studio=[result(100, [16])])
        ids = [m.id for m in build_batch(Tmdb("t", fetch=f), {28: 2}, set(), seed=1, page=1)]
        self.assertNotIn(100, ids)


if __name__ == "__main__":
    unittest.main()
