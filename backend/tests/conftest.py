"""Shared fixtures: the four-task reference project and helpers."""
import os
import tempfile

import pytest

# importing the API module builds the module-level app; make sure its fallback
# database lives in a throwaway location rather than ./backend/data
os.environ.setdefault("CCPM_DB_PATH", os.path.join(tempfile.gettempdir(), "ccpm-tests.db"))

from app.planning import plan_project  # noqa: E402
from app.reports import make_report  # noqa: E402
from app.storage import Storage  # noqa: E402


def four_task_raw(engineer_capacity=1, scale=1):
    k = scale
    return {
        "name": "four-tasks",
        "resources": [{"name": "Engineer", "capacity": engineer_capacity}],
        "tasks": [
            {"name": "A", "predecessors": [], "resources": {},
             "aggressive": 4 * k, "conservative": 8 * k},
            {"name": "B", "predecessors": ["A"], "resources": {"Engineer": 1},
             "aggressive": 6 * k, "conservative": 12 * k},
            {"name": "C", "predecessors": ["A"], "resources": {"Engineer": 1},
             "aggressive": 3 * k, "conservative": 6 * k},
            {"name": "D", "predecessors": ["B", "C"], "resources": {},
             "aggressive": 2 * k, "conservative": 4 * k},
        ],
        "yellow_line": 50,
        "red_line": 80,
    }


@pytest.fixture
def four_task_plan():
    return plan_project(four_task_raw())


def report(rid, task, ts, remaining=None, completed=False, seq=0):
    payload = {"report_id": rid, "task": task, "timestamp": ts}
    if completed:
        payload["completed"] = True
    else:
        payload["remaining"] = remaining
    return make_report(payload, seq)


@pytest.fixture
def tmp_storage(tmp_path):
    return Storage(str(tmp_path / "test.db"))
