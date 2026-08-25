"""Storage ports keep local-first implementations independent from adapters."""

import json
import sqlite3
from pathlib import Path

from typing import Protocol


class SessionStorePort(Protocol):
    def save(self, session): ...
    def load(self, session_id): ...


class RunStorePort(Protocol):
    def append_trace(self, task_state, payload): ...


class MemoryStorePort(Protocol):
    def load(self): ...
    def save(self, state): ...


class CacheStorePort(Protocol):
    def get(self, key): ...
    def set(self, key, value): ...


class SQLiteSessionStore:
    """Optional local SQLite session store implementing the existing store shape."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def _connect(self):
        return sqlite3.connect(self.path)

    def save(self, session):
        payload = json.dumps(session, ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions(id, payload) VALUES(?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (session["id"], payload),
            )
        return self.path

    def load(self, session_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        if row is None:
            raise FileNotFoundError(session_id)
        return json.loads(row[0])

    def latest(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM sessions ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None
