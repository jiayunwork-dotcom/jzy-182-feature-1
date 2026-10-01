"""Append-only progress report stream.

Persistence-independent rules (storage lives in :mod:`storage`):

* A report is one of: ``remaining`` (non-negative workdays left) or
  ``completed=true``. Unknown tasks are rejected at the API layer.
* Report ids are unique *per project*. Re-submitting the same id is
  idempotent: the stored row is returned unchanged, nothing is rewritten.
* Reports are **append-only** but may arrive out of timestamp order. Chosen
  behaviour: an early-timestamped report is *inserted into its correct
  position and the state is re-derived by replay* — never rejected, never
  duplicated. (The alternative, rejection, would lose corrections; replay
  keeps one consistent history.)
* The global, deterministic stream order is ``(timestamp, report_id)``.
  Concurrent submissions therefore converge to exactly the state obtained by
  applying reports one by one in that order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .calendar import to_utc


@dataclass(frozen=True)
class Report:
    report_id: str
    task: str
    timestamp: str  # normalized ISO-8601 UTC
    remaining: Optional[float] = None  # None <=> completed
    sequence: int = 0  # insertion order, only a last-resort tiebreaker

    def to_row(self) -> tuple[str, str, str, Optional[float], int]:
        return (self.report_id, self.task, self.timestamp, self.remaining, self.sequence)

    @property
    def sort_key(self) -> tuple:
        # Timestamp first; for reports at the exact same instant the
        # externally visible report id gives a deterministic order. Sequence
        # is the storage insertion order and only disambiguates impossible
        # collisions (a duplicate id would already have been rejected).
        return (self.timestamp, self.report_id)

    @property
    def is_completed(self) -> bool:
        return self.remaining is None


def order_reports(reports: list["Report"]) -> list["Report"]:
    return sorted(reports, key=lambda r: r.sort_key)


def validate_report_payload(payload: dict) -> list[dict[str, str]]:
    """Validate shape of an incoming report; returns field errors."""
    errors: list[dict[str, str]] = []
    rid = payload.get("report_id")
    if not isinstance(rid, str) or not rid.strip():
        errors.append({"field": "report_id", "message": "report id is required"})
    task = payload.get("task")
    if not isinstance(task, str) or not task.strip():
        errors.append({"field": "task", "message": "task name is required"})
    ts = payload.get("timestamp")
    if not isinstance(ts, str) or not ts.strip():
        errors.append({"field": "timestamp", "message": "ISO-8601 timestamp is required"})
    else:
        try:
            to_utc(ts)
        except (ValueError, TypeError):
            errors.append({"field": "timestamp", "message": "not a valid ISO-8601 timestamp"})

    completed = payload.get("completed", False)
    remaining = payload.get("remaining", None)
    if not isinstance(completed, bool):
        errors.append({"field": "completed", "message": "must be true or false"})
    if remaining is not None:
        if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
            errors.append({"field": "remaining", "message": "must be a number of workdays"})
        elif remaining < 0:
            errors.append({"field": "remaining", "message": "remaining duration must not be negative"})
    if not completed and remaining is None:
        errors.append(
            {"field": "remaining", "message": "required unless completed is true"}
        )
    return errors


def make_report(payload: dict, sequence: int) -> "Report":
    remaining = None if payload.get("completed") else float(payload["remaining"])
    return Report(
        report_id=payload["report_id"].strip(),
        task=payload["task"].strip(),
        timestamp=to_utc(payload["timestamp"]).isoformat(),
        remaining=remaining,
        sequence=sequence,
    )
