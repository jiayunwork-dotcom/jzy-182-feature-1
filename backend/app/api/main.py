"""HTTP layer: all computation happens here in the backend modules."""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import fever, planning, storage
from app.portfolio.api import build_router
from app.portfolio.persistence import PortfolioStore
from app.portfolio.service import PortfolioService
from app.reports import make_report, validate_report_payload
from app.validation import ValidationError, parse_input

STATIC_DIR = os.environ.get(
    "CCPM_STATIC_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "static")
)


def create_app(store: Optional[storage.Storage] = None) -> FastAPI:
    db_path = os.environ.get("CCPM_DB_PATH", storage.DEFAULT_DB_PATH)
    store = store or storage.Storage(db_path)
    store.init_db()

    # portfolios share the same SQLite file/WAL volume but keep their own
    # tables, store object and responsibility-separated package
    pstore = PortfolioStore(store.db_path)
    pstore.init_db()
    portfolio_service = PortfolioService(pstore)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.init_db()
        pstore.init_db()
        yield

    app = FastAPI(title="Critical Chain PM", version="1.0.0", lifespan=lifespan)
    app.state.store = store
    app.state.portfolio_service = portfolio_service
    app.include_router(build_router(portfolio_service))

    def plan_from_record(record: dict[str, Any]):
        return planning.plan_project(record["input"])

    # ---------------------------------------------------------------- #
    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/rule")
    def rule():
        return {"rule": planning.RULE_DESCRIPTION}

    @app.post("/api/projects")
    def create_project(payload: dict[str, Any]):
        try:
            project = parse_input(payload)
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"errors": exc.errors})
        plan = planning.plan_project(project)
        project_id = uuid.uuid4().hex
        store.save_project(
            project_id,
            project.name,
            payload,
            plan.to_dict(),
            project.yellow_line,
            project.red_line,
        )
        return {"id": project_id, "plan": plan.to_dict()}

    @app.get("/api/projects")
    def list_projects():
        return {"projects": store.list_projects()}

    def _require_project(project_id: str) -> dict[str, Any]:
        record = store.get_project(project_id)
        if record is None:
            raise HTTPException(status_code=404, detail="project not found")
        return record

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        record = _require_project(project_id)
        return {
            "id": project_id,
            "name": record["name"],
            "input": record["input"],
            "plan": record["plan"],
            "yellow_line": record["yellow_line"],
            "red_line": record["red_line"],
        }

    @app.post("/api/projects/{project_id}/reports")
    def submit_report(project_id: str, payload: dict[str, Any]):
        record = _require_project(project_id)
        errors = validate_report_payload(payload)
        if errors:
            return JSONResponse(status_code=422, content={"errors": errors})
        task_names = {t["name"] for t in record["input"].get("tasks", [])}
        if payload["task"].strip() not in task_names:
            return JSONResponse(
                status_code=422,
                content={"errors": [
                    {"field": "task", "message": "task does not exist in this project"}
                ]},
            )

        seq = store.next_seq(project_id)
        report = make_report(payload, seq)
        created = store.add_report(project_id, report)
        plan = plan_from_record(record)
        point = fever.current(
            plan, store.get_reports(project_id), record["yellow_line"], record["red_line"]
        )
        return {"duplicate": not created, "state": point}

    @app.get("/api/projects/{project_id}/reports")
    def list_reports(project_id: str):
        _require_project(project_id)
        reports = store.get_reports(project_id)
        return {
            "reports": [
                {
                    "report_id": r.report_id,
                    "task": r.task,
                    "timestamp": r.timestamp,
                    "remaining": r.remaining,
                    "completed": r.is_completed,
                    "sequence": r.sequence,
                }
                for r in reports
            ]
        }

    @app.get("/api/projects/{project_id}/state")
    def project_state(project_id: str, at: Optional[str] = None):
        record = _require_project(project_id)
        plan = plan_from_record(record)
        reports = store.get_reports(project_id)
        if at is not None:
            try:
                return fever.replay_at(
                    plan, reports, record["yellow_line"], record["red_line"], at
                )
            except ValueError:
                return JSONResponse(
                    status_code=422,
                    content={"errors": [{"field": "at", "message": "invalid ISO-8601 timestamp"}]},
                )
        return fever.current(plan, reports, record["yellow_line"], record["red_line"])

    @app.get("/api/projects/{project_id}/trajectory")
    def project_trajectory(project_id: str):
        record = _require_project(project_id)
        plan = plan_from_record(record)
        reports = store.get_reports(project_id)
        return {
            "yellow_line": record["yellow_line"],
            "red_line": record["red_line"],
            "points": fever.trajectory(
                plan, reports, record["yellow_line"], record["red_line"]
            ),
        }

    # static frontend (served by the backend in the runtime image) ------ #
    static_root = os.path.abspath(STATIC_DIR)
    if os.path.isdir(static_root):
        assets = os.path.join(static_root, "assets")
        if os.path.isdir(assets):
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/")
        def index():
            return FileResponse(os.path.join(static_root, "index.html"))

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="not found")
            candidate = os.path.join(static_root, full_path)
            if full_path and os.path.isfile(candidate):
                return FileResponse(candidate)
            return FileResponse(os.path.join(static_root, "index.html"))

    return app


app = create_app()
