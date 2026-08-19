"""TTL cache for Graph responses.

A creator marketplace polls the same accounts constantly, and Meta's limits are
per app, not per creator - so one noisy dashboard can throttle every read for
everyone. Caching by (account, metric) with a short TTL removes most of that
traffic; the cache also remembers *when* a value was fetched, which is what the
staleness check needs.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Cache:
    def __init__(self, path: str | Path, ttl_seconds: int = 3600) -> None:
        self.path = str(path)
        self.ttl = timedelta(seconds=ttl_seconds)
        self.hits = 0
        self.misses = 0
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def get(self, key: str, now: datetime | None = None) -> tuple[Any, datetime] | None:
        """Return (value, fetched_at) when the entry exists and is still fresh."""
        now = now or _now()
        with self._connect() as connection:
            row = connection.execute("SELECT value, fetched_at FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            self.misses += 1
            return None
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        if now - fetched_at > self.ttl:
            self.misses += 1
            return None
        self.hits += 1
        return json.loads(row["value"]), fetched_at

    def set(self, key: str, value: Any, now: datetime | None = None) -> datetime:
        fetched_at = now or _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO cache VALUES (?,?,?)",
                (key, json.dumps(value, default=str), fetched_at.isoformat()),
            )
        return fetched_at

    def last_fetched(self, key: str) -> datetime | None:
        """When this key was last written, regardless of the TTL.

        Used to answer "how old is the newest data we ever got?" after a token
        dies - the answer decides fresh vs stale.
        """
        with self._connect() as connection:
            row = connection.execute("SELECT fetched_at FROM cache WHERE key = ?", (key,)).fetchone()
        return datetime.fromisoformat(row["fetched_at"]) if row else None

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}
