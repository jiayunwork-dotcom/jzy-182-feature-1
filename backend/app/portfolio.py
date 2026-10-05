"""Multi-project portfolios: shared resource pools, the drum, and staggering.

A portfolio owns a **pool** of shared resources (name -> capacity). A project
resource is *pool-sourced* when it is explicitly declared ``shared`` at entry
time, or when its name matches a pool resource — the latter is how projects
created before the portfolio existed join: inside a portfolio, a resource
name refers to the one shared pool resource, not a private copy.

**The drum.** Exactly one pool resource paces the portfolio. The admin may
pin it (``drum_resource``); otherwise it is auto-identified by **load
ratio**::

    load(r) = sum(qty * aggressive over all member tasks drawing r) / capacity(r)

i.e. the time resource ``r`` would need to serve all demanded work on its
own — the classic bottleneck measure. Ties go to the lexicographically
smallest resource name (total order, deterministic). The auto-identified
drum is **frozen at the first join that produces positive load** and stored
on the portfolio: staggering every later project against one stable drum is
what keeps the invariant "drum occupancy never exceeds pool capacity" true
for all time. Re-identifying the drum mid-stream would invalidate offsets
computed against the previous drum. (Trade-off, documented in the README:
the auto drum reflects the portfolio as of its first loading join; the admin
can re-pin explicitly, which only affects future joins — existing offsets
are never recomputed.)

**Staggering.** A project keeps its own critical chain, chain length and
buffers; it only receives a start ``offset`` on the portfolio timeline. The
offset is the earliest one such that the merged drum profile (already placed
projects fixed) never exceeds the pool capacity. The push rule mirrors the
single-project scheduler: at the earliest overloaded point, push so that the
candidate segments covering the point start at the earliest finish of the
blocking existing segments. Offsets strictly increase through a finite
candidate set, so the computation terminates and is deterministic. No
capacity buffer is left between projects on the drum: a new project's drum
usage may start exactly when the previous usage releases capacity. When the
pool capacity is larger than one project's demand, projects may overlap on
the drum — "placed after already-placed projects" means existing placements
are never moved to make room, not that the drum is artificially serialized.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from .models import ProjectInput

_EPS = 1e-9


class PortfolioConflictError(Exception):
    """The drum capacity invariant cannot be satisfied because the already
    placed projects exceed the pool capacity on their own (only reachable
    after the pool capacity was shrunk below placed usage)."""


# --------------------------------------------------------------------------- #
# Pool-facing view of a project
# --------------------------------------------------------------------------- #
def pool_resources(project: ProjectInput, pool: dict[str, float]) -> dict[str, float]:
    """Resources of ``project`` that draw from the pool: name -> declared cap."""
    return {
        r.name: r.capacity
        for r in project.resources
        if r.shared or r.name in pool
    }


# --------------------------------------------------------------------------- #
# Drum identification
# --------------------------------------------------------------------------- #
def drum_loads(
    projects: Iterable[ProjectInput], pool: dict[str, float]
) -> dict[str, float]:
    """Load ratio per pool resource: demanded work / capacity."""
    demand = {name: 0.0 for name in pool}
    for project in projects:
        sourced = pool_resources(project, pool)
        for task in project.tasks:
            for rname, qty in task.resources.items():
                if rname in sourced and qty > 0:
                    demand[rname] += qty * task.aggressive
    return {
        name: (demand[name] / cap if cap > _EPS else 0.0)
        for name, cap in pool.items()
    }


def identify_drum(
    projects: Iterable[ProjectInput], pool: dict[str, float]
) -> Optional[str]:
    """Highest load ratio wins; ties go to the smallest resource name.

    Returns ``None`` when no pool resource carries any load yet (the drum is
    then resolved at a later join).
    """
    loads = drum_loads(projects, pool)
    positive = {n: load for n, load in loads.items() if load > _EPS}
    if not positive:
        return None
    best = max(positive.values())
    return min(n for n, load in positive.items() if abs(load - best) <= _EPS)


# --------------------------------------------------------------------------- #
# Staggering
# --------------------------------------------------------------------------- #
def drum_segments(
    plan_tasks: list[dict[str, Any]], offset: float, resource: str
) -> list[tuple[float, float, float]]:
    """(start, finish, qty) usage of ``resource`` by a placed plan."""
    return [
        (t["start"] + offset, t["finish"] + offset, t["resources"][resource])
        for t in plan_tasks
        if t["resources"].get(resource, 0) > 0
        and t["finish"] - t["start"] > _EPS
    ]


def compute_offset(
    capacity: float,
    existing: list[tuple[float, float, float]],
    candidate: list[tuple[float, float, float]],
) -> float:
    """Earliest offset >= 0 such that the merged drum profile fits ``capacity``.

    ``existing`` segments are in portfolio time (fixed); ``candidate``
    segments are relative to the new project's own start. The result is
    deterministic: the scan always resolves the earliest overloaded point by
    pushing to the earliest release of the blocking existing segments.
    """
    offset = 0.0
    while True:
        point = _first_overload(capacity, existing, candidate, offset)
        if point is None:
            return offset
        blockers = [s for s in existing if s[0] <= point + _EPS and s[1] > point + _EPS]
        if not blockers:
            raise PortfolioConflictError(
                "existing placements already exceed the drum capacity"
            )
        release = min(s[1] for s in blockers)
        shifted = [
            s
            for s in candidate
            if s[0] + offset <= point + _EPS and s[1] + offset > point + _EPS
        ]
        used = sum(s[2] for s in blockers) + sum(s[2] for s in shifted)
        excess = used - capacity
        # Clear candidate segments from the point, latest-starting first
        # (smallest push), until the remaining load fits. The offset must
        # move past the earliest-starting removed segment.
        shifted.sort(key=lambda s: (-s[0], -s[2]))
        cleared = 0.0
        earliest_removed_start: Optional[float] = None
        for s in shifted:
            cleared += s[2]
            if earliest_removed_start is None or s[0] < earliest_removed_start:
                earliest_removed_start = s[0]
            if cleared >= excess - _EPS:
                break
        if earliest_removed_start is None or cleared < excess - _EPS:
            raise PortfolioConflictError(
                "existing placements already exceed the drum capacity"
            )
        # strictly greater than the current offset: release > point and the
        # removed segment covered the point, so release - start > offset
        offset = release - earliest_removed_start


def _first_overload(
    capacity: float,
    existing: list[tuple[float, float, float]],
    candidate: list[tuple[float, float, float]],
    offset: float,
) -> Optional[float]:
    """Earliest time point where merged usage exceeds capacity (or None).

    Checking segment start points is sufficient: quantities are positive, so
    usage only decreases at finish boundaries.
    """
    points = sorted({s[0] for s in existing} | {s[0] + offset for s in candidate})
    for p in points:
        used = sum(s[2] for s in existing if s[0] <= p + _EPS and s[1] > p + _EPS)
        used += sum(
            s[2]
            for s in candidate
            if s[0] + offset <= p + _EPS and s[1] + offset > p + _EPS
        )
        if used > capacity + _EPS:
            return p
    return None


# --------------------------------------------------------------------------- #
# Profiles and the portfolio view
# --------------------------------------------------------------------------- #
def merge_profile(
    segments: list[tuple[float, float, float]],
) -> list[dict[str, float]]:
    """Sweep (start, finish, qty) segments into constant-usage regions."""
    points = sorted({s[0] for s in segments} | {s[1] for s in segments})
    regions: list[dict[str, float]] = []
    for a, b in zip(points, points[1:]):
        if b - a <= _EPS:
            continue
        used = sum(s[2] for s in segments if s[0] <= a + _EPS and s[1] > a + _EPS)
        if used <= _EPS:
            continue
        if (
            regions
            and abs(regions[-1]["used"] - used) <= _EPS
            and abs(regions[-1]["finish"] - a) <= _EPS
        ):
            regions[-1]["finish"] = b
        else:
            regions.append({"start": a, "finish": b, "used": used})
    return regions


def _r6(x: float) -> float:
    return round(x, 6)


def effective_drum(portfolio: dict[str, Any]) -> Optional[str]:
    """Pinned drum if the admin set one, else the frozen auto-identified one."""
    return portfolio["drum_resource"] or portfolio["auto_drum"]


def build_view(
    portfolio: dict[str, Any],
    members: list[dict[str, Any]],
    projects: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the portfolio timeline returned by the API.

    Member tasks/buffers are shifted by their frozen offsets; the project's
    own plan (critical chain, chain length, buffers) is never modified. The
    drum load curve and per-resource conflict windows are derived from the
    shifted placements.
    """
    pool = {p["name"]: p["capacity"] for p in portfolio["pool"]}
    drum = effective_drum(portfolio)

    member_views = []
    for m in members:
        record = projects[m["project_id"]]
        plan = record["plan"]
        off = m["offset"]
        member_views.append(
            {
                "project_id": m["project_id"],
                "name": record["name"],
                "offset": _r6(off),
                "join_seq": m["join_seq"],
                "makespan": plan["makespan"],
                "finish": _r6(off + plan["makespan"]),
                "critical_chain": plan["critical_chain"],
                "tasks": [
                    {**t, "start": _r6(t["start"] + off), "finish": _r6(t["finish"] + off)}
                    for t in plan["tasks"]
                ],
                "buffers": [
                    {**b, "start": _r6(b["start"] + off), "finish": _r6(b["finish"] + off)}
                    for b in plan["buffers"]
                ],
            }
        )

    profiles: dict[str, list[dict[str, float]]] = {}
    conflicts: list[dict[str, Any]] = []
    for rname, cap in pool.items():
        segments: list[tuple[float, float, float]] = []
        for mv in member_views:
            segments.extend(drum_segments(mv["tasks"], 0.0, rname))
        profile = merge_profile(segments)
        profiles[rname] = [
            {"start": _r6(r["start"]), "finish": _r6(r["finish"]), "used": _r6(r["used"])}
            for r in profile
        ]
        for region in profile:
            if region["used"] > cap + _EPS:
                conflicts.append(
                    {
                        "resource": rname,
                        "start": _r6(region["start"]),
                        "finish": _r6(region["finish"]),
                        "used": _r6(region["used"]),
                        "capacity": cap,
                    }
                )

    return {
        "id": portfolio["id"],
        "name": portfolio["name"],
        "pool": portfolio["pool"],
        "drum": drum,
        "drum_pinned": portfolio["drum_resource"] is not None,
        "members": member_views,
        "makespan": max((mv["finish"] for mv in member_views), default=0.0),
        "drum_profile": profiles.get(drum, []) if drum else [],
        "drum_capacity": pool.get(drum) if drum else None,
        "resource_profiles": profiles,
        "conflicts": conflicts,
    }
