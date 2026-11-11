import datetime
import json
import random
import re
import time
import urllib.parse
import urllib.request

from game import MAX_KEYWORDS, Movie

API = "https://api.themoviedb.org/3/"
IMG = "https://image.tmdb.org/t/p/w500"
CACHE_TTL = 6 * 3600
CACHE_MAX = 3000
MAX_PAGE = 500
ANIMATION, FAMILY = 16, 10751
STUDIOS = [3, 2, 521]
BATCH = 20


class Tmdb:
    def __init__(self, token: str, fetch=None, today: str | None = None):
        self.token = token
        self.fetch = fetch or self._http
        self.today = today
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
        self._put(key, data)
        return data

    def _put(self, key, data) -> None:
        self.cache.pop(key, None)
        while len(self.cache) >= CACHE_MAX:
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = (time.time(), data)

    def genres(self) -> list[dict]:
        return self._get("genre/movie/list", {"language": "en-US"})["genres"]

    def keywords(self, word: str) -> list[int]:
        whole = re.compile(r"\b%s\b" % re.escape(word.casefold()))
        found = self._get("search/keyword", {"query": word}).get("results", [])
        return [k["id"] for k in found if whole.search(str(k.get("name", "")).casefold())][:MAX_KEYWORDS]

    def discover(self, genre_ids: list[int], page: int, companies: list[int] | None = None,
                 keywords: list[int] | None = None) -> list[Movie]:
        params = {"language": "en-US", "with_original_language": "en", "with_runtime.gte": 60,
                  "vote_count.gte": 200, "release_date.lte": self.today or datetime.date.today().isoformat(),
                  "sort_by": "popularity.desc", "include_adult": "false", "page": page}
        if genre_ids:
            params["with_genres"] = "|".join(map(str, genre_ids))
        if companies:
            params["with_companies"] = "|".join(map(str, companies))
        if keywords:
            params["with_keywords"] = "|".join(map(str, keywords))
        return [_movie(r) for r in self._get("discover/movie", params).get("results", [])]

    def details(self, m: Movie) -> Movie:
        key = ("details", m.id)
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            m.runtime, m.provider = hit[1]
            return m
        d = self.fetch("movie/%d" % m.id, {"language": "en-US", "append_to_response": "watch/providers"})
        flat = ((d.get("watch/providers") or {}).get("results", {}).get("RO") or {}).get("flatrate") or []
        m.runtime, m.provider = d.get("runtime") or None, flat[0]["provider_name"] if flat else None
        self._put(key, (m.runtime, m.provider))
        return m


def _movie(r: dict) -> Movie:
    date = r.get("release_date") or ""
    return Movie(id=r["id"], title=r.get("title") or "?", year=int(date[:4]) if date[:4].isdigit() else None,
                 rating=round(float(r.get("vote_average") or 0), 1), overview=r.get("overview") or "",
                 poster=IMG + r["poster_path"] if r.get("poster_path") else None,
                 genre_ids=list(r.get("genre_ids") or []))


def _mix(base: list[Movie], extra: list[Movie], every: int) -> list[Movie]:
    out: list[Movie] = []
    while len(out) < BATCH and (base or extra):
        src = extra if (len(out) % every == every - 1 and extra) or not base else base
        out.append(src.pop(0))
    return out


def build_batch(t: Tmdb, weights: dict[int, int], exclude: set[int], seed: int, page: int,
                keywords: list[int] | None = None) -> list[Movie]:
    rng = random.Random(seed + page)
    picked = sorted(g for g, w in weights.items() if w > 0)
    base = [m for m in t.discover(picked, page) if m.id not in exclude] if picked or not keywords else []
    base.sort(key=lambda m: 10 * sum(weights.get(g, 0) for g in m.genre_ids) + m.rating + rng.random(), reverse=True)
    if keywords:
        taken = exclude | {m.id for m in base}
        base = _mix(base, [m for m in t.discover([], page, keywords=keywords) if m.id not in taken], 2)
    if not (weights.get(ANIMATION) or weights.get(FAMILY)):
        return base[:BATCH]
    taken = exclude | {m.id for m in base}
    return _mix(base, [m for m in t.discover([ANIMATION], page, companies=STUDIOS) if m.id not in taken], 4)
