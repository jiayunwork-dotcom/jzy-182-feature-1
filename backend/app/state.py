"""Project current state projected from the report stream.

Every report is applied on top of the task's previous report. The resulting
per-task runtime records hold everything the fever chart needs:

* ``status``: pending | in_progress | completed
* ``remaining``: latest stated remaining work (workdays)
* ``baseline_remaining_at_start``: remaining work when the task first started
* ``velocity``: workdays of progress per elapsed *working* time, established
  by consecutive in-progress reports and frozen at completion
* ``slip``: accumulated workdays of lateness against that velocity; frozen at
  completion (see :mod:`fever` for how slip consumes the project buffer)

Velocity/slip semantics: between two reports for one task the task is assumed
to have worked continuously, consuming ``elapsed workdays`` of calendar and
``previous_remaining - new_remaining`` workdays of content. Progress faster
than calendar gives negative slip (ahead); slower gives positive slip.
Starting a task creates no slip. A later, larger remaining estimate (rework /
re-estimate) adds slip like slow progress. A completion arriving with no
prior in-progress report assumes on-time completion (slip 0).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .calendar import elapsed_workdays, to_utc
from .models import Plan
from .reports import Report, order_reports


@dataclass
class TaskRuntime:
    name: str
    aggressive: float
    conservative: float
    is_critical: bool
    status: str = "pending"
    remaining: Optional[float] = None
    velocity: Optional[float] = None  # aggressive planned pace until measured
    slip: float = 0.0
    last_timestamp: Optional[str] = None
    last_remaining: Optional[float] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    report_count: int = 0


@dataclass
class ProjectRuntime:
    plan: Plan
    tasks: dict[str, TaskRuntime] = field(default_factory=dict)
    last_report_id: Optional[str] = None
    last_timestamp: Optional[str] = None

    def ordered_report_ids(self, reports: list[Report]) -> list[str]:
        return [r.report_id for r in order_reports(reports)]


def _planned_velocity(t: TaskRuntime) -> float:
    # 1 workday of content per elapsed workday, i.e. aggressive plan pace
    return 1.0


def apply_report(rt: TaskRuntime, report: Report) -> None:
    """Mutate a task runtime according to one report.

    Several reports at the exact same timestamp for one task describe the
    same instant: the one latest in (timestamp, report_id) order is the
    authoritative snapshot, so zero elapsed time neither consumes nor adds
    work and produces no slip.
    """
    rt.report_count += 1
    ts = report.timestamp
    completed = report.is_completed
    remaining = report.remaining
    same_instant = rt.last_timestamp is not None and ts == rt.last_timestamp

    if rt.status == "pending":
        rt.started_at = ts
        if completed:
            # completed with no progress reports: treat as on-time
            rt.status = "completed"
            rt.remaining = 0.0
            rt.last_remaining = 0.0
            rt.velocity = 1.0
            rt.slip = 0.0
            rt.completed_at = ts
        else:
            rt.status = "in_progress"
            rt.remaining = remaining
            rt.last_remaining = remaining
    elif rt.status == "in_progress":
        if completed:
            if same_instant:
                pass
            else:
                elapsed = elapsed_workdays(to_utc(rt.last_timestamp), to_utc(ts))
                consumed = rt.last_remaining  # remaining content finished since last report
                rt.slip += elapsed - consumed
            rt.velocity = _frozen_velocity(rt, ts)
            rt.status = "completed"
            rt.remaining = 0.0
            rt.last_remaining = 0.0
            rt.completed_at = ts
        else:
            if same_instant:
                # later snapshot at the same instant simply replaces the view
                rt.remaining = remaining
                rt.last_remaining = remaining
            else:
                elapsed = elapsed_workdays(to_utc(rt.last_timestamp), to_utc(ts))
                consumed = rt.last_remaining - remaining  # negative if estimate grew
                if elapsed > 0:
                    rt.slip += elapsed - consumed
                    rt.velocity = max(0.0, consumed / elapsed) if consumed > 0 else 0.0
                rt.remaining = remaining
                rt.last_remaining = remaining
    else:  # already completed
        if completed:
            pass  # duplicate completion, nothing changes
        elif same_instant:
            pass  # same-instant noise cannot reopen a completed task
        else:
            # re-opened / rework report after completion: re-estimate adds slip
            rt.status = "in_progress"
            rt.completed_at = None
            elapsed = elapsed_workdays(to_utc(rt.last_timestamp), to_utc(ts))
            rt.slip += elapsed + remaining  # elapsed since last note + new work discovered
            rt.remaining = remaining
            rt.last_remaining = remaining
    rt.last_timestamp = ts


def _frozen_velocity(rt: TaskRuntime, completed_ts: str) -> float:
    started = to_utc(rt.started_at)
    elapsed = elapsed_workdays(started, to_utc(completed_ts))
    content = rt.aggressive
    if elapsed <= 0:
        return 1.0
    return max(0.0, content / elapsed)


def effective_reports_up_to(reports: list[Report], upto_index: int) -> list[Report]:
    """Reports in deterministic order up to ``upto_index`` (inclusive),
    collapsing multiple same-(task, timestamp) reports to the latest one.
    """
    ordered = order_reports(reports)[: upto_index + 1]
    effective: list[Report] = []
    for i, r in enumerate(ordered):
        later_same = any(
            q.task == r.task and q.timestamp == r.timestamp for q in ordered[i + 1:]
        )
        if not later_same:
            effective.append(r)
    return effective


def project_state(plan: Plan, reports: list[Report]) -> ProjectRuntime:
    """Replay reports in deterministic order onto fresh task runtimes.

    Multiple reports for the same task at the exact same timestamp are
    snapshots of one instant: only the last one in (timestamp, report_id)
    order is applied — applying earlier ones would register zero elapsed
    progress and fictitious slip.
    """
    runtimes = {
        t.name: TaskRuntime(
            name=t.name,
            aggressive=t.aggressive,
            conservative=t.conservative,
            is_critical=t.is_critical,
        )
        for t in plan.tasks
    }
    ordered = order_reports(reports)
    effective = effective_reports_up_to(reports, len(ordered) - 1) if ordered else []
    for r in effective:
        if r.task in runtimes:
            apply_report(runtimes[r.task], r)
    pr = ProjectRuntime(plan=plan, tasks=runtimes)
    if ordered:
        pr.last_report_id = ordered[-1].report_id
        pr.last_timestamp = ordered[-1].timestamp
    return pr
