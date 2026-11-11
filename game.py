import math
import secrets
import unicodedata
from dataclasses import asdict, dataclass, field

MAX_PLAYERS = 10
MAX_ROOMS = 500
IDLE_CLOSE = 600
AWAY = 60
SHORTLIST_SIZE = 4
RELAX_AFTER = 25
MAX_LEAD = 15
MAX_DECK = 300
NAME_MAX = 20
MAX_GENRES = 32


@dataclass
class Movie:
    id: int
    title: str
    year: int | None
    rating: float
    overview: str
    poster: str | None
    genre_ids: list[int]
    runtime: int | None = None
    provider: str | None = None

    def public(self) -> dict:
        d = asdict(self)
        del d["genre_ids"]
        return d


@dataclass
class Player:
    id: str
    name: str
    joined: float
    last_seen: float
    genres: list[int] = field(default_factory=list)
    ready: bool = False
    cursor: int = 0


class GameError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def clean_name(name) -> str:
    if not isinstance(name, str):
        return ""
    kept = "".join(c for c in name if not unicodedata.category(c).startswith("C"))
    return " ".join(unicodedata.normalize("NFKC", kept).split())[:NAME_MAX].rstrip()


def threshold(n: int, relaxed: bool) -> int:
    if n <= 2:
        return n
    if relaxed:
        return n // 2 + 1
    return math.ceil(2 * n / 3)


class Room:
    def __init__(self, code: str, now: float, solo: bool = False):
        self.code = code
        self.solo = solo
        self.phase = "lobby"
        self.players: dict[str, Player] = {}
        self.host_id: str | None = None
        self.last_action = now
        self.deck: list[Movie] = []
        self.votes: dict[int, dict[str, bool]] = {}
        self.resolved = 0
        self.shortlist: list[Movie] = []
        self.relaxed = False
        self.no_match_streak = 0
        self.notice: str | None = None
        self.last_match: dict | None = None
        self.matches: list[dict] = []
        self.bye: Movie | None = None
        self.pair_votes: dict[str, int] = {}
        self.winner: Movie | None = None
        self.events: list[tuple] = []

    def _player(self, pid: str) -> Player:
        if pid not in self.players:
            raise GameError("You're not in this room", 403)
        return self.players[pid]

    def _act(self, pid: str, now: float) -> Player:
        self._open()
        p = self._player(pid)
        p.last_seen = self.last_action = now
        return p

    def _open(self):
        if self.phase == "closed":
            raise GameError("This room is closed", 404)

    def _host_only(self, pid: str):
        if pid != self.host_id:
            raise GameError("Only the host can do that", 403)

    def add_player(self, name: str, now: float) -> Player:
        self._open()
        name = clean_name(name)
        if not name:
            raise GameError("Pick a name", 400)
        if self.solo and self.players:
            raise GameError("This room is private", 409)
        if any(p.name.casefold() == name.casefold() for p in self.players.values()):
            raise GameError("Someone already has that name", 409)
        if len(self.players) >= MAX_PLAYERS:
            raise GameError("Room is full", 409)
        if self.phase == "winner":
            raise GameError("This room already picked its movie", 409)
        self.last_action = now
        p = Player(secrets.token_urlsafe(8), name, now, now, cursor=self.resolved)
        self.players[p.id] = p
        if self.host_id is None:
            self.host_id = p.id
        return p

    def leave(self, pid: str, now: float) -> None:
        self._act(pid, now)
        del self.players[pid]
        self.pair_votes.pop(pid, None)
        if self.host_id == pid:
            rest = sorted(self.players.values(), key=lambda p: p.joined)
            self.host_id = rest[0].id if rest else None
        if not self.players:
            self.phase = "closed"
        else:
            self._progress(now)

    def _progress(self, now: float) -> None:
        if self.phase == "genres" and self._everyone_ready(now):
            self._begin_swiping()
        elif self.phase == "swiping":
            self._resolve(now)
        elif self.phase == "tournament":
            self._resolve_pair(now)

    def _everyone_ready(self, now: float) -> bool:
        active = self.active(now)
        return bool(active) and all(p.ready for p in active)

    def _quorum(self, active: list[Player]) -> bool:
        return len(active) >= min(2, len(self.players))

    def seen(self, pid: str, now: float) -> None:
        self._player(pid).last_seen = now

    def active(self, now: float) -> list[Player]:
        return [p for p in self.players.values() if now - p.last_seen <= AWAY]

    def tick(self, now: float) -> None:
        if self.phase == "closed":
            return
        if now - self.last_action >= IDLE_CLOSE:
            self.phase = "closed"
            return
        self._progress(now)
        host = self.players.get(self.host_id)
        if host is None or now - host.last_seen > AWAY:
            active = sorted(self.active(now), key=lambda p: p.joined)
            if active:
                self.host_id = active[0].id

    def start(self, pid: str, now: float) -> None:
        self._act(pid, now)
        self._host_only(pid)
        if self.phase == "lobby":
            self.phase = "genres"

    def force_start(self, pid: str, now: float) -> None:
        self._act(pid, now)
        self._host_only(pid)
        if self.phase == "genres":
            self._begin_swiping()

    def set_genres(self, pid: str, genre_ids: list[int], now: float) -> None:
        p = self._act(pid, now)
        if self.phase not in ("lobby", "genres", "swiping"):
            raise GameError("Too late to change genres", 409)
        valid = isinstance(genre_ids, list) and len(genre_ids) <= MAX_GENRES and all(
            isinstance(g, int) and not isinstance(g, bool) for g in genre_ids)
        if not valid:
            raise GameError("Bad genre list", 400)
        p.genres = sorted(set(genre_ids))

    def set_ready(self, pid: str, now: float) -> None:
        p = self._act(pid, now)
        if self.phase != "genres":
            raise GameError("Not picking genres right now", 409)
        p.ready = True
        if self._everyone_ready(now):
            self._begin_swiping()

    def _begin_swiping(self) -> None:
        self.phase = "swiping"

    def add_cards(self, movies: list[Movie]) -> None:
        have = {m.id for m in self.deck}
        for m in movies:
            if m.id not in have and len(self.deck) < MAX_DECK:
                self.deck.append(m)
                have.add(m.id)

    def _cursor(self, p: Player) -> int:
        return max(p.cursor, self.resolved)

    def needs_cards(self, now: float) -> bool:
        if self.phase != "swiping" or len(self.deck) >= MAX_DECK:
            return False
        cursors = [self._cursor(p) for p in self.active(now)] or [self.resolved]
        return max(cursors) + 10 >= len(self.deck)

    def swipe(self, pid: str, index: int, yes: bool, now: float) -> None:
        p = self._act(pid, now)
        if self.phase != "swiping":
            raise GameError("Not swiping right now", 409)
        c = self._cursor(p)
        if index != c or c >= len(self.deck) or c >= self.resolved + MAX_LEAD:
            return
        self.votes.setdefault(c, {})[pid] = bool(yes)
        p.cursor = c + 1
        self._resolve(now)

    def _resolve(self, now: float) -> None:
        while self.phase == "swiping" and self.resolved < len(self.deck):
            active = self.active(now)
            votes = self.votes.get(self.resolved, {})
            if not self._quorum(active) or any(p.id not in votes for p in active):
                return
            yes = sum(votes[p.id] for p in active)
            movie = self.deck[self.resolved]
            self.events.append(("tally", movie, (yes, len(active) - yes)))
            self.resolved += 1
            if yes >= threshold(len(active), self.relaxed):
                self.shortlist.append(movie)
                self.no_match_streak = 0
                self.last_match = {"seq": len(self.shortlist), "title": movie.title, "yes": yes, "of": len(active)}
                if len(self.shortlist) >= SHORTLIST_SIZE:
                    self._begin_tournament(now)
            else:
                self.no_match_streak += 1
                if self.no_match_streak >= RELAX_AFTER and not self.relaxed:
                    self.relaxed = True
                    self.notice = "Loosening up: majority now counts"
        if self.phase == "swiping" and self.resolved >= MAX_DECK:
            if self.shortlist:
                self._begin_tournament(now)
            else:
                self.phase = "closed"

    def start_tournament(self, pid: str, now: float) -> None:
        self._act(pid, now)
        self._host_only(pid)
        if self.phase != "swiping" or not self.shortlist:
            raise GameError("Need at least one match first", 409)
        self._begin_tournament(now)

    def _begin_tournament(self, now: float) -> None:
        e = self.shortlist[:SHORTLIST_SIZE]
        self.phase = "tournament"
        if len(e) == 1:
            self._finish(e[0])
        elif len(e) == 2:
            self.matches = [self._match(e[0], e[1], "Final")]
        elif len(e) == 3:
            self.bye = max(e, key=lambda m: (m.rating, -e.index(m)))
            a, b = [m for m in e if m is not self.bye]
            self.matches = [self._match(a, b, "Semi-final")]
        else:
            self.matches = [self._match(e[0], e[1], "Semi-final 1 of 2"),
                            self._match(e[2], e[3], "Semi-final 2 of 2")]

    @staticmethod
    def _match(a: Movie, b: Movie, name: str) -> dict:
        return {"a": a, "b": b, "round": name, "winner": None}

    @property
    def current(self) -> dict | None:
        return next((m for m in self.matches if m["winner"] is None), None)

    @property
    def pair(self) -> tuple[Movie, Movie] | None:
        m = self.current
        return (m["a"], m["b"]) if m else None

    def vote(self, pid: str, movie_id: int, now: float, match: int | None = None) -> None:
        self._act(pid, now)
        m = self.current
        if self.phase != "tournament" or m is None:
            raise GameError("No vote right now", 409)
        if match is not None and match != self.matches.index(m):
            return
        if movie_id not in (m["a"].id, m["b"].id):
            raise GameError("Pick one of the two movies", 400)
        self.pair_votes[pid] = movie_id
        self._resolve_pair(now)

    def _resolve_pair(self, now: float) -> None:
        m = self.current
        active = self.active(now)
        if m is None or not self._quorum(active) or any(p.id not in self.pair_votes for p in active):
            return
        va = sum(self.pair_votes[p.id] == m["a"].id for p in active)
        vb = len(active) - va
        order = [x.id for x in self.shortlist]
        if va != vb:
            m["winner"] = m["a"] if va > vb else m["b"]
        else:
            m["winner"] = max((m["a"], m["b"]), key=lambda x: (x.rating, -order.index(x.id)))
        self.pair_votes = {}
        if self.current is not None:
            return
        if m["round"] == "Final":
            self._finish(m["winner"])
        else:
            finalists = [x["winner"] for x in self.matches] + ([self.bye] if self.bye else [])
            self.matches.append(self._match(finalists[0], finalists[1], "Final"))

    def _finish(self, movie: Movie) -> None:
        self.winner = movie
        self.phase = "winner"
        self.events.append(("pick", movie, None))

    def drain_events(self) -> list[tuple]:
        events, self.events = self.events, []
        return events

    def genre_weights(self, now: float) -> dict[int, int]:
        w: dict[int, int] = {}
        for p in self.active(now):
            for g in p.genres:
                w[g] = w.get(g, 0) + 1
        return w

    def close(self, pid: str, now: float) -> None:
        self._act(pid, now)
        self._host_only(pid)
        self.phase = "closed"

    def state_for(self, pid: str, now: float) -> dict:
        me = self._player(pid)
        return {
            "code": self.code,
            "phase": self.phase,
            "me": {"name": me.name, "genres": me.genres, "ready": me.ready},
            "host": pid == self.host_id,
            "players": [{"name": p.name, "ready": p.ready, "host": p.id == self.host_id,
                         "away": now - p.last_seen > AWAY}
                        for p in sorted(self.players.values(), key=lambda p: p.joined)],
            "max_players": MAX_PLAYERS,
            "solo": self.solo,
            "shortlist": [m.public() for m in self.shortlist],
            "relaxed": self.relaxed,
            "notice": self.notice,
            "last_match": self.last_match,
            **self._swipe_view(me),
            **self._tournament_view(pid, now),
        }

    def _tournament_view(self, pid: str, now: float) -> dict:
        m = self.current if self.phase == "tournament" else None
        active = self.active(now)
        return {
            "pair": [m["a"].public(), m["b"].public()] if m else None,
            "match": self.matches.index(m) if m else None,
            "round_name": m["round"] if m else None,
            "my_vote": self.pair_votes.get(pid),
            "voted": sum(p.id in self.pair_votes for p in active),
            "of": len(active),
            "bracket": [{"a": x["a"].title, "b": x["b"].title, "round": x["round"],
                         "winner": x["winner"].title if x["winner"] else None} for x in self.matches],
            "winner": self.winner.public() if self.winner else None,
        }

    def _swipe_view(self, me: Player) -> dict:
        if self.phase != "swiping":
            return {"cards": [], "ahead": False, "loading": False}
        c = self._cursor(me)
        ahead = c >= self.resolved + MAX_LEAD or c >= MAX_DECK
        loading = not ahead and c >= len(self.deck)
        cards = [] if ahead or loading else [dict(m.public(), index=i) for i, m in enumerate(self.deck[c:c + 5], start=c)]
        return {"cards": cards, "ahead": ahead, "loading": loading}
