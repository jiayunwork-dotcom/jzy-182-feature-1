"""Critical chain and feeding chain identification.

After the scheduler placed every task, the critical chain is the longest path
in a graph made of two kinds of *tight* edges:

* precedence edges ending exactly at the task's scheduled start (predecessors
  with free slack do not constrain it);
* "resource predecessor" edges emitted by the scheduler: the tasks that
  actually performed the last release of a contended resource.

Every edge on that augmented graph is tight, so its longest path always has
length equal to the project makespan and is a true critical chain. When
several chains share the maximum length, the lexicographically smallest
sequence of task names is chosen — a total order on distinct chains, so the
same input always yields the same chain.

Feeding chains are found on the full *precedence* network (not only tight
edges): every arc from a non-CC task into a CC task is a merge point, and the
feeding chain is the longest path of non-CC precedence tasks ending there.
Chains with free slack are normal (the feeding buffer lives in the slack);
tight merges are reported later as buffer overflow.
"""
from __future__ import annotations

from .models import ProjectInput, ScheduledTask
from .scheduler import _EPS


def build_tight_edges(
    project: ProjectInput, tasks: list[ScheduledTask]
) -> dict[str, list[str]]:
    """Return tight predecessors (logic finishing exactly at start + resource).

    Zero-duration tasks occupy no time, so tightness propagates through them:
    a predecessor whose finish reaches a task's start via a chain of
    zero-duration tasks is still tight.
    """
    by_name = {t.name: t for t in tasks}

    def tight_logic_preds(name: str, acc: set[str], visiting_zero: bool) -> None:
        t = by_name[name]
        for p in t.predecessors:
            pt = by_name[p]
            if abs(pt.finish - t.start) <= _EPS:
                if pt.aggressive <= _EPS:
                    if p not in acc:
                        acc.add(p)
                        tight_logic_preds(p, acc, True)
                else:
                    acc.add(p)

    edges: dict[str, list[str]] = {}
    for t in tasks:
        preds: set[str] = set()
        tight_logic_preds(t.name, preds, False)
        for p in t.resource_predecessors:
            preds.add(p)
        edges[t.name] = sorted(preds)
    return edges


def critical_chain(
    project: ProjectInput, tasks: list[ScheduledTask], makespan: float
) -> tuple[list[str], dict[str, list[str]]]:
    """Longest tight path; tie-break by lexicographically smallest name tuple."""
    edges = build_tight_edges(project, tasks)
    by_name = {t.name: t for t in tasks}
    length: dict[str, float] = {}
    path: dict[str, tuple[str, ...]] = {}

    def visit(n: str) -> float:
        if n in length:
            return length[n]
        best_len = 0.0
        best_path: tuple[str, ...] = ()
        best_count = -1
        for p in edges[n]:
            pl = visit(p)
            # longer; on equal length prefer more tasks (keeps zero-duration
            # chain members), then lexicographically smallest name sequence
            cand_count = len(path[p])
            better = (
                pl > best_len + _EPS
                or (abs(pl - best_len) <= _EPS and (
                    cand_count > best_count
                    or (cand_count == best_count and path[p] < best_path)
                ))
            )
            if better:
                best_len = pl
                best_path = path[p]
                best_count = cand_count
        length[n] = best_len + by_name[n].aggressive
        path[n] = best_path + (n,)
        return length[n]

    for t in tasks:
        visit(t.name)

    ends = [t.name for t in tasks if abs(by_name[t.name].finish - makespan) <= _EPS]
    end = min(ends, key=lambda n: (-length[n], path[n]))
    return list(path[end]), edges


def feeding_chains(
    project: ProjectInput,
    tasks: list[ScheduledTask],
    cc_set: set[str],
) -> list[tuple[str, str, list[str]]]:
    """Return (feeding_from, feeding_into, chain) for each merge arc.

    A merge arc is any precedence arc non-CC task -> CC task. The chain is the
    longest path of non-CC tasks ending at the arc tail; ties are resolved by
    the lexicographically smallest name tuple.
    """
    by_name = {t.name: t for t in tasks}
    length: dict[str, float] = {}
    path: dict[str, tuple[str, ...]] = {}

    def visit(n: str) -> float:
        if n in length:
            return length[n]
        best_len = 0.0
        best_path: tuple[str, ...] = ()
        t = project.task(n)
        assert t is not None
        for p in t.predecessors:
            if p in cc_set:
                continue  # walk stops at the critical chain boundary
            pl = visit(p)
            if pl > best_len + _EPS or (abs(pl - best_len) <= _EPS and path[p] < best_path):
                best_len = pl
                best_path = path[p]
        length[n] = best_len + by_name[n].aggressive
        path[n] = best_path + (n,)
        return length[n]

    result: list[tuple[str, str, list[str]]] = []
    seen: set[tuple[str, str]] = set()
    for name in cc_set:
        t = project.task(name)
        assert t is not None
        for p in t.predecessors:
            if p in cc_set or (p, name) in seen:
                continue
            seen.add((p, name))
            visit(p)
            result.append((p, name, list(path[p])))
    result.sort(key=lambda x: (x[1], x[0]))
    return result
