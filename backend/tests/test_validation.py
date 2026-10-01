"""Input rejection tests; each checks the specific offending field."""
import pytest

from app.validation import ValidationError, parse_input

_VALID_TASK = {"name": "A", "predecessors": [], "aggressive": 1, "conservative": 2}


def _raw(**task_over):
    task = dict(_VALID_TASK)
    task.update(task_over)
    return {"name": "p", "resources": [], "tasks": [task]}


def _fields(raw):
    with pytest.raises(ValidationError) as exc:
        parse_input(raw)
    return [e["field"] for e in exc.value.errors]


def test_cycle_rejected():
    raw = {
        "name": "p", "resources": [],
        "tasks": [
            {"name": "A", "predecessors": ["B"], "aggressive": 1, "conservative": 2},
            {"name": "B", "predecessors": ["A"], "aggressive": 1, "conservative": 2},
        ],
    }
    with pytest.raises(ValidationError) as exc:
        parse_input(raw)
    assert any("cycle" in e["message"] for e in exc.value.errors)


def test_self_dependency_rejected():
    assert any("predecessors[0]" in f for f in _fields(_raw(predecessors=["A"])))


def test_missing_task_reference():
    fields = _fields(_raw(predecessors=["GHOST"]))
    assert any("predecessors[0]" in f for f in fields)


def test_missing_resource_reference():
    raw = _raw(resources={"R1": 1})
    fields = _fields(raw)
    assert any(f == "tasks[0].resources.R1" for f in fields)


def test_aggressive_greater_than_conservative():
    fields = _fields(_raw(aggressive=5, conservative=2))
    assert "tasks[0].aggressive" in fields


def test_negative_durations():
    assert "tasks[0].aggressive" in _fields(_raw(aggressive=-1, conservative=2))
    assert "tasks[0].conservative" in _fields(_raw(aggressive=1, conservative=-2))


def test_capacity_exceeded():
    raw = {
        "name": "p",
        "resources": [{"name": "E", "capacity": 2}],
        "tasks": [
            {"name": "A", "predecessors": [], "resources": {"E": 3},
             "aggressive": 1, "conservative": 2}
        ],
    }
    fields = _fields(raw)
    assert "tasks[0].resources.E" in fields


def test_threshold_range_and_order():
    raw = _raw()
    raw["yellow_line"] = 150
    raw["red_line"] = 50
    fields = _fields(raw)
    assert "yellow_line" in fields  # out of range
    raw2 = _raw()
    raw2["yellow_line"], raw2["red_line"] = 90, 50
    fields2 = _fields(raw2)
    assert "yellow_line" in fields2  # yellow above red


def test_duplicate_task_and_resource_names():
    raw = {
        "name": "p",
        "resources": [
            {"name": "E", "capacity": 1},
            {"name": "E", "capacity": 1},
        ],
        "tasks": [
            {"name": "A", "aggressive": 1, "conservative": 2},
            {"name": "A", "aggressive": 1, "conservative": 2},
        ],
    }
    fields = _fields(raw)
    assert any("resources[1].name" in f for f in fields)
    assert any("tasks[1].name" in f for f in fields)


def test_valid_project_passes():
    project = parse_input(
        {
            "name": "ok",
            "resources": [{"name": "E", "capacity": 1}],
            "tasks": [
                {"name": "A", "predecessors": [], "resources": {},
                 "aggressive": 1, "conservative": 2}
            ],
            "yellow_line": 40,
            "red_line": 70,
        }
    )
    assert project.yellow_line == 40
