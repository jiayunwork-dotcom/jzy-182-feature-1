"""Fever chart: critical-chain completion vs. project-buffer consumption.

For every prefix of the report stream (replayed in deterministic order, see
:mod:`reports`) one point is produced:

* x = critical-chain completion percent. Earned workdays on CC tasks
  (completed -> aggressive duration; in progress -> aggressive - remaining,
  floored at 0) divided by the total aggressive CC content.
* y = project-buffer consumption percent. CC tasks accrue workday ``slip``
  against the plan pace (see :mod:`state`); the summed CC slip is what the
  project buffer absorbs. ``100 * max(0, slip) / buffer_size``; the value may
  exceed 100 (buffer burned through).

Zones are split by two rays from the origin set per project: on or below the
yellow ray is green, on or above the red ray is red, between is yellow. The
rays run from (0%,0%) toward (100%, yellow_line) and (100%, red_line). A
historical query at time T is the exact same projection as replaying only
reports with timestamp <= T, so every point matches re-running the world up
to then.

The critical chain used here is always the chain from the frozen plan — it is
never re-identified during execution (see README for the rationale and the
effect on the curve). Feeding buffers get the same slip/consumption view for
their own feeding task set (the direct non-CC predecessors of the entry
task); the main chart uses the project buffer.
"""
from __future__ import annotations

from typing import Any, Optional

from .models import Plan
from .reports import Report
from .state import project_state


def _cc_totals(pr) -> tuple[float, float]:
    total = 0.0
    earned = 0.0
    for t in pr.plan.tasks:
        if not t.is_critical:
            continue
        total += t.aggressive
        rt = pr.tasks[t.name]
        if rt.status == "completed":
            earned += t.aggressive
        elif rt.status == "in_progress":
            earned += max(0.0, t.aggressive - (rt.remaining or 0.0))
    return total, earned


def _project_buffer(plan: Plan) -> float:
    for b in plan.buffers:
        if b.kind == "project":
            return b.size
    return 0.0


def _ray_zone(cc_percent: float, buffer_percent: float, yellow: float, red: float) -> str:
    yellow_ray = (yellow / 100.0) * cc_percent
    red_ray = (red / 100.0) * cc_percent
    if buffer_percent <= yellow_ray + 1e-9:
        return "green"
    if buffer_percent >= red_ray - 1e-9:
        return "red"
    return "yellow"


def _task_states(plan: Plan, pr) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "is_critical": t.is_critical,
            "status": pr.tasks[t.name].status,
            "remaining": pr.tasks[t.name].remaining,
            "slip": round(pr.tasks[t.name].slip, 6),
            "velocity": pr.tasks[t.name].velocity,
            "started_at": pr.tasks[t.name].started_at,
            "completed_at": pr.tasks[t.name].completed_at,
            "last_timestamp": pr.tasks[t.name].last_timestamp,
        }
        for t in plan.tasks
    ]


def _feeding(plan: Plan, pr, yellow: float, red: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    by_name = {t.name: t for t in plan.tasks}
    for b in plan.buffers:
        if b.kind != "feeding":
            continue
        feeders = [
            t
            for t in plan.tasks
            if (not t.is_critical) and b.feeding_into in t.predecessors
        ]
        slip = sum(pr.tasks[t.name].slip for t in feeders)
        total = sum(t.aggressive for t in feeders)
        earned = 0.0
        for t in feeders:
            rt = pr.tasks[t.name]
            if rt.status == "completed":
                earned += t.aggressive
            elif rt.status == "in_progress":
                earned += max(0.0, t.aggressive - (rt.remaining or 0.0))
        pct = 100.0 * max(0.0, slip) / b.size if b.size > 1e-9 else (100.0 if slip > 1e-9 else 0.0)
        comp = 100.0 * earned / total if total > 1e-9 else 0.0
        out.append(
            {
                "feeding_into": b.feeding_into,
                "feeding_from": b.feeding_from,
                "buffer_size": b.size,
                "slip": round(slip, 6),
                "percent": round(pct, 6),
                "completion_percent": round(comp, 6),
                "zone": _ray_zone(comp, pct, yellow, red),
            }
        )
    return out


def compute_point(
    plan: Plan,
    reports: list[Report],
    yellow: float,
    red: float,
    point_report_id: Optional[str] = None,
    point_timestamp: Optional[str] = None,
) -> dict[str, Any]:
    pr = project_state(plan, reports)
    total, earned = _cc_totals(pr)
    cc_percent = 100.0 * earned / total if total > 1e-9 else 0.0
    cc_slip = sum(rt.slip for rt in pr.tasks.values() if rt.is_critical)
    pb = _project_buffer(plan)
    if pb > 1e-9:
        buffer_percent = 100.0 * max(0.0, cc_slip) / pb
    else:
        buffer_percent = 100.0 if cc_slip > 1e-9 else 0.0

    zone = _ray_zone(cc_percent, buffer_percent, yellow, red)
    if cc_percent <= 1e-9 and buffer_percent > 1e-9:
        zone = "red"

    return {
        "report_id": point_report_id if point_report_id is not None else pr.last_report_id,
        "timestamp": point_timestamp if point_timestamp is not None else pr.last_timestamp,
        "cc_percent": round(cc_percent, 6),
        "buffer_percent": round(buffer_percent, 6),
        "zone": zone,
        "slip": round(cc_slip, 6),
        "task_states": _task_states(plan, pr),
        "feeding": _feeding(plan, pr, yellow, red),
    }


_INITIAL_POINT_TEMPLATE: list = []


def trajectory(plan: Plan, reports: list[Report], yellow: float, red: float) -> list[dict[str, Any]]:
    """One point after each report, in deterministic stream order.

    Same-(task, timestamp) reports are collapsed in the projection, but each
    submitted report still gets its own point, labelled with that report's id
    and timestamp so the UI can show every submission.
    """
    from .reports import order_reports
    from .state import effective_reports_up_to

    ordered = order_reports(reports)
    initial = {
        "report_id": None,
        "timestamp": None,
        "cc_percent": 0.0,
        "buffer_percent": 0.0,
        "zone": "green",
        "slip": 0.0,
        "task_states": [
            {
                "name": t.name,
                "is_critical": t.is_critical,
                "status": "pending",
                "remaining": None,
                "slip": 0.0,
                "velocity": None,
                "started_at": None,
                "completed_at": None,
                "last_timestamp": None,
            }
            for t in plan.tasks
        ],
        "feeding": [],
    }
    points = [initial]
    for i, r in enumerate(ordered):
        prefix = effective_reports_up_to(ordered, i)
        points.append(
            compute_point(
                plan, prefix, yellow, red,
                point_report_id=r.report_id, point_timestamp=r.timestamp,
            )
        )
    return points


def replay_at(plan: Plan, reports: list[Report], yellow: float, red: float, timestamp: str) -> dict[str, Any]:
    """State as if only reports up to and including ``timestamp`` existed."""
    from .calendar import to_utc

    cutoff = to_utc(timestamp).isoformat()
    filtered = [r for r in reports if r.timestamp <= cutoff]
    return compute_point(plan, filtered, yellow, red)


def current(plan: Plan, reports: list[Report], yellow: float, red: float) -> dict[str, Any]:
    return compute_point(plan, reports, yellow, red)
