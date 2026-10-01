"""End-to-end API tests: persistence, concurrency and restart equivalence."""
import concurrent.futures

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
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


def _create(client, cap=1, yellow=50, red=80):
    raw = four_task_raw(engineer_capacity=cap)
    raw["yellow_line"] = yellow
    raw["red_line"] = red
    r = client.post("/api/projects", json=raw)
    assert r.status_code == 200, r.text
    return r.json()["id"], raw


def test_health_and_rule(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert "critical" in client.get("/api/rule").json()["rule"]


def test_create_project_returns_plan(client):
    pid, _ = _create(client)
    body = client.get(f"/api/projects/{pid}").json()
    assert body["plan"]["makespan"] == 15
    assert body["plan"]["critical_chain"] == ["A", "B", "C", "D"]


def test_validation_error_has_fields(client):
    r = client.post(
        "/api/projects",
        json={"name": "bad", "resources": [], "tasks": [
            {"name": "A", "predecessors": ["Z"], "aggressive": 9, "conservative": 1}
        ]},
    )
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    assert any("predecessors" in f for f in fields)
    assert any("aggressive" in f for f in fields)


def test_report_flow_and_state_refresh(client):
    pid, _ = _create(client)
    r = client.post(f"/api/projects/{pid}/reports", json={
        "report_id": "1", "task": "A", "timestamp": "2026-01-05T09:00:00Z", "remaining": 4,
    })
    assert r.status_code == 200
    assert r.json()["state"]["task_states"]
    r2 = client.post(f"/api/projects/{pid}/reports", json={
        "report_id": "2", "task": "A", "timestamp": "2026-01-06T09:00:00Z", "remaining": 3,
    })
    assert r2.json()["state"]["cc_percent"] > 0

    traj = client.get(f"/api/projects/{pid}/trajectory").json()
    assert [p["report_id"] for p in traj["points"]] == [None, "1", "2"]

    hist = client.get(f"/api/projects/{pid}/state", params={"at": "2026-01-05T12:00:00Z"}).json()
    assert hist["report_id"] == "1"
    assert any(s["name"] == "A" and s["remaining"] == 4 for s in hist["task_states"])


def test_duplicate_report_idempotent(client):
    pid, _ = _create(client)
    payload = {"report_id": "x", "task": "A", "timestamp": "2026-01-05T09:00:00Z", "remaining": 4}
    a = client.post(f"/api/projects/{pid}/reports", json=payload).json()
    b = client.post(f"/api/projects/{pid}/reports", json=payload).json()
    assert b["duplicate"] is True
    assert a["state"] == b["state"]
    rows = client.get(f"/api/projects/{pid}/reports").json()["reports"]
    assert len(rows) == 1


def test_report_for_unknown_task_rejected(client):
    pid, _ = _create(client)
    r = client.post(f"/api/projects/{pid}/reports", json={
        "report_id": "1", "task": "NOPE", "timestamp": "2026-01-05T09:00:00Z", "remaining": 1,
    })
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "task"


def test_bad_report_payload_fields(client):
    pid, _ = _create(client)
    r = client.post(f"/api/projects/{pid}/reports", json={"report_id": "", "task": "A"})
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    assert "report_id" in fields and "timestamp" in fields and "remaining" in fields


def _report_body(rid, task, day, remaining=None, completed=False):
    body = {"report_id": rid, "task": task, "timestamp": f"2026-01-{day:02d}T09:00:00Z"}
    if completed:
        body["completed"] = True
    else:
        body["remaining"] = remaining
    return body


def test_concurrent_reports_equal_serial_state(client):
    pid, _ = _create(client)
    # serial reference built in a fresh project of the same shape
    pid_ref, _ = _create(client)

    bodies = [
        _report_body("a1", "A", 5, remaining=4),
        _report_body("a2", "A", 6, remaining=3),
        _report_body("a3", "A", 7, remaining=2),
        _report_body("a4", "A", 8, remaining=1),
        _report_body("a5", "A", 9, completed=True),
        _report_body("b1", "B", 12, remaining=6),
        _report_body("b2", "B", 13, remaining=4),
        _report_body("c1", "C", 12, remaining=3),
        _report_body("c2", "C", 13, remaining=1),
    ]
    # serial, in (timestamp, report_id) order
    for b in sorted(bodies, key=lambda x: (x["timestamp"], x["report_id"])):
        client.post(f"/api/projects/{pid_ref}/reports", json=b)
    serial_state = client.get(f"/api/projects/{pid_ref}/state").json()

    # concurrent, shuffled, with duplicates sprinkled in
    shuffled = [bodies[i] for i in (7, 0, 5, 3, 8, 1, 4, 2, 6)] + bodies[:3]

    def post(body):
        return client.post(f"/api/projects/{pid}/reports", json=body)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(post, shuffled))

    final_state = client.get(f"/api/projects/{pid}/state").json()
    assert final_state == serial_state


def test_restart_recovers_identical_state(client):
    pid, _ = _create(client)
    bodies = [
        _report_body("a1", "A", 5, remaining=4),
        _report_body("a2", "A", 6, remaining=3),
        _report_body("a3", "A", 8, remaining=1),
        _report_body("b1", "B", 12, remaining=6),
    ]
    for b in bodies:
        client.post(f"/api/projects/{pid}/reports", json=b)
    before = client.get(f"/api/projects/{pid}/state").json()
    before_traj = client.get(f"/api/projects/{pid}/trajectory").json()
    listing = client.get("/api/projects").json()

    # "restart": build a brand-new app/storage over the same SQLite file
    store2 = Storage(client.db_path)
    app2 = create_app(store2)
    with TestClient(app2) as client2:
        after = client2.get(f"/api/projects/{pid}/state").json()
        after_traj = client2.get(f"/api/projects/{pid}/trajectory").json()
        listing2 = client2.get("/api/projects").json()

    assert after == before
    assert after_traj == before_traj
    assert listing2 == listing
