"""Report stream, state projection and fever chart tests."""
import pytest

from conftest import four_task_plan, four_task_raw, report
from app import fever
from app.planning import plan_project


# A deterministic report scenario on the four-task project. All timestamps are
# weekdays so the simplified calendar counts elapsed days one-for-one.
def _scenario_reports():
    return [
        report("r1", "A", "2026-01-05T09:00:00Z", remaining=4),
        report("r2", "A", "2026-01-06T09:00:00Z", remaining=3),
        report("r3", "A", "2026-01-07T09:00:00Z", remaining=2),
        report("r4", "A", "2026-01-08T09:00:00Z", remaining=1),
        report("r5", "A", "2026-01-09T09:00:00Z", completed=True),
        report("r6", "B", "2026-01-12T09:00:00Z", remaining=6),
        report("r7", "B", "2026-01-13T09:00:00Z", remaining=5),
    ]


def test_trajectory_has_one_point_per_report_plus_origin(four_task_plan):
    pts = fever.trajectory(four_task_plan, _scenario_reports(), 50, 80)
    assert len(pts) == 8
    assert pts[0]["cc_percent"] == 0
    assert pts[-1]["report_id"] == "r7"


def test_completion_percent(four_task_plan):
    pts = fever.trajectory(four_task_plan, _scenario_reports(), 50, 80)
    # after A completes: 4 of 15 CC workdays
    assert pts[5]["cc_percent"] == pytest.approx(100 * 4 / 15)
    # after one B workday: 5 of 15
    assert pts[7]["cc_percent"] == pytest.approx(100 * 5 / 15)


def test_out_of_order_matches_in_order(four_task_plan):
    ordered = _scenario_reports()
    shuffled = [ordered[i] for i in (6, 0, 5, 3, 1, 4, 2)]
    assert fever.current(four_task_plan, shuffled, 50, 80) == fever.current(
        four_task_plan, ordered, 50, 80
    )
    assert fever.trajectory(four_task_plan, shuffled, 50, 80) == fever.trajectory(
        four_task_plan, ordered, 50, 80
    )


def test_duplicate_report_id_is_idempotent(four_task_plan):
    ordered = _scenario_reports()
    with_dup = ordered + [
        report("r3", "A", "2026-01-07T09:00:00Z", remaining=2),
        report("r7", "B", "2026-01-13T09:00:00Z", remaining=5),
    ]
    assert fever.current(four_task_plan, with_dup, 50, 80) == fever.current(
        four_task_plan, ordered, 50, 80
    )


def test_late_arrival_is_inserted_and_recomputed(four_task_plan):
    """An older-timestamped report arriving late is inserted and state recomputed."""
    ordered = _scenario_reports()
    base = fever.current(four_task_plan, ordered, 50, 80)
    # A correction for B arrives late, timestamped 2026-01-14 (one workday
    # after the latest stored B report, which said 5 left). It states 5 left:
    # a whole elapsed workday consumed no content, so replay adds 1 slip.
    late = report("late1", "B", "2026-01-14T09:00:00Z", remaining=5, seq=99)
    merged = fever.current(four_task_plan, ordered + [late], 50, 80)
    assert merged["slip"] == pytest.approx(base["slip"] + 1.0)
    # insertion is replay: the same set in any submission order is identical
    again = fever.current(
        four_task_plan, list(reversed(ordered + [late])), 50, 80
    )
    assert again == merged


def test_historical_replay_matches_prefix(four_task_plan):
    reports = _scenario_reports()
    cutoff = "2026-01-08T12:00:00Z"
    replayed = fever.replay_at(four_task_plan, reports, 50, 80, cutoff)
    prefix = [r for r in reports if r.timestamp <= "2026-01-08T12:00:00+00:00"]
    from_scratch = fever.current(four_task_plan, prefix, 50, 80)
    assert replayed == from_scratch
    # task-state detail is included for the UI
    states = {s["name"]: s for s in replayed["task_states"]}
    assert states["A"]["status"] == "in_progress"
    assert states["A"]["remaining"] == 1
    assert states["B"]["status"] == "pending"


def test_buffer_consumption_and_zones(four_task_plan):
    # push B well behind pace: PB is 7.5 workdays
    reports = _scenario_reports()
    # stall B for several days after the last report (remaining stays 5)
    reports += [
        report("s1", "B", "2026-01-14T09:00:00Z", remaining=5),
        report("s2", "B", "2026-01-15T09:00:00Z", remaining=5),
        report("s3", "B", "2026-01-16T09:00:00Z", remaining=5),
        report("s4", "B", "2026-01-19T09:00:00Z", remaining=5),
        report("s5", "B", "2026-01-20T09:00:00Z", remaining=5),
    ]
    point = fever.current(four_task_plan, reports, 50, 80)
    # 5 stalled workdays vs 0 content, after being on pace -> slip 5
    assert point["slip"] == pytest.approx(5.0)
    assert point["buffer_percent"] == pytest.approx(100 * 5 / 7.5)
    assert point["zone"] == "red"


def test_green_zone_on_time(four_task_plan):
    point = fever.current(four_task_plan, _scenario_reports(), 50, 80)
    assert point["zone"] == "green"


def test_critical_chain_is_frozen(four_task_plan):
    """Execution never re-identifies the CC: points always use plan tasks."""
    p = four_task_plan
    cc_names = p.critical_chain
    reports = _scenario_reports()
    points = fever.trajectory(p, reports, 50, 80)
    for pt in points:
        crit_flags = {s["name"]: s["is_critical"] for s in pt["task_states"]}
        assert {n for n, c in crit_flags.items() if c} == set(cc_names)


def test_same_timestamp_reports_are_snapshots(four_task_plan):
    """Two reports for one task at the same instant: the later id wins, no slip."""
    reports = [
        report("r-z", "A", "2026-01-05T09:00:00Z", remaining=1),
        report("r-a", "A", "2026-01-05T09:00:00Z", remaining=4),
    ]
    point = fever.current(four_task_plan, reports, 50, 80)
    a = next(s for s in point["task_states"] if s["name"] == "A")
    assert a["remaining"] == 1  # (a,4) applied first, (z,1) is the final snapshot
    assert a["slip"] == 0.0
    # the order-sensitive property holds in the other submission order too
    point2 = fever.current(four_task_plan, list(reversed(reports)), 50, 80)
    assert point2 == point
