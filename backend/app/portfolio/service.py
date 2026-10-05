"""Portfolio orchestration: validate -> resolve drum -> stagger -> persist.

Every state-changing request runs inside one ``PortfolioStore.transaction()``:
the portfolio row is read, the newcomer is validated and offset, and the
membership row is written before the transaction commits. Two simultaneous
join requests therefore execute as two whole serial steps in *some* order —
never interleaved — which is exactly the required equivalence, and the drum
cannot be double-booked. Re-running the same join after it committed reports
the duplicate membership rather than changing any offset, so the same input
always describes the same resulting state.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Optional

from app import planning
from app.validation import ValidationError

from . import drum as drum_mod
from . import stagger
from .models import Member
from .persistence import DuplicateMembership, PortfolioStore
from .validation import (
    parse_portfolio_input,
    parse_shared_resources,
    validate_join,
    validate_update_against_members,
)


def _plan_of(record: dict[str, Any]) -> dict[str, Any]:
    """Frozen member plan. Stored JSON is authoritative; recompute if absent."""
    return record.get("plan") or planning.plan_project(record["input"]).to_dict()


class NotFound(Exception):
    pass


class PortfolioService:
    def __init__(self, pstore: PortfolioStore):
        self.pstore = pstore

    # ------------------------------------------------------------------ #
    def create(self, raw: dict[str, Any]) -> dict[str, Any]:
        parsed = parse_portfolio_input(raw)
        pid = uuid.uuid4().hex
        drum_mode = "manual" if parsed.drum else "auto"
        with self.pstore.transaction() as conn:
            self.pstore.insert_portfolio(conn, pid, parsed.name, drum_mode, parsed.drum, parsed.resources)
            return self._detail(conn, pid)

    def list_portfolios(self) -> dict[str, Any]:
        with self.pstore.read() as conn:
            rows = self.pstore.list_portfolio_rows(conn)
        return {"portfolios": rows}

    def detail(self, portfolio_id: str) -> dict[str, Any]:
        with self.pstore.read() as conn:
            result = self._detail(conn, portfolio_id)
        if result is None:
            raise NotFound("portfolio not found")
        return result

    def update(self, portfolio_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        parsed = parse_portfolio_input(raw)
        with self.pstore.transaction() as conn:
            row = self.pstore.get_portfolio_row(conn, portfolio_id)
            if row is None:
                raise NotFound("portfolio not found")
            pairs = self._member_pairs(conn, portfolio_id)

            errors: list[dict[str, str]] = []
            # The drum choice is frozen once members exist: changing it would
            # invalidate already-committed offsets.
            requested_manual = bool(parsed.drum)
            if pairs:
                if requested_manual and parsed.drum != row["drum"]:
                    errors.append(
                        {"field": "drum",
                         "message": "drum is fixed once projects have joined the portfolio"}
                    )
                target_drum = row["drum"]
                target_mode = row["drum_mode"]
            else:
                # no members yet: the admin may freely switch auto <-> manual
                target_drum = parsed.drum
                target_mode = "manual" if parsed.drum else "auto"

            member_demands = []
            for m, record in pairs:
                demands: dict[str, float] = {}
                for t in record["input"].get("tasks", []):
                    for proj_name, qty in (t.get("resources") or {}).items():
                        pool_name = m.shared_resources.get(proj_name)
                        if pool_name is not None:
                            demands[pool_name] = max(demands.get(pool_name, 0.0), float(qty))
                member_demands.append(demands)
            errors += validate_update_against_members(
                parsed.resources,
                target_drum,
                member_demands,
                current_drum=row["drum"],
                drum_mode=target_mode,
            )
            if errors:
                raise ValidationError(errors)

            self.pstore.replace_resources(conn, portfolio_id, parsed.resources)
            self.pstore.update_portfolio_row(
                conn, portfolio_id, parsed.name, target_drum, target_mode
            )
            return self._detail(conn, portfolio_id)

    def join(self, portfolio_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError([{"field": "$", "message": "request body must be an object"}])
        project_id = payload.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValidationError([{"field": "project_id", "message": "project id is required"}])
        project_id = project_id.strip()
        mapping, map_errors = parse_shared_resources(payload)
        if mapping is None:
            raise ValidationError(map_errors)

        with self.pstore.transaction() as conn:
            row = self.pstore.get_portfolio_row(conn, portfolio_id)
            if row is None:
                raise NotFound("portfolio not found")
            record = self._get_project_record(conn, project_id)
            if record is None:
                raise ValidationError(
                    [{"field": "project_id", "message": "project does not exist"}]
                )
            if self.pstore.is_member(conn, portfolio_id, project_id):
                raise ValidationError(
                    [{"field": "project_id",
                      "message": "project is already a member of this portfolio"}]
                )

            pool = self.pstore.get_resources(conn, portfolio_id)
            errors = list(map_errors) + validate_join(mapping, record["input"], pool)
            if errors:
                raise ValidationError(errors)

            plan = _plan_of(record)
            pairs = self._member_pairs(conn, portfolio_id)

            # ---- resolve the drum (auto: highest measured load) --------- #
            total_load = {r.name: 0.0 for r in pool}
            for m, rec in pairs:
                drum_mod.merge_loads(total_load, drum_mod.resource_load(_plan_of(rec), m.shared_resources, pool))
            drum_mod.merge_loads(total_load, drum_mod.resource_load(plan, mapping, pool))

            if row["drum_mode"] == "manual":
                drum_name = row["drum"]
            else:
                drum_name = row["drum"] or (drum_mod.choose_drum(total_load) or "")
            if not drum_name and not any(total_load.values()):
                raise ValidationError(
                    [{"field": "shared_resources",
                      "message": "no pool resource is used by any member yet; declare at least "
                                 "one shared resource so the drum can be resolved"}]
                )
            if not drum_name:  # pragma: no cover - defensive (manual drum always in pool)
                raise ValidationError(
                    [{"field": "drum", "message": "portfolio drum is not set; choose one before joining"}]
                )
            drum_capacity = next(r.capacity for r in pool if r.name == drum_name)

            # ---- earliest feasible offset on the drum ------------------- #
            existing_segments: list = []
            for m, rec in pairs:
                existing_segments += stagger.member_segments(
                    m.project_id, _plan_of(rec), m.shared_resources, m.offset
                )
            new_segments = stagger.member_segments(project_id, plan, mapping, 0.0)
            offset = stagger.earliest_feasible_offset(
                drum_name, drum_capacity, existing_segments, new_segments
            )
            if offset is None:
                raise ValidationError(
                    [{"field": "shared_resources",
                      "message": (
                          f"project tasks together exceed the capacity of drum {drum_name!r}; "
                          "no portfolio offset can make it fit"
                      )}]
                )

            position = self.pstore.next_position(conn, portfolio_id)
            try:
                self.pstore.add_member(conn, portfolio_id, project_id, mapping, offset, position)
            except DuplicateMembership:
                raise ValidationError(
                    [{"field": "project_id",
                      "message": "project is already a member of this portfolio"}]
                )
            if not row["drum"] and row["drum_mode"] == "auto":
                conn.execute(
                    "UPDATE portfolios SET drum = ? WHERE id = ?", (drum_name, portfolio_id)
                )
            return self._detail(conn, portfolio_id)

    # ------------------------------------------------------------------ #
    def _member_pairs(
        self, conn: sqlite3.Connection, portfolio_id: str
    ) -> list[tuple[Member, dict[str, Any]]]:
        pairs: list[tuple[Member, dict[str, Any]]] = []
        for m in self.pstore.get_members(conn, portfolio_id):
            record = self._get_project_record(conn, m.project_id)
            assert record is not None  # FK guarantees the project exists
            pairs.append((m, record))
        return pairs

    def _get_project_record(self, conn: sqlite3.Connection, project_id: str) -> Optional[dict[str, Any]]:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["input"] = json.loads(d.pop("input_json"))
        d["plan"] = json.loads(d.pop("plan_json"))
        return d

    def _detail(self, conn: sqlite3.Connection, portfolio_id: str) -> Optional[dict[str, Any]]:
        row = self.pstore.get_portfolio_row(conn, portfolio_id)
        if row is None:
            return None
        pool = self.pstore.get_resources(conn, portfolio_id)
        pairs = self._member_pairs(conn, portfolio_id)

        segments = []
        member_payloads = []
        plans: dict[str, dict[str, Any]] = {}
        for m, record in pairs:
            plan = _plan_of(record)
            plans[m.project_id] = plan
            segments += stagger.member_segments(m.project_id, plan, m.shared_resources, m.offset)
            member_payloads.append(self._member_payload(m, plan))

        horizon = max(
            (m.offset + plans[m.project_id].get("makespan", 0.0) for m, _ in pairs),
            default=0.0,
        )
        drum_name = row["drum"] or None
        drum_payload = None
        drum_windows: list[dict[str, Any]] = []
        if drum_name is not None:
            cap = next((r.capacity for r in pool if r.name == drum_name), 0.0)
            drum_payload = stagger.load_profile(drum_name, cap, segments, horizon)
            # normally impossible (joins pack to capacity), but a pool capacity
            # may later be cut down to the per-task limit: surface it instead
            # of silently redrawing the capacity line through the load
            drum_windows = stagger.overload_windows(drum_name, cap, segments)

        warnings = []
        for r in pool:
            if drum_name is not None and r.name == drum_name:
                windows = drum_windows
                kind = "drum"
            else:
                windows = stagger.overload_windows(r.name, r.capacity, segments)
                kind = "shared"
            if windows:
                warnings.append(
                    {
                        "resource": r.name,
                        "kind": kind,
                        "capacity": r.capacity,
                        "windows": windows,
                        "message": (
                            (
                                f"drum {r.name!r} is over-allocated in {len(windows)} interval(s) "
                                "after the latest pool edit; offsets are kept, raise its capacity"
                            )
                            if kind == "drum"
                            else (
                                f"shared resource {r.name!r} is over-allocated in "
                                f"{len(windows)} interval(s); this version only staggers on the drum"
                            )
                        ),
                    }
                )

        return {
            "id": row["id"],
            "name": row["name"],
            "drum": drum_name,
            "drum_mode": row["drum_mode"],
            "resources": [{"name": r.name, "capacity": r.capacity} for r in pool],
            "members": member_payloads,
            "timeline": {"horizon": stagger.round_num(horizon)},
            "drum_profile": drum_payload,
            "warnings": warnings,
        }

    @staticmethod
    def _member_payload(m: Member, plan: dict[str, Any]) -> dict[str, Any]:
        shifted_tasks = [
            {
                **{k: v for k, v in t.items() if k not in ("start", "finish")},
                "start": stagger.round_num(float(t["start"]) + m.offset),
                "finish": stagger.round_num(float(t["finish"]) + m.offset),
            }
            for t in plan.get("tasks", [])
        ]
        buffers = [
            {
                **b,
                "start": stagger.round_num(float(b["start"]) + m.offset),
                "finish": stagger.round_num(float(b["finish"]) + m.offset),
            }
            for b in plan.get("buffers", [])
        ]
        return {
            "project_id": m.project_id,
            "project_name": m.project_name,
            "position": m.position,
            "offset": stagger.round_num(m.offset),
            "makespan": plan.get("makespan", 0.0),
            "critical_chain": plan.get("critical_chain", []),
            "critical_path": plan.get("critical_path", []),
            "shared_resources": m.shared_resources,
            "tasks": shifted_tasks,
            "buffers": buffers,
            "project_buffer_finish": max(
                (b["finish"] for b in buffers if b["kind"] == "project"),
                default=stagger.round_num(m.offset + float(plan.get("makespan", 0.0))),
            ),
        }
