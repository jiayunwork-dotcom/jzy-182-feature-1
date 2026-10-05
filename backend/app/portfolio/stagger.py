"""Drum staggering math (pure functions, no I/O).

When a project joins a portfolio its own plan is frozen (critical chain,
length and buffers unchanged) and shifted as one rigid block by an *offset* on
the portfolio workday axis. This module answers four questions:

1. which resource intervals does a member place on each pool resource
   (:func:`member_segments`);
2. the smallest non-negative offset at which the newcomer's drum usage never
   exceeds drum capacity together with every already-placed member
   (:func:`earliest_feasible_offset`) — new projects are packed at the earliest
   feasible slot, so no capacity gap is intentionally left between projects on
   the drum ("项目之间在鼓上不留容量缓冲");
3. where non-drum pool resources are nevertheless overloaded after the join —
   reported as hints, never used to push projects (:func:`overload_windows`);
4. the drum load step curve drawn under the portfolio timeline
   (:func:`load_profile`).

Intervals are half-open ``[start, finish)`` exactly like the single-project
scheduler, so two segments touching at one instant do not conflict.
"""
from __future__ import annotations

from typing import Any, Optional

from .models import Segment

_EPS = 1e-9


def member_segments(
    project_id: str,
    plan: dict[str, Any],
    shared_resources: dict[str, str],
    offset: float,
) -> list[Segment]:
    """Translate a member plan's resource usage onto the portfolio axis.

    Only resources declared as shared produce segments; private project
    resources are invisible to the pool.
    """
    segments: list[Segment] = []
    for t in plan.get("tasks", []):
        start = float(t.get("start", 0.0)) + offset
        finish = float(t.get("finish", 0.0)) + offset
        if finish <= start + _EPS:
            continue  # zero-duration tasks occupy no resource
        for proj_name, qty_raw in (t.get("resources") or {}).items():
            pool_name = shared_resources.get(proj_name)
            if pool_name is None:
                continue
            qty = float(qty_raw)
            if qty > 0:
                segments.append(
                    Segment(project_id, t["name"], pool_name, start, finish, qty)
                )
    return segments


def segments_for(segments: list[Segment], resource: str) -> list[Segment]:
    return [s for s in segments if s.resource == resource]


def _sweep_load(points: list[float], segments: list[Segment]) -> list[tuple[float, float, float]]:
    """Return ``[(piece_start, piece_finish, total_load), ...]`` pieces."""
    pieces: list[tuple[float, float, float]] = []
    for a, b in zip(points, points[1:]):
        if b <= a + _EPS:
            continue
        mid = (a + b) / 2.0
        load = sum(s.qty for s in segments if s.start <= mid + _EPS and s.finish > mid + _EPS)
        pieces.append((a, b, load))
    return pieces


def _internally_feasible(new: list[Segment], capacity: float) -> bool:
    """Whether the newcomer alone fits the drum at any rigid shift."""
    if not new:
        return True
    points = sorted({p for s in new for p in (s.start, s.finish)})
    return all(load <= capacity + _EPS for _, _, load in _sweep_load(points, new))


def earliest_feasible_offset(
    drum: str,
    capacity: float,
    existing: list[Segment],
    new_unshifted: list[Segment],
) -> Optional[float]:
    """Earliest offset at which the newcomer respects drum capacity.

    Returns ``None`` when the newcomer's own tasks together demand more drum
    capacity than exists — no shift can fix that, so the join must be rejected.

    Only finitely many offsets can be optimal: 0, or a point where a newcomer
    segment edge meets an existing segment edge. They are tried in ascending
    order, so the returned offset is the global earliest — the newcomer slides
    into the first gap big enough, never leaving deliberate drum idle time.
    """
    new_drum = [s for s in new_unshifted if s.resource == drum]
    old_drum = [s for s in existing if s.resource == drum]
    if not new_drum:
        return 0.0
    # Edges relative to the newcomer's own start; the block is shifted by the
    # offset we are solving for.
    new_edges: list[tuple[float, float]] = [(s.start, s.finish) for s in new_drum]
    new_norm = [
        Segment(s.project_id, s.task, s.resource, a, b, s.qty)
        for s, (a, b) in zip(new_drum, new_edges)
    ]
    if not _internally_feasible(new_norm, capacity):
        return None

    # Only alignments of edges can be the earliest feasible offset: a newcomer
    # start edge meeting an existing finish edge, or a newcomer finish edge
    # meeting an existing start edge (plus 0).
    candidates = {0.0}
    for a, b in new_edges:
        for s in old_drum:
            candidates.add(max(0.0, s.finish - a))
            candidates.add(max(0.0, s.start - b))

    for offset in sorted(candidates):
        shifted = [
            Segment(s.project_id, s.task, s.resource, s.start + offset, s.finish + offset, s.qty)
            for s in new_norm
        ]
        points = sorted(
            {p for seg in old_drum + shifted for p in (seg.start, seg.finish)}
        )
        pieces = _sweep_load(points, old_drum + shifted)
        if all(load <= capacity + _EPS for _, _, load in pieces):
            return round_num(offset)
    return None  # pragma: no cover - candidate set always contains a feasible point


def overload_windows(
    resource: str, capacity: float, segments: list[Segment]
) -> list[dict[str, float]]:
    """Maximal intervals on which ``resource`` is claimed beyond capacity."""
    segs = segments_for(segments, resource)
    if not segs:
        return []
    points = sorted({p for s in segs for p in (s.start, s.finish)})
    windows: list[dict[str, float]] = []
    cur: Optional[list[float]] = None
    peak = 0.0
    for a, b, load in _sweep_load(points, segs):
        if load > capacity + _EPS:
            if cur is None:
                cur = [a, b]
                peak = load
            else:
                cur[1] = b
                peak = max(peak, load)
        elif cur is not None:
            windows.append(
                {"resource": resource, "start": round_num(cur[0]), "finish": round_num(cur[1]),
                 "load": round_num(peak), "capacity": capacity}
            )
            cur = None
    if cur is not None:
        windows.append(
            {"resource": resource, "start": round_num(cur[0]), "finish": round_num(cur[1]),
             "load": round_num(peak), "capacity": capacity}
        )
    return windows


def load_profile(
    resource: str, capacity: float, segments: list[Segment], horizon: float
) -> dict[str, Any]:
    """Step profile ``[0, horizon)`` of one pool resource for the chart."""
    segs = segments_for(segments, resource)
    points = {0.0, max(0.0, horizon)}
    for s in segs:
        points.add(min(max(s.start, 0.0), horizon))
        points.add(min(max(s.finish, 0.0), horizon))
    ordered = sorted(points)
    pieces = [
        {"start": round_num(a), "finish": round_num(b), "load": round_num(load)}
        for a, b, load in _sweep_load(ordered, segs)
    ]
    peak = max((p["load"] for p in pieces), default=0.0)
    return {
        "resource": resource,
        "capacity": capacity,
        "pieces": pieces,
        "peak": peak,
        "overloaded": peak > capacity + _EPS,
    }


def round_num(x: float) -> float:
    r = round(x, 9)
    return int(r) if r == int(r) else r
