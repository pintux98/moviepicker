import sqlite3
import threading

from game import Movie

SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    year INTEGER,
    rating REAL,
    overview TEXT,
    poster TEXT,
    runtime INTEGER,
    provider TEXT,
    picked INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    dislikes INTEGER NOT NULL DEFAULT 0,
    last_picked REAL
)
"""
UPSERT = """
INSERT INTO movies (id, title, year, rating, overview, poster, runtime, provider)
VALUES (:id, :title, :year, :rating, :overview, :poster, :runtime, :provider)
ON CONFLICT(id) DO UPDATE SET title = excluded.title, year = excluded.year, rating = excluded.rating,
    overview = excluded.overview, poster = excluded.poster,
    runtime = COALESCE(excluded.runtime, runtime), provider = COALESCE(excluded.provider, provider)
"""
COLUMNS = "id, title, year, rating, overview, poster, runtime, provider, picked, likes, dislikes, last_picked"


class Stats:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute(SCHEMA)

    def _upsert(self, m: Movie) -> None:
        self.db.execute(UPSERT, m.public())

    def record_vote(self, m: Movie, yes: bool) -> None:
        column = "likes" if yes else "dislikes"
        with self.lock, self.db:
            self._upsert(m)
            self.db.execute("UPDATE movies SET %s = %s + 1 WHERE id = ?" % (column, column), (m.id,))

    def record_pick(self, m: Movie, now: float) -> None:
        with self.lock, self.db:
            self._upsert(m)
            self.db.execute("UPDATE movies SET picked = picked + 1, last_picked = ? WHERE id = ?", (now, m.id))

    def _rows(self, order: str, limit: int) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT %s FROM movies WHERE picked > 0 ORDER BY %s LIMIT ?" % (COLUMNS, order),
                                   (limit,)).fetchall()
        return [dict(r) for r in rows]

    def top(self, limit: int = 5) -> list[dict]:
        return self._rows("picked DESC, last_picked DESC", limit)

    def recent(self, limit: int = 5) -> list[dict]:
        return self._rows("last_picked DESC", limit)

    def close(self) -> None:
        self.db.close()
