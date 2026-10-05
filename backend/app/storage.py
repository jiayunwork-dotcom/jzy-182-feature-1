"""SQLite persistence on the mounted volume.

Schema: projects hold the raw input plus the computed plan; reports are the
append-only log. Report ids are unique per project at the database level
(UNIQUE(project_id, report_id)) so concurrent duplicate submissions cannot
both succeed. All writes are serialized with a process lock, which together
with SQLite WAL gives the invariant: concurrent submissions end in exactly
the state produced by applying reports one-by-one in (timestamp, report_id)
order.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Optional

from .reports import Report

DEFAULT_DB_PATH = os.environ.get("CCPM_DB_PATH", "/data/ccpm.db")


def resolve_db_path(path: str = DEFAULT_DB_PATH) -> str:
    """Use ``path`` if its directory is writable/creatable; else fall back.

    The container mounts a writable volume at /data; on a developer machine
    without /data, a local ./data directory is used instead.
    """
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        probe = os.path.join(directory, ".write-probe")
        with open(probe, "w"):
            pass
        os.remove(probe)
        return path
    except OSError:
        local = os.path.join(os.getcwd(), "data", os.path.basename(path))
        os.makedirs(os.path.dirname(local), exist_ok=True)
        return local

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    input_json TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    yellow_line REAL NOT NULL,
    red_line REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    report_id TEXT NOT NULL,
    task TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    remaining REAL,
    seq INTEGER NOT NULL,
    UNIQUE(project_id, report_id)
);
CREATE INDEX IF NOT EXISTS idx_reports_project ON reports(project_id, timestamp, report_id);
CREATE TABLE IF NOT EXISTS portfolios (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    pool_json TEXT NOT NULL,
    drum_resource TEXT,
    auto_drum TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS portfolio_members (
    portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    start_offset REAL NOT NULL,
    join_seq INTEGER NOT NULL,
    joined_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(portfolio_id, project_id),
    UNIQUE(portfolio_id, join_seq)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_members_portfolio
    ON portfolio_members(portfolio_id, join_seq);
"""


class DuplicateReport(Exception):
    def __init__(self, report_id: str):
        self.report_id = report_id
        super().__init__(f"duplicate report id {report_id!r}")


class Storage:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = resolve_db_path(db_path)
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._connect().close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ #
    def save_project(
        self,
        project_id: str,
        name: str,
        raw_input: dict[str, Any],
        plan: dict[str, Any],
        yellow: float,
        red: float,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO projects (id, name, input_json, plan_json, yellow_line, red_line) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, name, json.dumps(raw_input), json.dumps(plan), yellow, red),
            )

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, yellow_line, red_line, created_at FROM projects ORDER BY created_at, id"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_project(self, project_id: str) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["input"] = json.loads(d.pop("input_json"))
        d["plan"] = json.loads(d.pop("plan_json"))
        return d

    # ------------------------------------------------------------------ #
    def add_report(self, project_id: str, report: Report) -> bool:
        """Append a report. Returns False (no write) if the id already exists."""
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM reports WHERE project_id = ? AND report_id = ?",
                (project_id, report.report_id),
            ).fetchone()
            if existing is not None:
                return False
            conn.execute(
                "INSERT INTO reports (project_id, report_id, task, timestamp, remaining, seq) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    report.report_id,
                    report.task,
                    report.timestamp,
                    report.remaining,
                    report.sequence,
                ),
            )
        return True

    def get_reports(self, project_id: str) -> list[Report]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT report_id, task, timestamp, remaining, seq FROM reports "
                "WHERE project_id = ? ORDER BY timestamp, report_id",
                (project_id,),
            ).fetchall()
        return [
            Report(
                report_id=r["report_id"],
                task=r["task"],
                timestamp=r["timestamp"],
                remaining=r["remaining"],
                sequence=r["seq"],
            )
            for r in rows
        ]

    def next_seq(self, project_id: str) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM reports WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------------ #
    # Portfolios
    # ------------------------------------------------------------------ #
    @contextmanager
    def transaction(self):
        """Serialize a read-modify-write unit (e.g. a portfolio join).

        The process-wide re-entrant lock makes concurrent joins execute one
        after another, so their combined result equals applying them in some
        sequential order and the drum can never be over-committed.
        """
        with self._lock:
            yield

    def save_portfolio(
        self,
        portfolio_id: str,
        name: str,
        pool: list[dict[str, Any]],
        drum_resource: Optional[str],
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO portfolios (id, name, pool_json, drum_resource) "
                "VALUES (?, ?, ?, ?)",
                (portfolio_id, name, json.dumps(pool), drum_resource),
            )

    def update_portfolio(
        self,
        portfolio_id: str,
        name: str,
        pool: list[dict[str, Any]],
        drum_resource: Optional[str],
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE portfolios SET name = ?, pool_json = ?, drum_resource = ? "
                "WHERE id = ?",
                (name, json.dumps(pool), drum_resource, portfolio_id),
            )

    def set_auto_drum(self, portfolio_id: str, drum: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE portfolios SET auto_drum = ? WHERE id = ?", (drum, portfolio_id)
            )

    def list_portfolios(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT p.id, p.name, p.pool_json, p.drum_resource, p.auto_drum, "
                "p.created_at, COUNT(m.project_id) AS member_count "
                "FROM portfolios p "
                "LEFT JOIN portfolio_members m ON m.portfolio_id = p.id "
                "GROUP BY p.id ORDER BY p.created_at, p.id"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["pool"] = json.loads(d.pop("pool_json"))
            out.append(d)
        return out

    def get_portfolio(self, portfolio_id: str) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM portfolios WHERE id = ?", (portfolio_id,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["pool"] = json.loads(d.pop("pool_json"))
        return d

    def get_portfolio_members(self, portfolio_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT project_id, start_offset, join_seq, joined_at "
                "FROM portfolio_members WHERE portfolio_id = ? ORDER BY join_seq",
                (portfolio_id,),
            ).fetchall()
        return [
            {
                "project_id": r["project_id"],
                "offset": r["start_offset"],
                "join_seq": r["join_seq"],
                "joined_at": r["joined_at"],
            }
            for r in rows
        ]

    def next_join_seq(self, portfolio_id: str) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(join_seq), -1) + 1 AS n FROM portfolio_members "
                "WHERE portfolio_id = ?",
                (portfolio_id,),
            ).fetchone()
        return int(row["n"])

    def add_portfolio_member(
        self, portfolio_id: str, project_id: str, offset: float, join_seq: int
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO portfolio_members "
                "(portfolio_id, project_id, start_offset, join_seq) VALUES (?, ?, ?, ?)",
                (portfolio_id, project_id, offset, join_seq),
            )
