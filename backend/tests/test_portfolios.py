"""Portfolio API tests: acceptance case, validation, concurrency, restart."""
import concurrent.futures

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.portfolio import drum
from app.storage import Storage
from conftest import four_task_raw


@pytest.fixture
def client(tmp_path):
    store = Storage(str(tmp_path / "api.db"))
    app = create_app(store)
    with TestClient(app) as c:
        c.store = store
        c.db_path = str(tmp_path / "api.db")
        yield c


def _four_task_project(client, name):
    raw = four_task_raw()
    raw["name"] = name
    r = client.post("/api/projects", json=raw)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _portfolio(client, name="组合", resources=None, drum=None):
    body = {"name": name, "resources": resources or [{"name": "Engineer", "capacity": 1}]}
    if drum is not None:
        body["drum"] = drum
    r = client.post("/api/portfolios", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _join(client, pfid, pid, mapping=None):
    if mapping is None:
        mapping = {"Engineer": "Engineer"}
    body = {"project_id": pid, "shared_resources": mapping}
    return client.post(f"/api/portfolios/{pfid}/members", json=body)


# --------------------------------------------------------------------------- #
# Acceptance scenario
# --------------------------------------------------------------------------- #
def test_acceptance_two_copies_stagger_on_drum(client):
    p1 = _four_task_project(client, "P1")
    p2 = _four_task_project(client, "P2")
    pfid = _portfolio(client)

    b1 = _join(client, pfid, p1).json()
    assert b1["members"][0]["offset"] == 0
    assert b1["members"][0]["makespan"] == 15  # chain length unchanged

    b2 = _join(client, pfid, p2).json()
    m1, m2 = b2["members"]
    assert m1["offset"] == 0
    # planned completion no earlier than portfolio workday 24
    assert m2["offset"] + m2["makespan"] >= 24
    assert m2["offset"] == 9 and m2["offset"] + m2["makespan"] == 24

    # each project's own critical chain is untouched
    assert m1["critical_chain"] == ["A", "B", "C", "D"]
    assert m2["critical_chain"] == ["A", "B", "C", "D"]

    # engineer usage never exceeds 1 anywhere on the portfolio axis
    prof = b2["drum_profile"]
    assert prof["resource"] == "Engineer"
    assert prof["capacity"] == 1
    assert prof["peak"] <= 1
    assert prof["overloaded"] is False


def test_drum_profile_covers_full_timeline_and_pieces(client):
    p1 = _four_task_project(client, "P1")
    pfid = _portfolio(client)
    b = _join(client, pfid, p1).json()
    pieces = b["drum_profile"]["pieces"]
    # engineer is busy on B [4,10) and C [10,13)
    busy = [(p["start"], p["finish"]) for p in pieces if p["load"] > 0]
    assert busy == [(4, 10), (10, 13)]


def test_drum_auto_resolved_and_persisted(client):
    p1 = _four_task_project(client, "P1")
    pfid = _portfolio(client)
    b = _join(client, pfid, p1).json()
    assert b["drum"] == "Engineer"
    assert b["drum_mode"] == "auto"
    listed = client.get("/api/portfolios").json()["portfolios"]
    row = next(p for p in listed if p["id"] == pfid)
    assert row["drum"] == "Engineer" and row["member_count"] == 1


def test_drum_can_be_named_manually(client):
    p1 = _four_task_project(client, "P1")
    pfid = _portfolio(client, resources=[{"name": "E", "capacity": 1}], drum="E")
    b = client.get(f"/api/portfolios/{pfid}").json()
    assert b["drum"] == "E" and b["drum_mode"] == "manual"
    ok = _join(client, pfid, p1, {"Engineer": "E"})
    assert ok.status_code == 200
    assert ok.json()["drum"] == "E"


def test_manual_drum_not_in_pool_rejected_field(client):
    r = client.post(
        "/api/portfolios",
        json={"name": "x", "resources": [{"name": "E", "capacity": 1}], "drum": "Ghost"},
    )
    assert r.status_code == 422
    assert any(e["field"] == "drum" for e in r.json()["errors"])


# --------------------------------------------------------------------------- #
# Validation, all per-field
# --------------------------------------------------------------------------- #
def test_join_unknown_project_rejected(client):
    pfid = _portfolio(client)
    r = _join(client, pfid, "nope")
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "project_id"


def test_join_missing_project_id_field(client):
    pfid = _portfolio(client)
    r = client.post(f"/api/portfolios/{pfid}/members", json={"shared_resources": {}})
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "project_id"


def test_join_pool_resource_name_not_in_pool(client):
    p1 = _four_task_project(client, "P1")
    pfid = _portfolio(client)
    r = _join(client, pfid, p1, {"Engineer": "Ghost"})
    assert r.status_code == 422
    fields = [e["field"] for e in r.json()["errors"]]
    assert "shared_resources.Engineer" in fields


def test_join_project_resource_name_not_on_project(client):
    p1 = _four_task_project(client, "P1")
    pfid = _portfolio(client)
    r = _join(client, pfid, p1, {"Engineer": "Engineer", "Nope": "Engineer"})
    assert r.status_code == 422
    fields = [e["field"] for e in r.json()["errors"]]
    assert "shared_resources.Nope" in fields


def test_join_task_requirement_over_pool_total_rejected(client):
    # a project whose task needs 2 of a resource it itself owns with cap 2
    raw = {
        "name": "big",
        "resources": [{"name": "R", "capacity": 2}],
        "tasks": [
            {"name": "T", "predecessors": [], "resources": {"R": 2},
             "aggressive": 3, "conservative": 5}
        ],
    }
    pid = client.post("/api/projects", json=raw).json()["id"]
    pfid = _portfolio(client, resources=[{"name": "E", "capacity": 1}])
    r = _join(client, pfid, pid, {"R": "E"})
    assert r.status_code == 422
    fields = [e["field"] for e in r.json()["errors"]]
    assert "tasks[0].resources.R" in fields


def test_join_reports_all_errors_at_once(client):
    p1 = _four_task_project(client, "P1")
    pfid = _portfolio(client)
    r = _join(client, pfid, p1, {"Engineer": "Ghost", "Phantom": "Ghost2"})
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    # both bad mapping targets and the unknown project resource are reported
    assert {"shared_resources.Engineer", "shared_resources.Phantom"} <= fields


def test_duplicate_join_rejected_and_offsets_unchanged(client):
    p1 = _four_task_project(client, "P1")
    pfid = _portfolio(client)
    _join(client, pfid, p1)
    again = _join(client, pfid, p1)
    assert again.status_code == 422
    assert again.json()["errors"][0]["field"] == "project_id"
    b = client.get(f"/api/portfolios/{pfid}").json()
    assert len(b["members"]) == 1
    assert b["members"][0]["offset"] == 0


def test_create_portfolio_validation_fields(client):
    r = client.post(
        "/api/portfolios",
        json={"name": "", "resources": [
            {"name": "E", "capacity": -1},
            {"name": "E", "capacity": 1},
            {"name": "", "capacity": 1},
        ]},
    )
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    assert "name" in fields
    assert "resources[0].capacity" in fields
    assert "resources[1].name" in fields
    assert "resources[2].name" in fields


def test_pool_capacity_cannot_shrink_below_member_need(client):
    p1 = _four_task_project(client, "P1")
    pfid = _portfolio(client)
    _join(client, pfid, p1)
    r = client.put(
        f"/api/portfolios/{pfid}",
        json={"name": "组合", "resources": [{"name": "Engineer", "capacity": 0}]},
    )
    assert r.status_code == 422
    assert any("capacity" in e["field"] for e in r.json()["errors"])


def test_drum_cannot_change_after_members(client):
    raw = four_task_raw()
    raw["resources"] = [{"name": "Engineer", "capacity": 2}, {"name": "QA", "capacity": 2}]
    raw["name"] = "multi"
    pid = client.post("/api/projects", json=raw).json()["id"]
    pfid = client.post(
        "/api/portfolios",
        json={"name": "m", "resources": [
            {"name": "Engineer", "capacity": 2}, {"name": "QA", "capacity": 2}
        ]},
    ).json()["id"]
    _join(client, pfid, pid, {"Engineer": "Engineer"})
    r = client.put(
        f"/api/portfolios/{pfid}",
        json={"name": "m", "drum": "QA", "resources": [
            {"name": "Engineer", "capacity": 2}, {"name": "QA", "capacity": 2}
        ]},
    )
    assert r.status_code == 422
    assert any(e["field"] == "drum" for e in r.json()["errors"])


# --------------------------------------------------------------------------- #
# Non-drum overloads are hints only
# --------------------------------------------------------------------------- #
def test_non_drum_overload_is_warning_not_blocker(client):
    # Engineer (drum) is touched for 3 days by X; Reviewer is held 6 days by a
    # parallel task Y. Staggering on the drum at offset 3 still leaves the two
    # reviewer windows overlapping -> a hint, not a rejection or a push.
    raw = {
        "name": "rv",
        "resources": [
            {"name": "Engineer", "capacity": 1},
            {"name": "Reviewer", "capacity": 1},
        ],
        "tasks": [
            {"name": "X", "predecessors": [], "resources": {"Engineer": 1},
             "aggressive": 3, "conservative": 5},
            {"name": "Y", "predecessors": [], "resources": {"Reviewer": 1},
             "aggressive": 6, "conservative": 9},
        ],
    }
    p1 = client.post("/api/projects", json=raw).json()["id"]
    p2 = client.post("/api/projects", json=raw).json()["id"]
    pfid = client.post(
        "/api/portfolios",
        json={"name": "p", "resources": [
            {"name": "Engineer", "capacity": 1}, {"name": "Reviewer", "capacity": 1}
        ], "drum": "Engineer"},
    ).json()["id"]
    _join(client, pfid, p1, {"Engineer": "Engineer", "Reviewer": "Reviewer"})
    b = _join(client, pfid, p2, {"Engineer": "Engineer", "Reviewer": "Reviewer"}).json()
    # staggered on engineer -> reviewer windows still overlap -> warning only
    assert b["members"][1]["offset"] == 3
    warned = {w["resource"]: w for w in b["warnings"]}
    assert "Reviewer" in warned
    assert warned["Reviewer"]["windows"] == [
        {"resource": "Reviewer", "start": 3, "finish": 6, "load": 2, "capacity": 1.0}
    ]
    assert b["drum_profile"]["overloaded"] is False


def test_member_without_any_pool_claim_is_rejected(client):
    # brand-new portfolio, drum unresolved: a project declaring no shared
    # resource cannot establish the drum and is rejected
    pfid = _portfolio(client)
    p0 = _four_task_project(client, "P0")
    r = _join(client, pfid, p0, {})
    assert r.status_code == 422
    assert any("drum" in e["message"] for e in r.json()["errors"])

    # once a drum exists, a project keeping everything private joins at 0
    p1 = _four_task_project(client, "P1")
    assert _join(client, pfid, p1, {"Engineer": "Engineer"}).status_code == 200
    p2 = _four_task_project(client, "P2")
    ok = _join(client, pfid, p2, {})
    assert ok.status_code == 200
    assert ok.json()["members"][-1]["offset"] == 0
    assert len(ok.json()["members"]) == 2


# --------------------------------------------------------------------------- #
# Concurrency: equivalent to some serial order, drum never over capacity
# --------------------------------------------------------------------------- #
def test_concurrent_joins_equal_some_serial_order(client):
    n = 6
    pids = [_four_task_project(client, f"P{i}") for i in range(n)]
    pfid = _portfolio(client)

    def join(pid):
        return _join(client, pfid, pid)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(join, pids))

    assert all(r.status_code == 200 for r in responses), [r.text for r in responses if r.status_code != 200]
    b = client.get(f"/api/portfolios/{pfid}").json()
    assert len(b["members"]) == n
    # offsets are a permutation of the serial offsets 0,9,18,... regardless of
    # which request won each serial slot
    offsets = sorted(m["offset"] for m in b["members"])
    assert offsets == [9 * i for i in range(n)]
    # drum never over capacity at any point
    assert b["drum_profile"]["peak"] <= 1
    assert b["drum_profile"]["overloaded"] is False
    # members are totally ordered by position
    positions = [m["position"] for m in b["members"]]
    assert sorted(positions) == list(range(n))
    # each member keeps its own frozen chain
    assert all(m["critical_chain"] == ["A", "B", "C", "D"] for m in b["members"])


def test_concurrent_joins_of_same_project_one_wins(client):
    p1 = _four_task_project(client, "P1")
    pfid = _portfolio(client)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: _join(client, pfid, p1), range(8)))
    wins = [r for r in responses if r.status_code == 200]
    losses = [r for r in responses if r.status_code == 422]
    assert len(wins) == 1 and len(losses) == 7
    b = client.get(f"/api/portfolios/{pfid}").json()
    assert len(b["members"]) == 1


def test_same_input_repeated_gives_identical_offsets(client, tmp_path):
    # build two independent portfolios with the same input sequence
    results = []
    for _ in range(3):
        store = Storage(str(tmp_path / f"db-{_}.db"))
        app = create_app(store)
        with TestClient(app) as c:
            ids = [_four_task_project(c, f"P{i}") for i in range(4)]
            pfid = _portfolio(c)
            for pid in ids:
                _join(c, pfid, pid)
            b = c.get(f"/api/portfolios/{pfid}").json()
            results.append([(m["project_name"], m["offset"]) for m in b["members"]])
    assert results[0] == results[1] == results[2]
    assert [off for _, off in results[0]] == [0, 9, 18, 27]


# --------------------------------------------------------------------------- #
# Restart: identical portfolio state
# --------------------------------------------------------------------------- #
def test_restart_restores_portfolio_exactly(client):
    ids = [_four_task_project(client, f"P{i}") for i in range(3)]
    pfid = _portfolio(client)
    for pid in ids:
        _join(client, pfid, pid)
    before = client.get(f"/api/portfolios/{pfid}").json()
    listing = client.get("/api/portfolios").json()

    store2 = Storage(client.db_path)
    app2 = create_app(store2)
    with TestClient(app2) as client2:
        after = client2.get(f"/api/portfolios/{pfid}").json()
        listing2 = client2.get("/api/portfolios").json()

    # members, order, offsets, drum and load curve survive untouched
    assert after == before
    assert listing2 == listing


# --------------------------------------------------------------------------- #
# Old projects (created before portfolios existed) can join afterwards
# --------------------------------------------------------------------------- #
def test_existing_old_project_can_join_afterwards(tmp_path):
    db = str(tmp_path / "old.db")
    store = Storage(db)
    app = create_app(store)
    with TestClient(app) as c:
        pid = _four_task_project(c, "old")
    # restart with the portfolio-capable app, then create and join
    store2 = Storage(db)
    app2 = create_app(store2)
    with TestClient(app2) as c2:
        pfid = _portfolio(c2)
        r = _join(c2, pfid, pid)
        assert r.status_code == 200
        assert r.json()["members"][0]["offset"] == 0
        assert r.json()["members"][0]["critical_chain"] == ["A", "B", "C", "D"]
        # the original single-project API behaviour is untouched
        detail = c2.get(f"/api/projects/{pid}").json()
        assert detail["plan"]["makespan"] == 15


# --------------------------------------------------------------------------- #
# Auto drum tie-break and load measure
# --------------------------------------------------------------------------- #
def test_auto_drum_tie_break_smallest_resource_name(client):
    raw = {
        "name": "tie",
        "resources": [{"name": "Zed", "capacity": 1}, {"name": "Able", "capacity": 1}],
        "tasks": [
            {"name": "T", "predecessors": [], "resources": {"Zed": 1, "Able": 1},
             "aggressive": 4, "conservative": 6},
        ],
    }
    pid = client.post("/api/projects", json=raw).json()["id"]
    pfid = client.post(
        "/api/portfolios",
        json={"name": "t", "resources": [
            {"name": "Zed", "capacity": 1}, {"name": "Able", "capacity": 1}
        ]},
    ).json()["id"]
    b = _join(client, pfid, pid, {"Zed": "Zed", "Able": "Able"}).json()
    assert b["drum"] == "Able"  # equal load (4/1 each) -> lexicographically smaller


def test_drum_load_measure_unit():
    plan = {"tasks": [
        {"name": "T1", "aggressive": 6, "resources": {"E": 1}},
        {"name": "T2", "aggressive": 3, "resources": {"E": 1}},
    ]}
    from app.portfolio.models import PoolResource
    load = drum.resource_load(plan, {"E": "E"}, [PoolResource("E", 3)])
    assert load["E"] == pytest.approx(3.0)  # (6+3)/3


def test_two_project_resources_folded_onto_one_pool_resource_checked(client):
    # inside one task, two project resources both map to the same capacity-1
    # pool resource: the folded 2 > 1 must be rejected with a task field
    raw = {
        "name": "fold",
        "resources": [
            {"name": "R1", "capacity": 1}, {"name": "R2", "capacity": 1}
        ],
        "tasks": [
            {"name": "T", "predecessors": [], "resources": {"R1": 1, "R2": 1},
             "aggressive": 3, "conservative": 5}
        ],
    }
    pid = client.post("/api/projects", json=raw).json()["id"]
    pfid = _portfolio(client, resources=[{"name": "Pool", "capacity": 1}])
    r = _join(client, pfid, pid, {"R1": "Pool", "R2": "Pool"})
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    assert fields == {"tasks[0].resources.R1"}  # deterministic source name


def test_concurrent_stress_drum_never_overloaded(client):
    n = 12
    pids = [_four_task_project(client, f"P{i}") for i in range(n)]
    pfid = _portfolio(client)

    import random
    rng = random.Random(42)

    def join(pid):
        return _join(client, pfid, pid)

    shuffled = pids[:]
    rng.shuffle(shuffled)
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(join, shuffled))
    assert all(r.status_code == 200 for r in responses)
    b = client.get(f"/api/portfolios/{pfid}").json()
    assert len(b["members"]) == n
    assert b["drum_profile"]["overloaded"] is False
    # every busy drum piece sits at exactly capacity 1 (no idle buffer between
    # projects) until the last member's tail
    busy = [p for p in b["drum_profile"]["pieces"] if p["load"] > 0]
    assert all(p["load"] == 1 for p in busy)


def test_get_unknown_portfolio_404(client):
    assert client.get("/api/portfolios/does-not-exist").status_code == 404
    assert client.post(
        "/api/portfolios/does-not-exist/members", json={"project_id": "x"}
    ).status_code == 404


def test_auto_drum_freezes_after_first_join(client):
    raw = {
        "name": "m",
        "resources": [{"name": "Heavy", "capacity": 1}, {"name": "Light", "capacity": 1}],
        "tasks": [
            {"name": "T", "predecessors": [], "resources": {"Heavy": 1},
             "aggressive": 5, "conservative": 8}
        ],
    }
    p1 = client.post("/api/projects", json=raw).json()["id"]
    pfid = client.post(
        "/api/portfolios",
        json={"name": "p", "resources": [
            {"name": "Heavy", "capacity": 1}, {"name": "Light", "capacity": 1}
        ]},
    ).json()["id"]
    _join(client, pfid, p1, {"Heavy": "Heavy"})
    b = client.get(f"/api/portfolios/{pfid}").json()
    assert b["drum"] == "Heavy"
    # adding a light-heavy later member does not change the frozen drum
    raw2 = {
        "name": "m2",
        "resources": [{"name": "Light", "capacity": 1}],
        "tasks": [
            {"name": "U", "predecessors": [], "resources": {"Light": 1},
             "aggressive": 9, "conservative": 12}
        ],
    }
    p2 = client.post("/api/projects", json=raw2).json()["id"]
    b2 = _join(client, pfid, p2, {"Light": "Light"}).json()
    assert b2["drum"] == "Heavy"


def test_join_same_project_to_two_portfolios_independent(client):
    p1 = _four_task_project(client, "P1")
    pa = _portfolio(client)
    pb = _portfolio(client)
    assert _join(client, pa, p1).status_code == 200
    assert _join(client, pb, p1).status_code == 200
    ba = client.get(f"/api/portfolios/{pa}").json()
    bb = client.get(f"/api/portfolios/{pb}").json()
    assert ba["members"][0]["offset"] == bb["members"][0]["offset"] == 0


def test_drum_mode_can_switch_before_first_join(client):
    pfid = client.post(
        "/api/portfolios",
        json={"name": "m", "resources": [{"name": "E", "capacity": 1}], "drum": "E"},
    ).json()["id"]
    assert client.get(f"/api/portfolios/{pfid}").json()["drum_mode"] == "manual"
    r = client.put(
        f"/api/portfolios/{pfid}",
        json={"name": "m", "resources": [{"name": "E", "capacity": 1}]},
    )
    assert r.status_code == 200
    assert r.json()["drum_mode"] == "auto"
    assert r.json()["drum"] is None


def test_capacity_cut_to_task_limit_keeps_offsets_and_flags_drum(client):
    # capacity 2 lets two identical projects share the drum at offset 0
    pfid = _portfolio(client, resources=[{"name": "Engineer", "capacity": 2}])
    p1 = _four_task_project(client, "P1")
    p2 = _four_task_project(client, "P2")
    _join(client, pfid, p1)
    b = _join(client, pfid, p2).json()
    assert [m["offset"] for m in b["members"]] == [0, 0]

    # cut capacity to 1 (still >= any single task's need of 1 -> allowed)
    r = client.put(
        f"/api/portfolios/{pfid}",
        json={"name": "组合", "resources": [{"name": "Engineer", "capacity": 1}]},
    )
    assert r.status_code == 200, r.text
    after = r.json()
    # offsets are deliberately NOT recomputed; the now-overloaded drum is a hint
    assert [m["offset"] for m in after["members"]] == [0, 0]
    assert after["drum_profile"]["overloaded"] is True
    drum_warning = next(w for w in after["warnings"] if w["kind"] == "drum")
    assert drum_warning["resource"] == "Engineer"
    assert drum_warning["windows"]


def test_single_project_api_unchanged(client):
    # original endpoints keep their exact payloads with portfolios present
    pid = _four_task_project(client, "Solo")
    detail = client.get(f"/api/projects/{pid}").json()
    plan = detail["plan"]
    assert plan["makespan"] == 15
    assert plan["critical_chain"] == ["A", "B", "C", "D"]
    # task starts are project-local, no portfolio offset leaked in
    c = next(t for t in plan["tasks"] if t["name"] == "C")
    assert c["start"] == 10
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"
