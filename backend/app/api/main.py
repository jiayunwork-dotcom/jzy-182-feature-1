"""HTTP layer: all computation happens here in the backend modules."""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import fever, planning, portfolio, portfolio_validation, storage
from app.reports import make_report, validate_report_payload
from app.validation import ValidationError, parse_input

STATIC_DIR = os.environ.get(
    "CCPM_STATIC_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "static")
)


def create_app(store: Optional[storage.Storage] = None) -> FastAPI:
    db_path = os.environ.get("CCPM_DB_PATH", storage.DEFAULT_DB_PATH)
    store = store or storage.Storage(db_path)
    store.init_db()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.init_db()
        yield

    app = FastAPI(title="Critical Chain PM", version="1.0.0", lifespan=lifespan)
    app.state.store = store

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

    # ---------------------------------------------------------------- #
    # Portfolios: shared pool, drum, staggered placement
    # ---------------------------------------------------------------- #
    def _require_portfolio(portfolio_id: str) -> dict[str, Any]:
        record = store.get_portfolio(portfolio_id)
        if record is None:
            raise HTTPException(status_code=404, detail="portfolio not found")
        return record

    def _portfolio_view(portfolio_id: str) -> dict[str, Any]:
        record = store.get_portfolio(portfolio_id)
        members = store.get_portfolio_members(portfolio_id)
        projects = {m["project_id"]: store.get_project(m["project_id"]) for m in members}
        return portfolio.build_view(record, members, projects)

    @app.post("/api/portfolios")
    def create_portfolio(payload: dict[str, Any]):
        try:
            spec = portfolio_validation.parse_create_payload(payload)
        except ValidationError as exc:
            return JSONResponse(status_code=422, content={"errors": exc.errors})
        portfolio_id = uuid.uuid4().hex
        store.save_portfolio(portfolio_id, spec["name"], spec["pool"], spec["drum"])
        return {"id": portfolio_id, "portfolio": _portfolio_view(portfolio_id)}

    @app.get("/api/portfolios")
    def list_portfolios():
        return {"portfolios": store.list_portfolios()}

    @app.get("/api/portfolios/{portfolio_id}")
    def get_portfolio(portfolio_id: str):
        _require_portfolio(portfolio_id)
        return _portfolio_view(portfolio_id)

    @app.put("/api/portfolios/{portfolio_id}")
    def update_portfolio(portfolio_id: str, payload: dict[str, Any]):
        with store.transaction():
            current = _require_portfolio(portfolio_id)
            try:
                changes = portfolio_validation.parse_update_payload(payload)
            except ValidationError as exc:
                return JSONResponse(status_code=422, content={"errors": exc.errors})
            members = store.get_portfolio_members(portfolio_id)
            member_projects = [
                parse_input(store.get_project(m["project_id"])["input"]) for m in members
            ]
            errors = portfolio_validation.update_errors(current, changes, member_projects)
            if errors:
                return JSONResponse(status_code=422, content={"errors": errors})
            store.update_portfolio(
                portfolio_id,
                changes.get("name", current["name"]),
                changes.get("pool", current["pool"]),
                changes.get("drum", current["drum_resource"]),
            )
        return _portfolio_view(portfolio_id)

    @app.post("/api/portfolios/{portfolio_id}/projects")
    def join_portfolio(portfolio_id: str, payload: dict[str, Any]):
        project_id = payload.get("project_id") if isinstance(payload, dict) else None
        if not isinstance(project_id, str) or not project_id.strip():
            return JSONResponse(
                status_code=422,
                content={"errors": [{"field": "project_id", "message": "project id is required"}]},
            )
        project_id = project_id.strip()
        with store.transaction():
            record = _require_portfolio(portfolio_id)
            project_record = store.get_project(project_id)
            if project_record is None:
                raise HTTPException(status_code=404, detail="project not found")
            members = store.get_portfolio_members(portfolio_id)
            if any(m["project_id"] == project_id for m in members):
                # re-joining is a no-op: the frozen placement is returned as-is
                return {"already_member": True, "portfolio": _portfolio_view(portfolio_id)}

            project = parse_input(project_record["input"])
            pool = {p["name"]: p["capacity"] for p in record["pool"]}
            errors = portfolio_validation.join_errors(project, pool)
            if errors:
                return JSONResponse(status_code=422, content={"errors": errors})

            drum = portfolio.effective_drum(record)
            if drum is None:
                placed = [
                    parse_input(store.get_project(m["project_id"])["input"]) for m in members
                ]
                drum = portfolio.identify_drum([*placed, project], pool)
                if drum is not None:
                    store.set_auto_drum(portfolio_id, drum)

            offset = 0.0
            if drum is not None:
                existing = []
                for m in members:
                    placed_record = store.get_project(m["project_id"])
                    existing.extend(
                        portfolio.drum_segments(
                            placed_record["plan"]["tasks"], m["offset"], drum
                        )
                    )
                candidate = portfolio.drum_segments(project_record["plan"]["tasks"], 0.0, drum)
                try:
                    offset = portfolio.compute_offset(pool[drum], existing, candidate)
                except portfolio.PortfolioConflictError as exc:
                    return JSONResponse(
                        status_code=409,
                        content={"errors": [{"field": "pool", "message": str(exc)}]},
                    )
            seq = store.next_join_seq(portfolio_id)
            store.add_portfolio_member(portfolio_id, project_id, offset, seq)
            placement = {
                "project_id": project_id,
                "offset": offset,
                "join_seq": seq,
                "drum": drum,
            }
        return {
            "already_member": False,
            "placement": placement,
            "portfolio": _portfolio_view(portfolio_id),
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
