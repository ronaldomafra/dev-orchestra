"""Estado do delegador em SQLite (modo WAL). Uma conexão, protegida por trava, com transações explícitas."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    role TEXT NOT NULL,
    cli TEXT NOT NULL,
    ref TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS sessions_project ON sessions (project, role, active);

CREATE TABLE IF NOT EXISTS tasks (
    project TEXT NOT NULL,
    task_id TEXT NOT NULL,
    state TEXT NOT NULL,
    to_role TEXT NOT NULL,
    orchestrator_session TEXT,
    session_id TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    model TEXT,
    effort TEXT,
    model_reason TEXT,
    user_approved INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project, task_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    type TEXT NOT NULL,
    from_role TEXT NOT NULL,
    from_session TEXT,
    to_role TEXT NOT NULL,
    to_session TEXT,
    task_id TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    body TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    claimed_by TEXT
);
CREATE INDEX IF NOT EXISTS messages_inbox ON messages (project, state, to_role, to_session);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    task_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    question TEXT NOT NULL,
    options TEXT,
    answer TEXT,
    state TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    agent TEXT NOT NULL,
    role TEXT,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    detail TEXT,
    task_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cleared_at TEXT
);
CREATE INDEX IF NOT EXISTS alerts_active ON alerts (project, cleared_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    project TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    role_from TEXT,
    role_to TEXT,
    task_id TEXT,
    state TEXT,
    attempt INTEGER,
    message_id INTEGER,
    latency_ms INTEGER
);
CREATE INDEX IF NOT EXISTS events_project ON events (project, id);
"""


class Store:
    """Acesso ao SQLite. `tx()` abre uma transação imediata (escritas serializadas)."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Bancos criados antes do monitor não têm as colunas novas de `events`."""
        have = {row["name"] for row in self._conn.execute("PRAGMA table_info(events)")}
        for column, kind in (
            ("role_from", "TEXT"), ("role_to", "TEXT"), ("task_id", "TEXT"), ("state", "TEXT"),
            ("attempt", "INTEGER"), ("message_id", "INTEGER"), ("latency_ms", "INTEGER"),
        ):  # fmt: skip
            if column not in have:
                self._conn.execute(f"ALTER TABLE events ADD COLUMN {column} {kind}")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def executescript(self, script: str) -> None:
        with self._lock:
            self._conn.executescript(script)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
