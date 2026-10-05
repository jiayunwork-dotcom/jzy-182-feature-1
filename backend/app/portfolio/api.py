"""HTTP router for portfolios.

Routes (all JSON, all math already done in :mod:`app.portfolio.service`):

    POST   /api/portfolios                       create pool + drum choice
    GET    /api/portfolios                        list
    GET    /api/portfolios/{id}                   detail: members, offsets,
                                                  timeline, drum load curve,
                                                  non-drum overload warnings
    PUT    /api/portfolios/{id}                   maintain name / resource pool
    POST   /api/portfolios/{id}/members           stagger a project into the pool

Validation problems use the exact same error envelope as project creation:
``422 {"errors": [{"field", "message"}]}`` with field paths reported all at once.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.validation import ValidationError

from .service import NotFound, PortfolioService


def build_router(service: PortfolioService) -> APIRouter:
    router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])

    def _error(exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"errors": getattr(exc, "errors", [])})

    @router.post("")
    def create_portfolio(payload: dict[str, Any]):
        try:
            return service.create(payload)
        except ValidationError as exc:
            return _error(exc)

    @router.get("")
    def list_portfolios():
        return service.list_portfolios()

    @router.get("/{portfolio_id}")
    def get_portfolio(portfolio_id: str):
        try:
            return service.detail(portfolio_id)
        except NotFound:
            return JSONResponse(status_code=404, content={"detail": "portfolio not found"})

    @router.put("/{portfolio_id}")
    def update_portfolio(portfolio_id: str, payload: dict[str, Any]):
        try:
            return service.update(portfolio_id, payload)
        except NotFound:
            return JSONResponse(status_code=404, content={"detail": "portfolio not found"})
        except ValidationError as exc:
            return _error(exc)

    @router.post("/{portfolio_id}/members")
    def add_member(portfolio_id: str, payload: dict[str, Any]):
        try:
            return service.join(portfolio_id, payload)
        except NotFound:
            return JSONResponse(status_code=404, content={"detail": "portfolio not found"})
        except ValidationError as exc:
            return _error(exc)

    return router
