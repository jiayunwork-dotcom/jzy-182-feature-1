"""Critical chain identification tests."""
import pytest

from app.planning import plan_project
from conftest import four_task_raw


def test_four_task_chain_is_15(four_task_plan):
    """The reference case: resource contention lengthens the chain to 15."""
    p = four_task_plan
    assert p.makespan == 15
    assert p.critical_chain == ["A", "B", "C", "D"]
    # the precedence-only critical path is the A-B-D path of length 12
    assert p.critical_path == ["A", "B", "D"]
    assert p.critical_path_length == 12
    # C was pushed behind B by the shared engineer
    c = next(t for t in p.tasks if t.name == "C")
    assert c.start == 10
    assert c.resource_predecessors == ["B"]


def test_no_resource_conflict_cc_equals_critical_path():
    """With capacity 2 the critical chain must equal the dependency CP."""
    p = plan_project(four_task_raw(engineer_capacity=2))
    assert p.makespan == 12
    assert p.critical_chain == p.critical_path == ["A", "B", "D"]


def test_cc_length_equals_makespan(four_task_plan):
    """The chain found over tight edges always spans the whole makespan."""
    p = four_task_plan
    by_name = {t.name: t for t in p.tasks}
    chain_span = sum(by_name[n].aggressive for n in p.critical_chain)
    assert chain_span == p.makespan


@pytest.mark.parametrize("k", [2, 3, 7, 100])
def test_scaling_preserves_chain_and_scales_length_and_buffers(k):
    """Multiplying every duration by a positive integer scales lengths & buffers."""
    base = plan_project(four_task_raw(scale=1))
    scaled = plan_project(four_task_raw(scale=k))
    assert scaled.critical_chain == base.critical_chain
    assert scaled.makespan == base.makespan * k
    base_pb = next(b for b in base.buffers if b.kind == "project")
    scaled_pb = next(b for b in scaled.buffers if b.kind == "project")
    assert scaled_pb.size == base_pb.size * k
    assert scaled_pb.finish - scaled_pb.start == (base_pb.finish - base_pb.start) * k
    # every task start/finish scales too
    for bt, st in zip(base.tasks, scaled.tasks):
        assert st.start == bt.start * k
        assert st.finish == bt.finish * k


def test_deterministic_same_input_same_output():
    raw = four_task_raw()
    a = plan_project(raw).to_dict()
    b = plan_project(raw).to_dict()
    assert a == b


def test_project_buffer_size_rule(four_task_plan):
    """PB = 1/2 * sum(conservative-aggressive) on the CC = (4+6+3+2)/2."""
    p = four_task_plan
    pb = next(b for b in p.buffers if b.kind == "project")
    assert pb.size == 7.5
    assert pb.start == 15
    assert pb.finish == 22.5


def test_feeding_buffer_placed_at_merge():
    """A non-CC feeder gets a feeding buffer ending at the CC task start."""
    raw = {
        "name": "f",
        "resources": [{"name": "E", "capacity": 1}],
        "tasks": [
            {"name": "A", "predecessors": [], "resources": {"E": 1},
             "aggressive": 5, "conservative": 10},
            {"name": "B", "predecessors": ["A"], "resources": {},
             "aggressive": 10, "conservative": 15},
            {"name": "C", "predecessors": ["A"], "resources": {"E": 1},
             "aggressive": 3, "conservative": 6},
            {"name": "D", "predecessors": ["B", "C"], "resources": {"E": 1},
             "aggressive": 2, "conservative": 4},
        ],
    }
    p = plan_project(raw)
    fb = next(b for b in p.buffers if b.kind == "feeding")
    assert fb.feeding_from == "C"
    assert fb.feeding_into == "D"
    # C spread = 3 -> buffer 1.5; ends at D start (15)
    assert fb.size == 1.5
    assert fb.finish == 15
    assert fb.start == 13.5


def test_resource_conflict_reshapes_chain():
    raw = {
        "name": "r",
        "resources": [{"name": "E", "capacity": 1}],
        "tasks": [
            {"name": "S", "predecessors": [], "resources": {},
             "aggressive": 1, "conservative": 2},
            {"name": "short1", "predecessors": ["S"], "resources": {"E": 1},
             "aggressive": 2, "conservative": 4},
            {"name": "long1", "predecessors": ["S"], "resources": {"E": 1},
             "aggressive": 8, "conservative": 12},
            {"name": "short2", "predecessors": ["short1"], "resources": {"E": 1},
             "aggressive": 2, "conservative": 4},
            {"name": "long2", "predecessors": ["long1"], "resources": {"E": 1},
             "aggressive": 8, "conservative": 12},
            {"name": "E", "predecessors": ["short2", "long2"], "resources": {},
             "aggressive": 1, "conservative": 2},
        ],
    }
    p = plan_project(raw)
    assert p.critical_path_length == 18
    assert p.makespan == 22  # contention serializes both branches
    # the long branch wins first claim; shorts are pushed behind long2
    assert "long1" in p.critical_chain and "short1" in p.critical_chain


def test_zero_duration_tasks_stay_on_chain():
    raw = {
        "name": "z",
        "resources": [{"name": "E", "capacity": 1}],
        "tasks": [
            {"name": "S", "predecessors": [], "resources": {},
             "aggressive": 0, "conservative": 0},
            {"name": "M", "predecessors": ["S"], "resources": {},
             "aggressive": 0, "conservative": 0},
            {"name": "W", "predecessors": ["M"], "resources": {"E": 1},
             "aggressive": 3, "conservative": 5},
        ],
    }
    p = plan_project(raw)
    assert p.makespan == 3
    assert p.critical_chain == ["S", "M", "W"]


def test_tight_feeding_merge_marks_overflow():
    """A feeding buffer larger than the free gap is flagged, not silently moved."""
    raw = {
        "name": "o",
        "resources": [{"name": "E", "capacity": 1}],
        "tasks": [
            {"name": "A", "predecessors": [], "resources": {"E": 1},
             "aggressive": 5, "conservative": 10},
            {"name": "B", "predecessors": ["A"], "resources": {},
             "aggressive": 5, "conservative": 20},
            {"name": "C", "predecessors": ["A"], "resources": {"E": 1},
             "aggressive": 5, "conservative": 6},
            {"name": "D", "predecessors": ["B", "C"], "resources": {},
             "aggressive": 1, "conservative": 2},
        ],
    }
    p = plan_project(raw)
    fb = next(b for b in p.buffers if b.kind == "feeding")
    # C finishes exactly when D starts: free gap is 0, any positive FB overflows
    assert fb.feeding_from == "C"
    assert fb.size == 0.5
    assert fb.overflow is True


def test_feeding_buffer_with_slack_does_not_overflow():
    raw = {
        "name": "slack",
        "resources": [{"name": "E", "capacity": 1}],
        "tasks": [
            {"name": "A", "predecessors": [], "resources": {"E": 1},
             "aggressive": 5, "conservative": 10},
            {"name": "B", "predecessors": ["A"], "resources": {},
             "aggressive": 10, "conservative": 15},
            {"name": "C", "predecessors": ["A"], "resources": {"E": 1},
             "aggressive": 3, "conservative": 6},
            {"name": "D", "predecessors": ["B", "C"], "resources": {"E": 1},
             "aggressive": 2, "conservative": 4},
        ],
    }
    p = plan_project(raw)
    fb = next(b for b in p.buffers if b.kind == "feeding")
    # C finishes at 8, D starts at 15: 7 workdays of slack vs FB 1.5
    assert fb.overflow is False
    assert fb.start == 13.5 and fb.finish == 15
