"""Portfolio payload and join validation.

Same contract as :mod:`validation`: every problem is reported with a precise
field path (``pool[1].capacity``, ``tasks[0].resources.工程师`` ...) and all
errors are reported at once.
"""
from __future__ import annotations

from typing import Any, Optional

from .models import ProjectInput
from .portfolio import pool_resources
from .validation import ValidationError, _coerce_number

_EPS = 1e-9


def _pool_errors(raw_pool: Any) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Validate a pool payload without raising; return (pool, errors)."""
    if not isinstance(raw_pool, list):
        return [], [{"field": "pool", "message": "must be a list"}]
    errors: list[dict[str, str]] = []
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, r in enumerate(raw_pool):
        base = f"pool[{i}]"
        if not isinstance(r, dict):
            errors.append({"field": base, "message": "must be an object"})
            continue
        name = r.get("name", "")
        if not isinstance(name, str) or not name.strip():
            errors.append({"field": f"{base}.name", "message": "resource name is required"})
            name = ""
        elif name in seen:
            errors.append(
                {"field": f"{base}.name", "message": f"duplicate resource name {name!r}"}
            )
        seen.add(name)
        cap = _coerce_number(r.get("capacity"), f"{base}.capacity", errors)
        if cap is not None and cap < 0:
            errors.append(
                {"field": f"{base}.capacity", "message": "capacity must not be negative"}
            )
        pool.append(
            {
                "name": name.strip() if isinstance(name, str) else "",
                "capacity": cap if cap is not None else 0.0,
            }
        )
    return pool, errors


def parse_pool(raw_pool: Any) -> list[dict[str, Any]]:
    """Validate a pool payload; return normalized ``[{name, capacity}]``."""
    pool, errors = _pool_errors(raw_pool)
    if errors:
        raise ValidationError(errors)
    return pool


def _parse_drum(raw: Any, field: str, errors: list[dict[str, str]]) -> Optional[str]:
    """Drum pin: absent/None means auto; a string pins a pool resource."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        errors.append({"field": field, "message": "must be a pool resource name or null"})
        return None
    return raw.strip()


def parse_create_payload(raw: Any) -> dict[str, Any]:
    """Validate a portfolio creation payload."""
    errors: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        raise ValidationError([{"field": "$", "message": "request body must be an object"}])
    name = raw.get("name", "")
    if not isinstance(name, str) or not name.strip():
        errors.append({"field": "name", "message": "portfolio name is required"})
    pool, pool_errors = _pool_errors(raw.get("pool", []))
    errors.extend(pool_errors)
    drum = _parse_drum(raw.get("drum"), "drum", errors)
    if drum is not None and drum not in {p["name"] for p in pool}:
        errors.append(
            {"field": "drum", "message": f"drum resource {drum!r} is not in the pool"}
        )
    if errors:
        raise ValidationError(errors)
    return {"name": name.strip(), "pool": pool, "drum": drum}


def parse_update_payload(raw: Any) -> dict[str, Any]:
    """Validate a portfolio update payload; return only the provided keys."""
    errors: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        raise ValidationError([{"field": "$", "message": "request body must be an object"}])
    changes: dict[str, Any] = {}
    if "name" in raw:
        name = raw["name"]
        if not isinstance(name, str) or not name.strip():
            errors.append({"field": "name", "message": "portfolio name is required"})
        else:
            changes["name"] = name.strip()
    if "pool" in raw:
        pool, pool_errors = _pool_errors(raw["pool"])
        errors.extend(pool_errors)
        changes["pool"] = pool
    if "drum" in raw:
        changes["drum"] = _parse_drum(raw["drum"], "drum", errors)
    if errors:
        raise ValidationError(errors)
    return changes


def update_errors(
    current: dict[str, Any],
    changes: dict[str, Any],
    member_projects: list[ProjectInput],
) -> list[dict[str, str]]:
    """Cross-check an update against the current portfolio and its members.

    A pool resource cannot be removed while it is the effective drum with
    placed projects, or while member projects draw it from the pool. The
    pinned drum must name a pool resource. Capacity edits are allowed even
    below current usage — the view then reports the overload as a conflict.
    """
    errors: list[dict[str, str]] = []
    pool = changes.get("pool", current["pool"])
    pool_names = {p["name"] for p in pool}

    drum = changes.get("drum", current["drum_resource"])
    if drum is not None and drum not in pool_names:
        errors.append(
            {"field": "drum", "message": f"drum resource {drum!r} is not in the pool"}
        )

    frozen = current["auto_drum"]
    if frozen is not None and frozen not in pool_names:
        errors.append(
            {
                "field": "pool",
                "message": (
                    f"resource {frozen!r} is the current drum with placed projects "
                    "and cannot be removed"
                ),
            }
        )

    old_pool = {p["name"]: p["capacity"] for p in current["pool"]}
    used: set[str] = set()
    for project in member_projects:
        used |= set(pool_resources(project, old_pool))
    for name in sorted(used):
        if name not in pool_names:
            errors.append(
                {
                    "field": "pool",
                    "message": (
                        f"resource {name!r} is drawn from the pool by member "
                        "projects and cannot be removed"
                    ),
                }
            )
    return errors


def join_errors(project: ProjectInput, pool: dict[str, float]) -> list[dict[str, str]]:
    """Validate a project against the pool it is joining; all errors at once.

    * a resource declared ``shared`` whose name is not in the pool
    * a pool-sourced resource whose declared capacity exceeds the pool's
      (the standalone plan could then pack more parallel load than the pool
      can ever hold, so no offset could satisfy the drum invariant)
    * a single task demanding more of a pool resource than the pool holds
    """
    errors: list[dict[str, str]] = []
    for i, r in enumerate(project.resources):
        if r.shared and r.name not in pool:
            errors.append(
                {
                    "field": f"resources[{i}].name",
                    "message": (
                        f"resource {r.name!r} is declared as pool-shared but the "
                        "pool has no resource with that name"
                    ),
                }
            )
    for i, r in enumerate(project.resources):
        if not (r.shared or r.name in pool) or r.name not in pool:
            continue
        if r.capacity > pool[r.name] + _EPS:
            errors.append(
                {
                    "field": f"resources[{i}].capacity",
                    "message": (
                        f"declared capacity {r.capacity} of shared resource "
                        f"{r.name!r} exceeds the pool capacity {pool[r.name]}"
                    ),
                }
            )
    for j, t in enumerate(project.tasks):
        for rname, qty in t.resources.items():
            if rname in pool and qty > pool[rname] + _EPS:
                errors.append(
                    {
                        "field": f"tasks[{j}].resources.{rname}",
                        "message": (
                            f"task requires {qty} of shared resource {rname!r} "
                            f"but the pool capacity is {pool[rname]}"
                        ),
                    }
                )
    return errors
