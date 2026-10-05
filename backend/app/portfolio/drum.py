"""Drum selection.

The drum is the single shared resource whose beat staggers the projects. CCPM
multi-project scheduling uses one system-wide constraint resource this way:
projects are offset so their claims on that resource never overlap beyond its
capacity; every other shared resource is only watched, not scheduled against
(see :mod:`stagger`).

Two ways to fix the drum:

* ``manual`` — the portfolio admin names a pool resource when creating the
  portfolio. It never changes behind the admin's back.
* ``auto`` (default) — the backend measures the *load* of every pool resource
  from the joined members' plans and picks the tightest one.

Load measure
------------

For a pool resource *r* with capacity ``C_r`` and member tasks shifted by their
offsets:

    load(r) = sum over tasks t using r of  qty(t,r) * aggressive_duration(t)
              / C_r

i.e. total *capacity-normalised work content* in workdays. A resource that
many tasks want for long stretches and that has little capacity scores high.
The values are computed from each project's own (unchanged) plan, so they do
not depend on offsets.

Tie-break
---------

Equal loads are broken by the lexicographically smallest pool resource name —
a total order, so the same members always resolve to the same drum.

Freeze rule
-----------

An auto drum is resolved when the first member that actually borrows a pool
resource joins, and is then frozen. Freezing matters for determinism: a later
member must not silently re-stagger every earlier project. The price is noted
in the delivery docs: the drum is the bottleneck of the members present at
portfolio start, not necessarily of the portfolio a year later; the admin can
create a new portfolio (or use a manual drum) when the bottleneck moves.
"""
from __future__ import annotations

from typing import Any, Optional

from .models import PoolResource

_EPS = 1e-9


def resource_load(
    plan: dict[str, Any],
    shared_resources: dict[str, str],
    pool_resources: list[PoolResource],
) -> dict[str, float]:
    """Capacity-normalised load each pool resource gets from one member plan.

    ``shared_resources`` maps the project's own resource name to the pool
    resource name. Resources the project keeps private contribute nothing.
    """
    capacity = {r.name: r.capacity for r in pool_resources}
    load = {r.name: 0.0 for r in pool_resources}
    for t in plan.get("tasks", []):
        duration = float(t.get("aggressive", 0.0))
        for proj_name, qty_raw in (t.get("resources") or {}).items():
            pool_name = shared_resources.get(proj_name)
            if pool_name is None or pool_name not in load:
                continue
            qty = float(qty_raw)
            cap = capacity[pool_name]
            load[pool_name] += qty * duration / cap if cap > _EPS else float("inf")
    return load


def choose_drum(loads: dict[str, float]) -> Optional[str]:
    """Highest load; ties broken by smallest resource name. None if all zero."""
    candidates = [(name, value) for name, value in loads.items() if value > _EPS]
    if not candidates:
        return None
    candidates.sort(key=lambda nv: (-nv[1], nv[0]))
    return candidates[0][0]


def merge_loads(total: dict[str, float], extra: dict[str, float]) -> None:
    for name, value in extra.items():
        total[name] = total.get(name, 0.0) + value
