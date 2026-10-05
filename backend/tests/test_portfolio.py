"""Portfolio tests: pool validation, drum identification, staggering,
concurrency, restart recovery — plus the acceptance scenario end to end."""
import concurrent.futures

import pytest
from fastapi.testclient import TestClient

from app import portfolio
from app.api.main import create_app
from app.storage import Storage


@pytest.fixture
def client(tmp_path):
    store = Storage(str(tmp_path / "portfolio.db"))
    app = create_app(store)
    with TestClient(app) as c:
        c.store = store
        c.db_path = str(tmp_path / "portfolio.db")
        yield c


def four_task(resource="工程师", cap=1, shared=False, name="四任务"):
    res = {"name": resource, "capacity": cap}
    if shared:
        res["shared"] = True
    return {
        "name": name,
        "resources": [res],
        "tasks": [
            {"name": "A", "predecessors": [], "resources": {},
             "aggressive": 4, "conservative": 8},
            {"name": "B", "predecessors": ["A"], "resources": {resource: 1},
             "aggressive": 6, "conservative": 12},
            {"name": "C", "predecessors": ["A"], "resources": {resource: 1},
             "aggressive": 3, "conservative": 6},
            {"name": "D", "predecessors": ["B", "C"], "resources": {},
             "aggressive": 2, "conservative": 4},
        ],
        "yellow_line": 50,
        "red_line": 80,
    }


def _create_project(client, raw):
    r = client.post("/api/projects", json=raw)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _create_portfolio(client, pool, name="组合", drum=None):
    payload = {"name": name, "pool": pool}
    if drum is not None:
        payload["drum"] = drum
    r = client.post("/api/portfolios", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _join(client, portfolio_id, project_id):
    return client.post(
        f"/api/portfolios/{portfolio_id}/projects", json={"project_id": project_id}
    )


def _view(client, portfolio_id):
    r = client.get(f"/api/portfolios/{portfolio_id}")
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Acceptance scenario
# --------------------------------------------------------------------------- #
def test_acceptance_two_copies_staggered_on_one_engineer(client):
    """Pool of one 工程师; two identical four-task copies join in order."""
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}])
    p1 = _create_project(client, four_task(shared=True, name="副本一"))
    p2 = _create_project(client, four_task(shared=True, name="副本二"))

    r1 = _join(client, port, p1)
    assert r1.status_code == 200, r1.text
    assert r1.json()["placement"]["offset"] == 0
    r2 = _join(client, port, p2)
    assert r2.status_code == 200, r2.text
    assert r2.json()["placement"]["offset"] == 9

    view = _view(client, port)
    assert view["drum"] == "工程师"
    m1, m2 = view["members"]
    # each project keeps its own chain shape, length and buffers
    assert m1["offset"] == 0 and m1["makespan"] == 15
    assert m1["critical_chain"] == ["A", "B", "C", "D"]
    assert m2["critical_chain"] == ["A", "B", "C", "D"] and m2["makespan"] == 15
    # second copy finishes no earlier than portfolio workday 24
    assert m2["finish"] == 24
    assert view["makespan"] == 24
    # engineer occupancy never exceeds the pool capacity of 1
    assert view["drum_capacity"] == 1
    assert view["drum_profile"] == [{"start": 4, "finish": 22, "used": 1}]
    assert view["conflicts"] == []


def test_acceptance_projects_entered_as_is_stagger_by_name_match(client):
    """Old-style projects without any shared flag still draw same-named
    pool resources when they join."""
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}])
    p1 = _create_project(client, four_task(shared=False, name="原样一"))
    p2 = _create_project(client, four_task(shared=False, name="原样二"))
    assert _join(client, port, p1).json()["placement"]["offset"] == 0
    assert _join(client, port, p2).json()["placement"]["offset"] == 9
    view = _view(client, port)
    assert [m["finish"] for m in view["members"]] == [15, 24]


def test_no_capacity_buffer_between_projects(client):
    """The second project's drum usage starts exactly at the release."""
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}])
    p1 = _create_project(client, four_task(shared=True))
    p2 = _create_project(client, four_task(shared=True))
    _join(client, port, p1)
    _join(client, port, p2)
    view = _view(client, port)
    m2 = view["members"][1]
    b = next(t for t in m2["tasks"] if t["name"] == "B")
    assert b["start"] == 13  # first project's engineer frees at 13, no padding


# --------------------------------------------------------------------------- #
# Pool payload validation
# --------------------------------------------------------------------------- #
def test_pool_validation_reports_each_field(client):
    r = client.post("/api/portfolios", json={
        "name": "",
        "pool": [
            {"name": "工程师", "capacity": -1},
            {"name": "工程师", "capacity": 1},
            {"name": "", "capacity": "x"},
        ],
        "drum": "不存在的资源",
    })
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    assert "name" in fields
    assert "pool[0].capacity" in fields
    assert "pool[1].name" in fields  # duplicate
    assert "pool[2].name" in fields
    assert "pool[2].capacity" in fields
    assert "drum" in fields


def test_portfolio_list_and_get(client):
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 2}])
    listing = client.get("/api/portfolios").json()["portfolios"]
    assert len(listing) == 1
    assert listing[0]["id"] == port
    assert listing[0]["member_count"] == 0
    view = _view(client, port)
    assert view["pool"] == [{"name": "工程师", "capacity": 2}]
    assert view["members"] == [] and view["drum"] is None


def test_unknown_portfolio_and_project_404(client):
    assert client.get("/api/portfolios/nope").status_code == 404
    port = _create_portfolio(client, pool=[])
    assert _join(client, port, "ghost").status_code == 404
    pid = _create_project(client, four_task())
    assert _join(client, "ghost", pid).status_code == 404


# --------------------------------------------------------------------------- #
# Join validation (per-field, all errors at once)
# --------------------------------------------------------------------------- #
def test_join_rejects_shared_resource_not_in_pool(client):
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}])
    raw = four_task(shared=True)
    raw["resources"].append({"name": "测试机", "capacity": 1, "shared": True})
    raw["tasks"][1]["resources"]["测试机"] = 1
    pid = _create_project(client, raw)
    r = _join(client, port, pid)
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    assert "resources[1].name" in fields


def test_join_rejects_task_demand_above_pool_total(client):
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}])
    raw = four_task(cap=2, shared=True)  # private plan allows 2 parallel
    raw["tasks"][1]["resources"]["工程师"] = 2
    pid = _create_project(client, raw)
    r = _join(client, port, pid)
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    assert "tasks[1].resources.工程师" in fields
    assert "resources[0].capacity" in fields  # declared 2 > pool 1


def test_join_reports_all_offending_fields_at_once(client):
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}])
    raw = four_task(shared=True)
    raw["resources"].append({"name": "电工", "capacity": 1, "shared": True})
    raw["tasks"][1]["resources"] = {"工程师": 5, "电工": 1}
    raw["tasks"][2]["resources"] = {"工程师": 3}
    raw["resources"][0]["capacity"] = 5
    pid = _create_project(client, raw)
    r = _join(client, port, pid)
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    assert fields == {
        "resources[0].capacity",
        "resources[1].name",
        "tasks[1].resources.工程师",
        "tasks[2].resources.工程师",
    }


def test_join_idempotent_rejoin_returns_existing_placement(client):
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}])
    p1 = _create_project(client, four_task(shared=True))
    p2 = _create_project(client, four_task(shared=True))
    _join(client, port, p1)
    _join(client, port, p2)
    again = _join(client, port, p1)
    assert again.status_code == 200
    assert again.json()["already_member"] is True
    view = _view(client, port)
    assert len(view["members"]) == 2
    assert [m["offset"] for m in view["members"]] == [0, 9]


# --------------------------------------------------------------------------- #
# Drum identification
# --------------------------------------------------------------------------- #
def test_drum_auto_identified_by_load_ratio(client):
    port = _create_portfolio(client, pool=[
        {"name": "R1", "capacity": 1},
        {"name": "R2", "capacity": 2},
    ])
    raw = {
        "name": "p",
        "resources": [
            {"name": "R1", "capacity": 1, "shared": True},
            {"name": "R2", "capacity": 2, "shared": True},
        ],
        "tasks": [
            {"name": "T1", "predecessors": [], "resources": {"R1": 1},
             "aggressive": 10, "conservative": 12},  # load 10/1 = 10
            {"name": "T2", "predecessors": [], "resources": {"R2": 2},
             "aggressive": 3, "conservative": 4},  # load 6/2 = 3
        ],
    }
    _join(client, port, _create_project(client, raw))
    assert _view(client, port)["drum"] == "R1"


def test_drum_tie_broken_by_smallest_name(client):
    port = _create_portfolio(client, pool=[
        {"name": "R2", "capacity": 1},
        {"name": "R1", "capacity": 2},
    ])
    raw = {
        "name": "p",
        "resources": [
            {"name": "R1", "capacity": 2, "shared": True},
            {"name": "R2", "capacity": 1, "shared": True},
        ],
        "tasks": [
            {"name": "T1", "predecessors": [], "resources": {"R1": 1},
             "aggressive": 4, "conservative": 5},  # load 4/2 = 2
            {"name": "T2", "predecessors": [], "resources": {"R2": 1},
             "aggressive": 2, "conservative": 3},  # load 2/1 = 2
        ],
    }
    _join(client, port, _create_project(client, raw))
    assert _view(client, port)["drum"] == "R1"  # tie -> lexicographically smallest


def test_drum_pinned_by_admin(client):
    port = _create_portfolio(
        client,
        pool=[{"name": "R1", "capacity": 1}, {"name": "R2", "capacity": 1}],
        drum="R2",
    )
    raw = {
        "name": "p",
        "resources": [
            {"name": "R1", "capacity": 1, "shared": True},
            {"name": "R2", "capacity": 1, "shared": True},
        ],
        "tasks": [
            {"name": "T1", "predecessors": [], "resources": {"R1": 1},
             "aggressive": 10, "conservative": 12},
            {"name": "T2", "predecessors": [], "resources": {"R2": 1},
             "aggressive": 1, "conservative": 2},
        ],
    }
    p1 = _create_project(client, raw)
    p2 = _create_project(client, raw)
    _join(client, port, p1)
    r2 = _join(client, port, p2)
    view = _view(client, port)
    assert view["drum"] == "R2" and view["drum_pinned"] is True
    # staggered on the pinned R2 (1 workday each), not on the heavier R1
    assert [m["offset"] for m in view["members"]] == [0, 1]


def test_auto_drum_frozen_after_first_resolution(client):
    port = _create_portfolio(client, pool=[
        {"name": "R1", "capacity": 1},
        {"name": "R2", "capacity": 1},
    ])
    p1 = _create_project(client, {
        "name": "p1", "resources": [{"name": "R1", "capacity": 1, "shared": True}],
        "tasks": [{"name": "T", "predecessors": [], "resources": {"R1": 1},
                   "aggressive": 5, "conservative": 6}],
    })
    p2 = _create_project(client, {
        "name": "p2", "resources": [{"name": "R2", "capacity": 1, "shared": True}],
        "tasks": [{"name": "T", "predecessors": [], "resources": {"R2": 1},
                   "aggressive": 100, "conservative": 120}],
    })
    _join(client, port, p1)
    r = _join(client, port, p2)
    view = _view(client, port)
    # the heavier later load on R2 does not re-identify the drum
    assert view["drum"] == "R1"
    # p2 draws nothing from the drum, so it starts at 0
    assert r.json()["placement"]["offset"] == 0


# --------------------------------------------------------------------------- #
# Staggering semantics
# --------------------------------------------------------------------------- #
def test_pool_capacity_two_allows_overlap(client):
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 2}])
    p1 = _create_project(client, four_task(shared=True))
    p2 = _create_project(client, four_task(shared=True))
    assert _join(client, port, p1).json()["placement"]["offset"] == 0
    assert _join(client, port, p2).json()["placement"]["offset"] == 0
    p3 = _create_project(client, four_task(shared=True))
    assert _join(client, port, p3).json()["placement"]["offset"] == 9
    view = _view(client, port)
    assert all(r["used"] <= 2 for r in view["drum_profile"])


def test_offset_fills_gap_in_drum_profile(client):
    """Earliest feasible offset: a candidate fitting an early gap uses it."""
    existing = [(4.0, 13.0, 1.0)]
    candidate = [(0.0, 3.0, 1.0)]  # fits into [0, 4)
    assert portfolio.compute_offset(1.0, existing, candidate) == 0.0
    candidate2 = [(0.0, 5.0, 1.0)]  # does not fit -> pushed past 13
    assert portfolio.compute_offset(1.0, existing, candidate2) == 13.0


def test_compute_offset_raises_when_existing_exceeds_capacity():
    with pytest.raises(portfolio.PortfolioConflictError):
        portfolio.compute_offset(1.0, [(0.0, 5.0, 2.0)], [(0.0, 1.0, 1.0)])


def test_non_drum_shared_resource_conflicts_are_listed(client):
    port = _create_portfolio(
        client,
        pool=[{"name": "工程师", "capacity": 1}, {"name": "测试机", "capacity": 1}],
        drum="工程师",
    )
    raw = {
        "name": "p",
        "resources": [
            {"name": "工程师", "capacity": 1, "shared": True},
            {"name": "测试机", "capacity": 1, "shared": True},
        ],
        "tasks": [
            {"name": "X", "predecessors": [], "resources": {"测试机": 1},
             "aggressive": 8, "conservative": 10},
            {"name": "Y", "predecessors": [], "resources": {"工程师": 1},
             "aggressive": 2, "conservative": 3},
        ],
    }
    p1 = _create_project(client, raw)
    p2 = _create_project(client, raw)
    _join(client, port, p1)
    r2 = _join(client, port, p2)
    assert r2.status_code == 200  # conflicts are reported, not blocked
    view = _view(client, port)
    assert [m["offset"] for m in view["members"]] == [0, 2]  # staggered on 工程师
    assert view["conflicts"] == [
        {"resource": "测试机", "start": 2, "finish": 8, "used": 2, "capacity": 1}
    ]


# --------------------------------------------------------------------------- #
# Concurrency and determinism
# --------------------------------------------------------------------------- #
def test_concurrent_joins_equal_serial(client):
    projects = [_create_project(client, four_task(shared=True, name=f"p{i}"))
                for i in range(4)]
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}])

    def join(pid):
        r = _join(client, port, pid)
        assert r.status_code == 200, r.text

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(join, projects))

    view = _view(client, port)
    assert sorted(m["offset"] for m in view["members"]) == [0, 9, 18, 27]
    assert all(r["used"] <= 1 for r in view["drum_profile"])

    # identical projects joined serially produce the very same portfolio
    port2 = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}], name="串行")
    for pid in [_create_project(client, four_task(shared=True, name=f"s{i}"))
                for i in range(4)]:
        _join(client, port2, pid)
    view2 = _view(client, port2)
    assert [m["offset"] for m in view2["members"]] == [0, 9, 18, 27]
    assert view["drum_profile"] == view2["drum_profile"]


def test_concurrent_join_matches_some_serial_order(client):
    def one_task(name):
        return {
            "name": name,
            "resources": [{"name": "E", "capacity": 1, "shared": True}],
            "tasks": [{"name": "T", "predecessors": [], "resources": {"E": 1},
                       "aggressive": 5, "conservative": 6}],
        }

    p = _create_project(client, one_task("P"))
    q = _create_project(client, one_task("Q"))
    port = _create_portfolio(client, pool=[{"name": "E", "capacity": 1}])
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pid: _join(client, port, pid), [p, q]))
    offsets = {m["project_id"]: m["offset"] for m in _view(client, port)["members"]}
    assert offsets in ({p: 0, q: 5}, {p: 5, q: 0})


def test_same_input_same_offsets(client):
    offsets_per_run = []
    for run in range(2):
        port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}],
                                 name=f"run{run}")
        offsets = []
        for i in range(3):
            pid = _create_project(client, four_task(shared=True, name=f"r{run}p{i}"))
            offsets.append(_join(client, port, pid).json()["placement"]["offset"])
        offsets_per_run.append(offsets)
    assert offsets_per_run[0] == offsets_per_run[1] == [0, 9, 18]


# --------------------------------------------------------------------------- #
# Pool maintenance
# --------------------------------------------------------------------------- #
def test_pool_update_capacity_and_drum_pin(client):
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}])
    r = client.put(f"/api/portfolios/{port}", json={
        "pool": [{"name": "工程师", "capacity": 2}],
        "drum": "工程师",
    })
    assert r.status_code == 200, r.text
    view = _view(client, port)
    assert view["pool"] == [{"name": "工程师", "capacity": 2}]
    assert view["drum"] == "工程师" and view["drum_pinned"] is True


def test_pool_update_cannot_remove_drum_or_used_resource(client):
    port = _create_portfolio(client, pool=[
        {"name": "工程师", "capacity": 1},
        {"name": "测试机", "capacity": 1},
    ])
    raw = four_task(shared=True)
    raw["resources"].append({"name": "测试机", "capacity": 1, "shared": True})
    _join(client, port, _create_project(client, raw))

    r = client.put(f"/api/portfolios/{port}", json={"pool": [{"name": "测试机", "capacity": 1}]})
    assert r.status_code == 422
    assert any("工程师" in e["message"] for e in r.json()["errors"])  # the drum

    r = client.put(f"/api/portfolios/{port}", json={"pool": [{"name": "工程师", "capacity": 1}]})
    assert r.status_code == 422
    assert any("测试机" in e["message"] for e in r.json()["errors"])  # used by a member

    # shrinking capacity is allowed; the overload surfaces as a conflict
    r = client.put(f"/api/portfolios/{port}", json={
        "pool": [{"name": "工程师", "capacity": 1}, {"name": "测试机", "capacity": 1}],
    })
    assert r.status_code == 200


def test_pool_update_pin_must_be_pool_resource(client):
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}])
    r = client.put(f"/api/portfolios/{port}", json={"drum": "不存在"})
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "drum"


# --------------------------------------------------------------------------- #
# Restart recovery
# --------------------------------------------------------------------------- #
def test_restart_recovers_identical_portfolio_state(client):
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}])
    for i in range(3):
        _join(client, port, _create_project(client, four_task(shared=True, name=f"p{i}")))
    before_view = _view(client, port)
    before_list = client.get("/api/portfolios").json()

    store2 = Storage(client.db_path)
    app2 = create_app(store2)
    with TestClient(app2) as client2:
        assert client2.get(f"/api/portfolios/{port}").json() == before_view
        assert client2.get("/api/portfolios").json() == before_list


# --------------------------------------------------------------------------- #
# Existing behavior is untouched
# --------------------------------------------------------------------------- #
def test_membership_does_not_change_project_endpoints(client):
    port = _create_portfolio(client, pool=[{"name": "工程师", "capacity": 1}])
    pid = _create_project(client, four_task(shared=True))
    before = client.get(f"/api/projects/{pid}").json()
    _join(client, port, pid)
    _join(client, port, _create_project(client, four_task(shared=True)))
    after = client.get(f"/api/projects/{pid}").json()
    assert after == before  # plan, buffers, thresholds all unchanged
    r = client.post(f"/api/projects/{pid}/reports", json={
        "report_id": "1", "task": "A", "timestamp": "2026-01-05T09:00:00Z",
        "remaining": 4,
    })
    assert r.status_code == 200
