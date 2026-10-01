"""Planning-phase orchestration: validate input, schedule, chain, buffers."""
from __future__ import annotations

from typing import Any

from . import buffers, chain, scheduler
from .models import Plan, ProjectInput
from .validation import ValidationError, parse_input

RULE_DESCRIPTION = (
    "Serial schedule generation: ready tasks are picked by longest aggressive "
    "tail length (ties: lexicographically smallest task name) and placed at the "
    "earliest workday satisfying precedence and per-resource capacity; when "
    "capacity blocks them they are pushed to the finish of the earliest-finishing "
    "blocking task. The critical chain is the longest path over tight precedence "
    "edges plus final resource-release edges; ties are resolved by the "
    "lexicographically smallest task-name sequence. Buffers are half the sum of "
    "(conservative - aggressive) spreads on the chain."
)


def plan_project(raw: dict[str, Any] | ProjectInput) -> Plan:
    if isinstance(raw, ProjectInput):
        project = raw
    else:
        project = parse_input(raw)

    scheduled = scheduler.schedule(project)
    makespan = max((t.finish for t in scheduled), default=0.0)
    cc, _tight = chain.critical_chain(project, scheduled, makespan)
    cc_set = set(cc)
    for t in scheduled:
        t.is_critical = t.name in cc_set

    feeding = chain.feeding_chains(project, scheduled, cc_set)
    plan_buffers = buffers.build_buffers(project, scheduled, makespan, feeding)

    cp_len, cp = scheduler.critical_path(project)

    return Plan(
        project_name=project.name,
        makespan=makespan,
        critical_chain=cc,
        tasks=scheduled,
        buffers=plan_buffers,
        critical_path_length=cp_len,
        critical_path=cp,
        rule=RULE_DESCRIPTION,
    )


__all__ = ["plan_project", "ValidationError", "RULE_DESCRIPTION"]
