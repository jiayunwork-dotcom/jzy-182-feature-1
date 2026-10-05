"""Input validation.

Every problem is reported with a precise field path, e.g.
``tasks[2].predecessors[0]``. Validation is independent of scheduling so
callers can show every error at once.
"""
from __future__ import annotations

from typing import Any

from .models import ProjectInput, Resource, Task


class ValidationError(Exception):
    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("; ".join(e["field"] + ": " + e["message"] for e in errors))


def _coerce_number(value: Any, field: str, errors: list[dict[str, str]]) -> float | None:
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        errors.append({"field": field, "message": "must be a number"})
        return None
    if isinstance(value, (int, float)):
        f = float(value)
    elif isinstance(value, str):
        try:
            f = float(value)
        except ValueError:
            errors.append({"field": field, "message": "must be a number"})
            return None
    else:
        errors.append({"field": field, "message": "must be a number"})
        return None
    if f != f or f in (float("inf"), float("-inf")):
        errors.append({"field": field, "message": "must be a finite number"})
        return None
    return f


def parse_input(raw: dict[str, Any]) -> ProjectInput:
    """Parse a raw JSON payload into :class:`ProjectInput`.

    Raises :class:`ValidationError` with per-field messages. Structural
    problems (missing/wrongly typed fields) are caught here; semantic problems
    (cycles, dangling references, durations, capacities) are caught in
    :func:`validate`.
    """
    errors: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        raise ValidationError([{"field": "$", "message": "request body must be an object"}])

    name = raw.get("name", "")
    if not isinstance(name, str) or not name.strip():
        errors.append({"field": "name", "message": "project name is required"})

    resources: list[Resource] = []
    raw_resources = raw.get("resources", [])
    if not isinstance(raw_resources, list):
        errors.append({"field": "resources", "message": "must be a list"})
        raw_resources = []
    seen_resource_names: set[str] = set()
    for i, r in enumerate(raw_resources):
        base = f"resources[{i}]"
        if not isinstance(r, dict):
            errors.append({"field": base, "message": "must be an object"})
            continue
        rname = r.get("name", "")
        if not isinstance(rname, str) or not rname.strip():
            errors.append({"field": f"{base}.name", "message": "resource name is required"})
            rname = ""
        elif rname in seen_resource_names:
            errors.append({"field": f"{base}.name", "message": f"duplicate resource name {rname!r}"})
        seen_resource_names.add(rname)
        cap = _coerce_number(r.get("capacity"), f"{base}.capacity", errors)
        if cap is not None and cap < 0:
            errors.append({"field": f"{base}.capacity", "message": "capacity must not be negative"})
        shared = r.get("shared", False)
        if not isinstance(shared, bool):
            errors.append({"field": f"{base}.shared", "message": "must be true or false"})
            shared = False
        resources.append(
            Resource(name=rname, capacity=cap if cap is not None else 0.0, shared=shared)
        )

    tasks: list[Task] = []
    raw_tasks = raw.get("tasks", [])
    if not isinstance(raw_tasks, list):
        errors.append({"field": "tasks", "message": "must be a list"})
        raw_tasks = []
    seen_task_names: set[str] = set()
    for i, t in enumerate(raw_tasks):
        base = f"tasks[{i}]"
        if not isinstance(t, dict):
            errors.append({"field": base, "message": "must be an object"})
            continue
        tname = t.get("name", "")
        if not isinstance(tname, str) or not tname.strip():
            errors.append({"field": f"{base}.name", "message": "task name is required"})
            tname = f"__invalid_{i}__"
        elif tname in seen_task_names:
            errors.append({"field": f"{base}.name", "message": f"duplicate task name {tname!r}"})
        seen_task_names.add(tname)

        preds_raw = t.get("predecessors", [])
        if not isinstance(preds_raw, list):
            errors.append({"field": f"{base}.predecessors", "message": "must be a list"})
            preds_raw = []
        preds = tuple(p if isinstance(p, str) else None for p in preds_raw)
        for j, p in enumerate(preds):
            if not isinstance(p, str) or not p:
                errors.append(
                    {"field": f"{base}.predecessors[{j}]", "message": "must be a task name string"}
                )
        preds = tuple(p for p in preds if isinstance(p, str))

        res_raw = t.get("resources", {})
        task_resources: dict[str, float] = {}
        if not isinstance(res_raw, dict):
            errors.append({"field": f"{base}.resources", "message": "must be an object"})
        else:
            for rname, qty in res_raw.items():
                if not isinstance(rname, str):
                    errors.append({"field": f"{base}.resources", "message": "keys must be strings"})
                    continue
                q = _coerce_number(qty, f"{base}.resources.{rname}", errors)
                if q is not None:
                    if q < 0:
                        errors.append(
                            {"field": f"{base}.resources.{rname}", "message": "quantity must not be negative"}
                        )
                    task_resources[rname] = q

        agg = _coerce_number(t.get("aggressive"), f"{base}.aggressive", errors)
        con = _coerce_number(t.get("conservative"), f"{base}.conservative", errors)
        tasks.append(
            Task(
                name=tname,
                predecessors=preds,
                resources=task_resources,
                aggressive=agg if agg is not None else 0.0,
                conservative=con if con is not None else 0.0,
            )
        )

    yellow = _coerce_number(raw.get("yellow_line", 50.0), "yellow_line", errors)
    red = _coerce_number(raw.get("red_line", 80.0), "red_line", errors)
    if yellow is None:
        yellow = 50.0
    if red is None:
        red = 80.0

    if errors:
        raise ValidationError(errors)

    project = ProjectInput(
        name=name.strip(),
        resources=resources,
        tasks=tasks,
        yellow_line=yellow,
        red_line=red,
    )
    semantic_errors = validate(project)
    if semantic_errors:
        raise ValidationError(semantic_errors)
    return project


def validate(project: ProjectInput) -> list[dict[str, str]]:
    """Semantic validation of a structurally parsed project."""
    errors: list[dict[str, str]] = []
    resource_names = {r.name for r in project.resources}
    task_index = {t.name: i for i, t in enumerate(project.tasks)}
    task_names = set(task_index)

    for i, t in enumerate(project.tasks):
        base = f"tasks[{i}]"
        for j, p in enumerate(t.predecessors):
            if p not in task_names:
                errors.append(
                    {
                        "field": f"{base}.predecessors[{j}]",
                        "message": f"references task {p!r} which does not exist",
                    }
                )
            if p == t.name:
                errors.append(
                    {"field": f"{base}.predecessors[{j}]", "message": "task cannot depend on itself"}
                )
        for rname, qty in t.resources.items():
            if rname not in resource_names:
                errors.append(
                    {
                        "field": f"{base}.resources.{rname}",
                        "message": f"references resource {rname!r} which does not exist",
                    }
                )
        if t.aggressive < 0:
            errors.append({"field": f"{base}.aggressive", "message": "duration must not be negative"})
        if t.conservative < 0:
            errors.append({"field": f"{base}.conservative", "message": "duration must not be negative"})
        if t.aggressive > t.conservative:
            errors.append(
                {
                    "field": f"{base}.aggressive",
                    "message": (
                        f"aggressive duration ({t.aggressive}) must not exceed "
                        f"conservative duration ({t.conservative})"
                    ),
                }
            )
        for rname, qty in t.resources.items():
            if rname in resource_names:
                cap = next(r.capacity for r in project.resources if r.name == rname)
                if qty > cap + 1e-9:
                    errors.append(
                        {
                            "field": f"{base}.resources.{rname}",
                            "message": (
                                f"task requires {qty} of resource {rname!r} but total "
                                f"capacity is {cap}"
                            ),
                        }
                    )

    # dependency cycle detection (depth-first, deterministic by input order)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {name: WHITE for name in task_names}
    cycle: list[str] = []

    def visit(name: str, stack: list[str]) -> bool:
        color[name] = GRAY
        stack.append(name)
        task = project.task(name)
        assert task is not None
        for p in task.predecessors:
            if p not in task_names:
                continue  # dangling reference already reported
            if color[p] == GRAY:
                start = stack.index(p)
                cycle.extend(stack[start:] + [p])
                return True
            if color[p] == WHITE and visit(p, stack):
                return True
        stack.pop()
        color[name] = BLACK
        return False

    # A predecessor pointing to a known GRAY node closes a cycle even if the
    # same task also has a dangling reference, so iterate over all tasks.
    for t in project.tasks:
        if color[t.name] == WHITE and visit(t.name, []):
            errors.append(
                {
                    "field": "tasks.predecessors",
                    "message": "dependency cycle detected: " + " -> ".join(cycle),
                }
            )
            break

    y, r = project.yellow_line, project.red_line
    if not (0.0 <= y <= 100.0):
        errors.append({"field": "yellow_line", "message": "must be between 0 and 100"})
    if not (0.0 <= r <= 100.0):
        errors.append({"field": "red_line", "message": "must be between 0 and 100"})
    if 0.0 <= y <= 100.0 and 0.0 <= r <= 100.0 and y > r:
        errors.append({"field": "yellow_line", "message": "yellow line must not be above the red line"})

    return errors
