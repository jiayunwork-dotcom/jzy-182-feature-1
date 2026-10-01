"""Buffer sizing and placement.

Sizing rule (documented in README):

    buffer = 1/2 * sum(conservative - aggressive) over the tasks on the chain

This is the classic "cut-and-paste" 50% rule: each task carries safety
equal to roughly half of its optimistic-to-safe spread; pasting the removed
safety into one pooled buffer at the end of the chain aggregates it. This
rule is *exactly homogeneous under positive integer scaling*: multiplying all
durations by k multiplies both chain lengths and buffers by k, which is one
of the required properties covered by tests.

Buffers are placed on the timeline but do not shift scheduled tasks:

* project buffer: [makespan, makespan + PB], after the last critical task;
* feeding buffer: ends exactly at the scheduled start of the critical-chain
  task the feeding chain enters, i.e. [start_CC - FB, start_CC]. If FB is
  larger than the free gap in front of that start (the feeding chain has
  less slack than the buffer would consume) the buffer is flagged
  ``overflow=true`` and is drawn extending past the entry point. The plan is
  not automatically replanned; this is surfaced for the planner.
"""
from __future__ import annotations

from .models import BufferInfo, ProjectInput, ScheduledTask

BUFFER_FACTOR = 0.5


def chain_safety(project: ProjectInput, chain: list[str]) -> float:
    total = 0.0
    for name in chain:
        t = project.task(name)
        assert t is not None
        total += t.conservative - t.aggressive
    return total


def buffer_size(project: ProjectInput, chain: list[str]) -> float:
    return BUFFER_FACTOR * chain_safety(project, chain)


def build_buffers(
    project: ProjectInput,
    tasks: list[ScheduledTask],
    makespan: float,
    feeding: list[tuple[str, str, list[str]]],
) -> list[BufferInfo]:
    buffers: list[BufferInfo] = []
    by_name = {t.name: t for t in tasks}
    for feeding_from, feeding_into, chain in feeding:
        size = buffer_size(project, chain)
        entry = by_name[feeding_into].start
        start = entry - size
        overflow = False
        # Free slack for the feeding buffer is the gap between the feeding
        # chain's last finish and the CC task's start. If the computed buffer
        # is larger than that gap its left edge overlaps the chain's own work.
        chain_finish = max(by_name[n].finish for n in chain)
        if size > 1e-9 and start < chain_finish - 1e-9:
            overflow = True
        buffers.append(
            BufferInfo(
                kind="feeding",
                start=start,
                finish=entry,
                size=size,
                feeding_from=feeding_from,
                feeding_into=feeding_into,
                overflow=overflow,
            )
        )
    # project buffer
    cc_names = [t.name for t in tasks if t.is_critical]
    pb = buffer_size(project, cc_names)
    buffers.append(
        BufferInfo(
            kind="project",
            start=makespan,
            finish=makespan + pb,
            size=pb,
        )
    )
    return buffers
