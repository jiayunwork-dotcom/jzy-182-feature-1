"""Resource-constrained scheduling (serial schedule generation scheme).

Rule (documented in README):

1. Tasks are picked from the *ready set* (all predecessors already scheduled).
   Priority is the longest aggressive-duration tail through the dependency
   graph (``tail`` = own aggressive duration + longest successor tail), ties
   broken lexicographically by task name. This is a deterministic LPT-style
   rule: long/downstream-critical work gets first claim on resources.
2. A task is placed at the earliest workday at which all predecessors are
   finished **and** every required resource has enough free capacity over its
   whole execution window. Capacity is checked per unit-length bucket, so
   partial availability windows are respected.
3. If capacity is insufficient, the task is pushed to the finish time of the
   earliest-finishing blocking task, and the search repeats. Tasks that
   perform the final blocking release become *resource predecessors* — tight
   extra edges used by :mod:`chain` to identify the critical chain.

The scan uses integer/fraction workday offsets from project start; no calendar
is involved at planning time.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import ProjectInput, ScheduledTask

_EPS = 1e-9


@dataclass
class _Segment:
    start: float
    finish: float
    task: str
    qty: float


def compute_tails(project: ProjectInput) -> dict[str, float]:
    """Aggressive duration of the longest path starting at each task."""
    tails: dict[str, float] = {}
    # tasks are acyclic here; process in reverse input order is not enough in
    # general, so do memoized DFS with deterministic iteration.
    def tail(name: str) -> float:
        if name in tails:
            return tails[name]
        t = project.task(name)
        assert t is not None
        best = 0.0
        # successor view
        succ = successors(project).get(name, [])
        best = max((tail(s) for s in succ), default=0.0)
        tails[name] = t.aggressive + best
        return tails[name]

    for t in project.tasks:
        tail(t.name)
    return tails


def successors(project: ProjectInput) -> dict[str, list[str]]:
    succ: dict[str, list[str]] = {t.name: [] for t in project.tasks}
    for t in project.tasks:
        for p in t.predecessors:
            succ.setdefault(p, []).append(t.name)
    for v in succ.values():
        v.sort()
    return succ


def critical_path(project: ProjectInput) -> tuple[float, list[str]]:
    """Precedence-only longest path (aggressive durations).

    Returns (length, task names). Lexicographically smallest name sequence on
    ties.
    """
    order = _topo(project)
    finish_at: dict[str, float] = {}
    best_path: dict[str, tuple[str, ...]] = {}
    for name in order:
        t = project.task(name)
        assert t is not None
        best_len = -1.0
        best_tuple: tuple[str, ...] | None = None
        best_count = -1
        for p in t.predecessors:
            cand = best_path[p]
            better = (
                finish_at[p] > best_len + _EPS
                or (
                    abs(finish_at[p] - best_len) <= _EPS
                    and best_tuple is not None
                    and (
                        len(cand) > best_count
                        or (len(cand) == best_count and cand < best_tuple)
                    )
                )
            )
            if better:
                best_len = finish_at[p]
                best_tuple = cand
                best_count = len(cand)
        finish_at[name] = (best_len if best_len > -0.5 else 0.0) + t.aggressive
        best_path[name] = (best_tuple or ()) + (name,)
    max_finish = max(finish_at.values()) if finish_at else 0.0
    ends = [n for n in finish_at if abs(finish_at[n] - max_finish) <= _EPS]
    end = min(ends, key=lambda n: best_path[n])
    return max_finish, list(best_path[end])


def _topo(project: ProjectInput) -> list[str]:
    """Kahn order, ties by input order (validated to be acyclic)."""
    indeg = {t.name: 0 for t in project.tasks}
    succ = successors(project)
    for t in project.tasks:
        for p in t.predecessors:
            indeg[t.name] += 1
    input_rank = {t.name: i for i, t in enumerate(project.tasks)}
    ready = sorted((n for n, d in indeg.items() if d == 0), key=lambda n: input_rank[n])
    order: list[str] = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for s in succ[n]:
            indeg[s] -= 1
            if indeg[s] == 0:
                ready.append(s)
                ready.sort(key=lambda x: input_rank[x])
    return order


def schedule(project: ProjectInput) -> list[ScheduledTask]:
    """Run the serial SGS; return tasks with start/finish in workday offsets."""
    tails = compute_tails(project)
    succ = successors(project)
    finish_at: dict[str, float] = {}
    placed: dict[str, ScheduledTask] = {}
    profiles: dict[str, list[_Segment]] = {r.name: [] for r in project.resources}
    capacity = {r.name: r.capacity for r in project.resources}
    unscheduled = {t.name for t in project.tasks}
    indeg = {t.name: len(t.predecessors) for t in project.tasks}

    while unscheduled:
        ready = [n for n in unscheduled if indeg[n] == 0]
        # priority: longest tail, then name
        name = min(ready, key=lambda n: (-tails[n], n))
        task = project.task(name)
        assert task is not None

        logic_start = max((finish_at[p] for p in task.predecessors), default=0.0)
        tight_logic = [
            p for p in task.predecessors if abs(finish_at[p] - logic_start) <= _EPS
        ]

        start, resource_preds = _earliest_feasible(
            name, task.aggressive, task.resources, logic_start, profiles, capacity
        )

        # All predecessors finishing exactly at the start are tight, including
        # zero-duration tasks (which occupy no time at all).
        finish = start + task.aggressive
        for rname, qty in task.resources.items():
            if qty > 0 and finish > start + _EPS:
                profiles[rname].append(_Segment(start, finish, name, qty))

        sched = ScheduledTask(
            name=name,
            start=start,
            finish=finish,
            aggressive=task.aggressive,
            conservative=task.conservative,
            is_critical=False,
            resources=dict(task.resources),
            predecessors=task.predecessors,
            resource_predecessors=sorted(
                r for r in resource_preds if r not in tight_logic
            ),
        )
        placed[name] = sched
        finish_at[name] = finish
        unscheduled.remove(name)
        for s in succ[name]:
            indeg[s] -= 1

    return [placed[t.name] for t in project.tasks]


def _earliest_feasible(
    name: str,
    duration: float,
    needs: dict[str, float],
    logic_start: float,
    profiles: dict[str, list[_Segment]],
    capacity: dict[str, float],
) -> tuple[float, list[str]]:
    """Find the earliest feasible start; return (start, final blocker names)."""
    if duration <= _EPS:
        return logic_start, []

    start = logic_start
    end = start + duration
    last_blockers: list[str] = []
    while True:
        hit = _first_infeasible(start, end, needs, profiles, capacity)
        if hit is None:
            return start, last_blockers
        point, blockers = hit
        if not blockers:  # pragma: no cover - defensive, cannot happen
            raise RuntimeError(f"resource infeasibility without blocker for task {name}")
        # Push to the earliest finish among the blocking tasks; the tasks
        # ending exactly at that moment release the bottleneck.
        finish_times = {b: _finish_of(b, profiles) for b in blockers}
        target = min(finish_times.values())
        last_blockers = sorted(b for b, f in finish_times.items() if abs(f - target) <= _EPS)
        start = target
        end = start + duration


def _finish_of(task: str, profiles: dict[str, list[_Segment]]) -> float:
    for segs in profiles.values():
        for s in segs:
            if s.task == task:
                return s.finish
    raise KeyError(task)  # pragma: no cover


def _first_infeasible(
    start: float,
    end: float,
    needs: dict[str, float],
    profiles: dict[str, list[_Segment]],
    capacity: dict[str, float],
) -> tuple[float, list[str]] | None:
    """Return (point, blocking task names) at the first overloaded bucket."""
    points = {start}
    for rname in needs:
        for seg in profiles.get(rname, ()):
            if seg.start > start + _EPS and seg.start < end - _EPS:
                points.add(seg.start)
            # a segment starting before/at `start` is captured by the check at
            # `start` itself
    for point in sorted(points):
        blockers: list[str] = []
        overloaded = False
        for rname, need in needs.items():
            if need <= 0:
                continue
            running = [
                seg
                for seg in profiles.get(rname, ())
                if seg.start <= point + _EPS and seg.finish > point + _EPS
            ]
            used = sum(seg.qty for seg in running)
            if used + need > capacity[rname] + _EPS:
                overloaded = True
                blockers.extend(seg.task for seg in running)
        if overloaded:
            return point, sorted(set(blockers))
    return None
