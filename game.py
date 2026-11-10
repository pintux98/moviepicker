"""Moviepicker room rules. Pure logic: no I/O, the caller passes `now` (seconds)."""
import math
import secrets
from dataclasses import asdict, dataclass, field

MAX_PLAYERS = 10
MAX_ROOMS = 30
IDLE_CLOSE = 600      # seconds without any player action -> room closes
AWAY = 60             # seconds without polling -> player stops counting
SHORTLIST_SIZE = 4    # matches that start the tournament
RELAX_AFTER = 25      # resolved cards in a row without a match -> majority counts
MAX_LEAD = 15         # how far a player may swipe ahead of the group
NAME_MAX = 20


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


def threshold(n: int, relaxed: bool) -> int:
    """Yes-votes a card needs to be a match with n active players."""
    if n <= 2:
        return n
    if relaxed:
        return n // 2 + 1
    return math.ceil(2 * n / 3)


class Room:
    def __init__(self, code: str, now: float):
        self.code = code
        self.phase = "lobby"
        self.players: dict[str, Player] = {}
        self.host_id: str | None = None
        self.last_action = now
        self.deck: list[Movie] = []
        self.votes: dict[int, dict[str, bool]] = {}   # card index -> player id -> yes
        self.resolved = 0                               # cards decided so far, in order
        self.shortlist: list[Movie] = []
        self.relaxed = False
        self.no_match_streak = 0
        self.notice: str | None = None
        self.last_match: dict | None = None

    # --- helpers -------------------------------------------------------------
    def _player(self, pid: str) -> Player:
        if pid not in self.players:
            raise GameError("You're not in this room", 403)
        return self.players[pid]

    def _act(self, pid: str | None, now: float) -> Player | None:
        """Every player action: room must be open, counts as activity and as a heartbeat."""
        if self.phase == "closed":
            raise GameError("This room is closed", 404)
        self.last_action = now
        if pid is None:
            return None
        p = self._player(pid)
        p.last_seen = now
        return p

    def _host_only(self, pid: str):
        if pid != self.host_id:
            raise GameError("Only the host can do that", 403)

    # --- players -------------------------------------------------------------
    def add_player(self, name: str, now: float) -> Player:
        self._act(None, now)
        name = (name or "").strip()[:NAME_MAX]
        if not name:
            raise GameError("Pick a name", 400)
        if len(self.players) >= MAX_PLAYERS:
            raise GameError("Room is full", 409)
        if self.phase == "winner":
            raise GameError("This room already picked its movie", 409)
        p = Player(secrets.token_urlsafe(8), name, now, now, cursor=self.resolved)
        self.players[p.id] = p
        if self.host_id is None:
            self.host_id = p.id
        return p

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
        if self.phase == "swiping":
            self._resolve(now)  # someone going away can complete a card
        host = self.players.get(self.host_id)
        if host is None or now - host.last_seen > AWAY:
            active = sorted(self.active(now), key=lambda p: p.joined)
            if active:
                self.host_id = active[0].id

    # --- lobby / genres --------------------------------------------------------
    def start(self, pid: str, now: float) -> None:
        self._act(pid, now)
        self._host_only(pid)
        if self.phase == "lobby":
            self.phase = "genres"
        elif self.phase == "genres":
            self._begin_swiping()
        else:
            raise GameError("Already started", 409)

    def set_genres(self, pid: str, genre_ids: list[int], now: float) -> None:
        p = self._act(pid, now)
        p.genres = sorted({int(g) for g in genre_ids})

    def set_ready(self, pid: str, now: float) -> None:
        p = self._act(pid, now)
        if self.phase != "genres":
            raise GameError("Not picking genres right now", 409)
        p.ready = True
        if all(q.ready for q in self.active(now)):
            self._begin_swiping()

    def _begin_swiping(self) -> None:
        self.phase = "swiping"

    # --- swiping -------------------------------------------------------------
    def add_cards(self, movies: list[Movie]) -> None:
        have = {m.id for m in self.deck}
        for m in movies:
            if m.id not in have:
                self.deck.append(m)
                have.add(m.id)

    def _cursor(self, p: Player) -> int:
        return max(p.cursor, self.resolved)

    def needs_cards(self, now: float) -> bool:
        if self.phase not in ("genres", "swiping"):
            return False
        cursors = [self._cursor(p) for p in self.active(now)] or [self.resolved]
        return max(cursors) + 10 >= len(self.deck)

    def swipe(self, pid: str, index: int, yes: bool, now: float) -> None:
        p = self._act(pid, now)
        if self.phase != "swiping":
            raise GameError("Not swiping right now", 409)
        c = self._cursor(p)
        if index != c or c >= len(self.deck) or c >= self.resolved + MAX_LEAD:
            return  # double tap, stale screen or too far ahead: ignore
        self.votes.setdefault(c, {})[pid] = bool(yes)
        p.cursor = c + 1
        self._resolve(now)

    def _resolve(self, now: float) -> None:
        """Decide cards in order once every active player has voted on them."""
        while self.phase == "swiping" and self.resolved < len(self.deck):
            active = self.active(now)
            votes = self.votes.get(self.resolved, {})
            if not active or any(p.id not in votes for p in active):
                return
            yes = sum(votes[p.id] for p in active)
            movie = self.deck[self.resolved]
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

    def _begin_tournament(self, now: float) -> None:
        self.phase = "tournament"

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

    # --- view --------------------------------------------------------------
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
            "shortlist": [m.public() for m in self.shortlist],
            "relaxed": self.relaxed,
            "notice": self.notice,
            "last_match": self.last_match,
            **self._swipe_view(me),
        }

    def _swipe_view(self, me: Player) -> dict:
        if self.phase != "swiping":
            return {"cards": [], "ahead": False}
        c = self._cursor(me)
        ahead = c >= self.resolved + MAX_LEAD or c >= len(self.deck)
        cards = [] if ahead else [dict(m.public(), index=i) for i, m in enumerate(self.deck[c:c + 5], start=c)]
        return {"cards": cards, "ahead": ahead}
