"""TMDB client (cached) and the deck builder that orders movies for a room."""
import datetime
import json
import random
import time
import urllib.parse
import urllib.request

from game import Movie

API = "https://api.themoviedb.org/3/"
IMG = "https://image.tmdb.org/t/p/w500"
CACHE_TTL = 6 * 3600
ANIMATION, FAMILY = 16, 10751
STUDIOS = [3, 2, 521]  # Pixar, Walt Disney Animation Studios, DreamWorks Animation
BATCH = 20


class Tmdb:
    def __init__(self, token: str, fetch=None, today: str | None = None):
        self.token = token
        self.fetch = fetch or self._http
        self.today = today
        # ponytail: unbounded dict cache, fine for a few rooms a night; add LRU if memory ever grows
        self.cache: dict = {}

    def _http(self, path: str, params: dict) -> dict:
        req = urllib.request.Request(API + path + "?" + urllib.parse.urlencode(params),
                                     headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    def _get(self, path: str, params: dict) -> dict:
        key = (path, tuple(sorted(params.items())))
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        data = self.fetch(path, params)
        self.cache[key] = (time.time(), data)
        return data

    def genres(self) -> list[dict]:
        return self._get("genre/movie/list", {"language": "en-US"})["genres"]

    def discover(self, genre_ids: list[int], page: int, companies: list[int] | None = None) -> list[Movie]:
        params = {"language": "en-US", "with_original_language": "en", "with_runtime.gte": 60,
                  "vote_count.gte": 200, "release_date.lte": self.today or datetime.date.today().isoformat(),
                  "sort_by": "popularity.desc", "include_adult": "false", "page": page}
        if genre_ids:
            params["with_genres"] = "|".join(map(str, genre_ids))
        if companies:
            params["with_companies"] = "|".join(map(str, companies))
        return [_movie(r) for r in self._get("discover/movie", params).get("results", [])]

    def details(self, m: Movie) -> Movie:
        d = self._get("movie/%d" % m.id, {"language": "en-US", "append_to_response": "watch/providers"})
        m.runtime = d.get("runtime") or None
        flat = ((d.get("watch/providers") or {}).get("results", {}).get("RO") or {}).get("flatrate") or []
        m.provider = flat[0]["provider_name"] if flat else None
        return m


def _movie(r: dict) -> Movie:
    date = r.get("release_date") or ""
    return Movie(id=r["id"], title=r.get("title") or "?", year=int(date[:4]) if date[:4].isdigit() else None,
                 rating=round(float(r.get("vote_average") or 0), 1), overview=r.get("overview") or "",
                 poster=IMG + r["poster_path"] if r.get("poster_path") else None,
                 genre_ids=list(r.get("genre_ids") or []))


def build_batch(t: Tmdb, weights: dict[int, int], exclude: set[int], seed: int, page: int) -> list[Movie]:
    """Up to BATCH new movies for a room: more shared genres first, studio animation every 4th card."""
    rng = random.Random(seed + page)
    picked = sorted(g for g, w in weights.items() if w > 0)
    base = [m for m in t.discover(picked, page) if m.id not in exclude]
    base.sort(key=lambda m: 10 * sum(weights.get(g, 0) for g in m.genre_ids) + m.rating + rng.random(), reverse=True)
    if not (weights.get(ANIMATION) or weights.get(FAMILY)):
        return base[:BATCH]
    taken = exclude | {m.id for m in base}
    anim = [m for m in t.discover([ANIMATION], page, companies=STUDIOS) if m.id not in taken]
    out: list[Movie] = []
    while len(out) < BATCH and (base or anim):
        src = anim if (len(out) % 4 == 3 and anim) or not base else base
        out.append(src.pop(0))
    return out
