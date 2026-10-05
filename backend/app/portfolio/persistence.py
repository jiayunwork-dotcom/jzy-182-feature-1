"""SQLite persistence for portfolios.

The portfolio tables live in the *same* SQLite file as the projects/reports
tables (so one volume backs everything and one restart restores everything),
but the schema and all queries are isolated here — the single-project
:mod:`app.storage` module is untouched.

Concurrency follows the same contract as report submission: one process-wide
lock serialises every mutating transaction, and each mutation opens
``BEGIN IMMEDIATE`` so writers across connections/processes queue at the
database too. Database constraints are the last line of defence:

- ``PRIMARY KEY (portfolio_id, project_id)`` makes a double-join impossible;
- ``FOREIGN KEY (project_id) REFERENCES projects(id)`` makes joining a
  nonexistent project impossible;
- pool resources have ``PRIMARY KEY (portfolio_id, name)``.

Offsets, membership order (``position``) and the resolved drum are all stored
explicitly, so restart restores the portfolio byte-for-byte without any
recomputation that could drift.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .models import Member, PoolResource


class DuplicateMembership(Exception):
    pass


class PortfolioStore:
    def __init__(self, db_path: str):
        # Same fallback logic as app.storage: trust the given path, the
        # project store already created/probed its directory.
        self.db_path = db_path
        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialize all writers in-process; take the DB write lock eagerly."""
        conn = self._connect()
        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    def insert_portfolio(
        self,
        conn: sqlite3.Connection,
        portfolio_id: str,
        name: str,
        drum_mode: str,
        drum: str,
        resources: list[PoolResource],
    ) -> None:
        conn.execute(
            "INSERT INTO portfolios (id, name, drum, drum_mode) VALUES (?, ?, ?, ?)",
            (portfolio_id, name, drum, drum_mode),
        )
        for i, r in enumerate(resources):
            conn.execute(
                "INSERT INTO portfolio_resources (portfolio_id, name, capacity, position) "
                "VALUES (?, ?, ?, ?)",
                (portfolio_id, r.name, r.capacity, i),
            )

    def replace_resources(self, conn: sqlite3.Connection, portfolio_id: str, resources: list[PoolResource]) -> None:
        conn.execute("DELETE FROM portfolio_resources WHERE portfolio_id = ?", (portfolio_id,))
        for i, r in enumerate(resources):
            conn.execute(
                "INSERT INTO portfolio_resources (portfolio_id, name, capacity, position) "
                "VALUES (?, ?, ?, ?)",
                (portfolio_id, r.name, r.capacity, i),
            )

    def update_portfolio_row(
        self, conn: sqlite3.Connection, portfolio_id: str, name: str, drum: str, drum_mode: str
    ) -> None:
        conn.execute(
            "UPDATE portfolios SET name = ?, drum = ?, drum_mode = ? WHERE id = ?",
            (name, drum, drum_mode, portfolio_id),
        )

    def list_portfolio_rows(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.drum, p.drum_mode, p.created_at,
                   (SELECT COUNT(*) FROM portfolio_members m WHERE m.portfolio_id = p.id) AS member_count
            FROM portfolios p
            ORDER BY p.created_at, p.id
            """
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["resources"] = self.get_resources(conn, d["id"])
            result.append(d)
        return result

    def get_resources(self, conn: sqlite3.Connection, portfolio_id: str) -> list[PoolResource]:
        rows = conn.execute(
            "SELECT name, capacity FROM portfolio_resources WHERE portfolio_id = ? ORDER BY position",
            (portfolio_id,),
        ).fetchall()
        return [PoolResource(name=r["name"], capacity=r["capacity"]) for r in rows]

    def get_portfolio_row(self, conn: sqlite3.Connection, portfolio_id: str) -> Optional[dict[str, Any]]:
        row = conn.execute(
            "SELECT * FROM portfolios WHERE id = ?", (portfolio_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def get_members(self, conn: sqlite3.Connection, portfolio_id: str) -> list[Member]:
        rows = conn.execute(
            """
            SELECT m.project_id, m.shared_json, m.offset, m.position, pr.name AS project_name
            FROM portfolio_members m
            JOIN projects pr ON pr.id = m.project_id
            WHERE m.portfolio_id = ?
            ORDER BY m.position
            """,
            (portfolio_id,),
        ).fetchall()
        return [
            Member(
                project_id=r["project_id"],
                project_name=r["project_name"],
                shared_resources=json.loads(r["shared_json"]),
                offset=r["offset"],
                position=r["position"],
                makespan=0.0,
            )
            for r in rows
        ]

    def is_member(self, conn: sqlite3.Connection, portfolio_id: str, project_id: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM portfolio_members WHERE portfolio_id = ? AND project_id = ?",
            (portfolio_id, project_id),
        ).fetchone()
        return row is not None

    def next_position(self, conn: sqlite3.Connection, portfolio_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS n FROM portfolio_members WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()
        return int(row["n"])

    def add_member(
        self,
        conn: sqlite3.Connection,
        portfolio_id: str,
        project_id: str,
        shared_resources: dict[str, str],
        offset: float,
        position: int,
    ) -> None:
        try:
            conn.execute(
                "INSERT INTO portfolio_members (portfolio_id, project_id, shared_json, offset, position) "
                "VALUES (?, ?, ?, ?, ?)",
                (portfolio_id, project_id, json.dumps(shared_resources), offset, position),
            )
        except sqlite3.IntegrityError as exc:
            msg = str(exc)
            if "UNIQUE" in msg.upper():
                raise DuplicateMembership(project_id) from exc
            raise


_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolios (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    drum TEXT NOT NULL DEFAULT '',
    drum_mode TEXT NOT NULL DEFAULT 'auto',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS portfolio_resources (
    portfolio_id TEXT NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    capacity REAL NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (portfolio_id, name)
);
CREATE TABLE IF NOT EXISTS portfolio_members (
    portfolio_id TEXT NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id),
    shared_json TEXT NOT NULL,
    offset REAL NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (portfolio_id, project_id)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_members_order
    ON portfolio_members(portfolio_id, position);
"""
