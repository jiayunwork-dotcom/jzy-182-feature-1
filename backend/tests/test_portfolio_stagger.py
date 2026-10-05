"""Unit tests for drum selection and the drum staggering math."""
import pytest

from app.portfolio import drum, stagger
from app.portfolio.models import PoolResource, Segment
from app.planning import plan_project
from conftest import four_task_raw

POOL = [PoolResource("Engineer", 1)]


def _plan_drums(raw=None):
    plan = plan_project(raw or four_task_raw()).to_dict()
    return plan


def test_drum_load_is_capacity_normalised_work_content():
    plan = _plan_drums()
    # B(6) + C(3) = 9 workdays of a single engineer, capacity 1 -> 9
    load = drum.resource_load(plan, {"Engineer": "Engineer"}, POOL)
    assert load["Engineer"] == pytest.approx(9.0)


def test_drum_load_divides_by_capacity():
    plan = _plan_drums()
    pool2 = [PoolResource("Engineer", 3)]
    load = drum.resource_load(plan, {"Engineer": "Engineer"}, pool2)
    assert load["Engineer"] == pytest.approx(3.0)


def test_private_resources_contribute_no_load():
    plan = _plan_drums()
    assert drum.resource_load(plan, {}, POOL) == {"Engineer": 0.0}


def test_choose_drum_picks_highest_load_then_name():
    assert drum.choose_drum({"a": 5.0, "b": 9.0}) == "b"
    assert drum.choose_drum({"b": 5.0, "a": 5.0}) == "a"  # name tie-break
    assert drum.choose_drum({"a": 0.0}) is None


def test_segments_translate_and_skip_zero_duration():
    plan = _plan_drums()
    segs = stagger.member_segments("p", plan, {"Engineer": "Engineer"}, 9.0)
    by_task = {s.task: s for s in segs}
    # B was [4,10], C [10,13] in the project plan
    assert (by_task["B"].start, by_task["B"].finish) == (13.0, 19.0)
    assert (by_task["C"].start, by_task["C"].finish) == (19.0, 22.0)
    # A and D use no engineer -> no segments
    assert set(by_task) == {"B", "C"}


def test_earliest_offset_packs_without_idle_gap():
    plan = _plan_drums()
    old = stagger.member_segments("p1", plan, {"Engineer": "Engineer"}, 0.0)
    new = stagger.member_segments("p2", plan, {"Engineer": "Engineer"}, 0.0)
    offset = stagger.earliest_feasible_offset("Engineer", 1.0, old, new)
    # B of project 2 can start as soon as B of project 1 frees the engineer:
    # 10 - 4 = 6? no — project 2's own C at 10+off overlaps project 1's C
    # [10,13], so the earliest is 13 - 4 = 9 (B2 starts at 13).
    assert offset == 9.0


def test_offset_zero_when_drum_unused_by_newcomer():
    plan = _plan_drums()
    old = stagger.member_segments("p1", plan, {"Engineer": "Engineer"}, 0.0)
    assert stagger.earliest_feasible_offset("Engineer", 1.0, old, []) == 0.0


def test_offset_never_overloads_drum():
    plan = _plan_drums()
    segs = stagger.member_segments("p0", plan, {"Engineer": "Engineer"}, 0.0)
    for k in range(1, 6):
        new = stagger.member_segments(f"p{k}", plan, {"Engineer": "Engineer"}, 0.0)
        off = stagger.earliest_feasible_offset("Engineer", 1.0, segs, new)
        segs += stagger.member_segments(f"p{k}", plan, {"Engineer": "Engineer"}, off)
        prof = stagger.load_profile("Engineer", 1.0, segs, 200)
        assert prof["overloaded"] is False


def test_infeasible_when_newcomer_alone_exceeds_capacity():
    # two tasks in the newcomer simultaneously demand 2 of a capacity-1 drum
    new = [
        Segment("p", "X", "E", 0, 5, 1),
        Segment("p", "Y", "E", 1, 4, 1),
    ]
    assert stagger.earliest_feasible_offset("E", 1.0, [], new) is None


def test_capacity_2_allows_same_offset():
    plan = _plan_drums()
    old = stagger.member_segments("p1", plan, {"Engineer": "Engineer"}, 0.0)
    new = stagger.member_segments("p2", plan, {"Engineer": "Engineer"}, 0.0)
    off = stagger.earliest_feasible_offset(
        "Engineer", 2.0, old, new
    )
    assert off == 0.0


def test_overload_windows_detect_non_drum_contention():
    segs = [
        Segment("p1", "X", "R", 0, 5, 1),
        Segment("p2", "Y", "R", 3, 8, 1),
    ]
    windows = stagger.overload_windows("R", 1.0, segs)
    assert windows == [
        {"resource": "R", "start": 3, "finish": 5, "load": 2, "capacity": 1.0}
    ]


def test_load_profile_pieces_and_capacity_line():
    segs = [Segment("p1", "X", "E", 2, 5, 1)]
    prof = stagger.load_profile("E", 1.0, segs, 8)
    loads = [(p["start"], p["finish"], p["load"]) for p in prof["pieces"]]
    assert loads == [(0, 2, 0), (2, 5, 1), (5, 8, 0)]
    assert prof["peak"] == 1
    assert prof["overloaded"] is False


def test_deterministic_offset_repeated():
    plan = _plan_drums()
    old = stagger.member_segments("p1", plan, {"Engineer": "Engineer"}, 0.0)
    new = stagger.member_segments("p2", plan, {"Engineer": "Engineer"}, 0.0)
    results = [
        stagger.earliest_feasible_offset("Engineer", 1.0, old, new)
        for _ in range(10)
    ]
    assert len(set(results)) == 1
