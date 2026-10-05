"""Portfolio payload validation.

Same convention as single-project validation (:mod:`app.validation`): every
problem is returned as ``{"field": ..., "message": ...}`` with a precise path,
so callers can surface all of them at once. Raised :class:`ValidationError`s
are the same type the project API turns into HTTP 422.

Field paths used here:

- portfolio create/update: ``name``, ``resources[i].name``,
  ``resources[i].capacity``, ``drum``;
- join: ``project_id``, ``shared_resources.<project resource name>``, and
  task-level ``tasks[i].resources.<name>`` copied verbatim from the project so
  the message points at the offending requirement;
- pool updates that would break already-joined members point at
  ``resources[i].capacity`` (or ``resources[i].name`` when a still-referenced
  resource is removed).
"""
from __future__ import annotations

from typing import Any

from app.validation import ValidationError, _coerce_number

from .models import AUTO_DRUM, PortfolioInput, PoolResource


# --------------------------------------------------------------------------- #
# Portfolio create / update
# --------------------------------------------------------------------------- #
def parse_portfolio_input(raw: dict[str, Any]) -> PortfolioInput:
    errors: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        raise ValidationError([{"field": "$", "message": "request body must be an object"}])

    name = raw.get("name", "")
    if not isinstance(name, str) or not name.strip():
        errors.append({"field": "name", "message": "portfolio name is required"})
        name = ""

    resources: list[PoolResource] = []
    raw_resources = raw.get("resources", [])
    if not isinstance(raw_resources, list):
        errors.append({"field": "resources", "message": "must be a list"})
        raw_resources = []
    seen: set[str] = set()
    for i, r in enumerate(raw_resources):
        base = f"resources[{i}]"
        if not isinstance(r, dict):
            errors.append({"field": base, "message": "must be an object"})
            continue
        rname = r.get("name", "")
        if not isinstance(rname, str) or not rname.strip():
            errors.append({"field": f"{base}.name", "message": "resource name is required"})
            rname = ""
        elif rname in seen:
            errors.append({"field": f"{base}.name", "message": f"duplicate resource name {rname!r}"})
        seen.add(rname)
        cap = _coerce_number(r.get("capacity"), f"{base}.capacity", errors)
        if cap is not None and cap < 0:
            errors.append({"field": f"{base}.capacity", "message": "capacity must not be negative"})
        resources.append(PoolResource(name=rname, capacity=cap if cap is not None else 0.0))

    drum_raw = raw.get("drum", AUTO_DRUM)
    if drum_raw is None:
        drum = AUTO_DRUM
    elif not isinstance(drum_raw, str):
        errors.append({"field": "drum", "message": "must be a resource name string or null"})
        drum = AUTO_DRUM
    else:
        drum = drum_raw.strip()
        if drum and drum not in seen:
            errors.append(
                {"field": "drum", "message": f"drum resource {drum!r} is not in the pool"}
            )

    if errors:
        raise ValidationError(errors)
    return PortfolioInput(name=name.strip(), resources=resources, drum=drum)


def validate_update_against_members(
    resources: list[PoolResource],
    drum: str,
    member_demands: list[dict[str, float]],
    current_drum: str,
    drum_mode: str,
) -> list[dict[str, str]]:
    """Check a replacement pool against already-joined members.

    ``member_demands`` is one dict per member: pool resource name -> the
    maximum single-task quantity it maps onto that resource. Capacity must
    stay large enough for every member task; otherwise the member could no
    longer live in the pool. Offsets are never recomputed on pool edits.
    """
    errors: list[dict[str, str]] = []
    pool = {r.name: r for i, r in enumerate(resources)}
    for i, r in enumerate(resources):
        for demands in member_demands:
            qty = demands.get(r.name, 0.0)
            if qty > r.capacity + 1e-9:
                errors.append(
                    {
                        "field": f"resources[{i}].capacity",
                        "message": (
                            f"capacity {r.capacity} of resource {r.name!r} is below a joined "
                            f"member task requirement of {qty}"
                        ),
                    }
                )
                break
    if drum_mode == "manual" and drum and drum not in pool:
        errors.append({"field": "drum", "message": f"drum resource {drum!r} is not in the pool"})
    if current_drum and current_drum not in pool:
        # the resolved drum (auto or manual) must not silently disappear
        errors.append(
            {
                "field": "resources.name",
                "message": (
                    f"resource {current_drum!r} is the current drum and cannot be removed; "
                    "repoint the drum first"
                ),
            }
        )
    return errors


# --------------------------------------------------------------------------- #
# Join a project
# --------------------------------------------------------------------------- #
def parse_shared_resources(
    raw: dict[str, Any],
) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
    """Extract the ``shared_resources`` mapping with structural validation."""
    errors: list[dict[str, str]] = []
    value = raw.get("shared_resources", {})
    if value is None:
        value = {}
    if not isinstance(value, dict):
        return None, [{"field": "shared_resources", "message": "must be an object"}]
    mapping: dict[str, str] = {}
    for proj_name, pool_name in value.items():
        if not isinstance(proj_name, str) or not proj_name:
            errors.append({"field": "shared_resources", "message": "keys must be resource name strings"})
            continue
        if not isinstance(pool_name, str) or not pool_name.strip():
            errors.append(
                {"field": f"shared_resources.{proj_name}", "message": "must be a pool resource name string"}
            )
            continue
        mapping[proj_name] = pool_name.strip()
    return mapping, errors


def validate_join(
    mapping: dict[str, str],
    project_input: dict[str, Any],
    pool_resources: list[PoolResource],
) -> list[dict[str, str]]:
    """Semantic checks for joining one parsed project to one pool.

    Every declared project resource must exist on the project; the pool
    resource it points at must exist; no single task may ask for more of a
    pool resource than the pool holds. Two distinct project resources may not
    be folded onto the same pool resource inside one task either — that could
    demand more than the pool capacity the project itself never saw.
    """
    errors: list[dict[str, str]] = []
    project_resource_names = {r.get("name") for r in project_input.get("resources", [])}
    project_tasks = project_input.get("tasks", [])
    pool_names = {r.name for r in pool_resources}
    pool_capacity = {r.name: r.capacity for r in pool_resources}

    for proj_name, pool_name in mapping.items():
        if proj_name not in project_resource_names:
            errors.append(
                {
                    "field": f"shared_resources.{proj_name}",
                    "message": f"project has no resource named {proj_name!r}",
                }
            )
        if pool_name not in pool_names:
            errors.append(
                {
                    "field": f"shared_resources.{proj_name}",
                    "message": f"portfolio pool has no resource named {pool_name!r}",
                }
            )

    # Per-task checks: map each task's project-resource demand through the
    # declaration into pool-resource demand and check it against the pool.
    for i, t in enumerate(project_tasks):
        if not isinstance(t, dict):
            continue
        task_resources = t.get("resources", {})
        if not isinstance(task_resources, dict):
            continue
        # pool resource -> (summed quantity, source project resource names)
        folded: dict[str, float] = {}
        folded_from: dict[str, list[str]] = {}
        for proj_name, qty in task_resources.items():
            if proj_name not in mapping:
                continue  # private project resource: invisible to the portfolio
            pool_name = mapping[proj_name]
            if isinstance(qty, bool) or not isinstance(qty, (int, float)):
                continue
            folded[pool_name] = folded.get(pool_name, 0.0) + float(qty)
            folded_from.setdefault(pool_name, []).append(proj_name)
        for pool_name, total in folded.items():
            if pool_name not in pool_names:
                continue  # bad mapping target already reported above
            source = sorted(folded_from[pool_name])[0]
            if total > pool_capacity[pool_name] + 1e-9:
                errors.append(
                    {
                        "field": f"tasks[{i}].resources.{source}",
                        "message": (
                            f"task requires {total:g} of pool resource {pool_name!r} but pool "
                            f"capacity is {pool_capacity[pool_name]:g}"
                        ),
                    }
                )
    return errors
