"""Input and output data structures."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Resource:
    name: str
    capacity: float  # available quantity (non-negative)
    # declared at entry time as drawn from a portfolio's shared pool rather
    # than being a private copy; the standalone plan still uses ``capacity``
    shared: bool = False


@dataclass(frozen=True)
class Task:
    name: str
    predecessors: tuple[str, ...]
    resources: dict[str, float]  # resource name -> quantity used during task
    aggressive: float  # aggressive (optimistic) duration in workdays
    conservative: float  # conservative (safe) duration in workdays


@dataclass
class ProjectInput:
    name: str
    resources: list[Resource]
    tasks: list[Task]
    yellow_line: float = 50.0  # fever chart threshold, percent
    red_line: float = 80.0

    def task(self, name: str) -> Optional[Task]:
        for t in self.tasks:
            if t.name == name:
                return t
        return None


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
@dataclass
class ScheduledTask:
    name: str
    start: float
    finish: float
    aggressive: float
    conservative: float
    is_critical: bool
    resources: dict[str, float]
    predecessors: tuple[str, ...]
    resource_predecessors: list[str]  # tasks it waited for due to contention


@dataclass
class BufferInfo:
    kind: str  # "project" | "feeding"
    start: float
    finish: float
    size: float
    # feeding buffers only: task the feeding chain ends in, and its CC successor
    feeding_from: Optional[str] = None
    feeding_into: Optional[str] = None
    # warning flag: feeding buffer longer than the free gap it lives in
    overflow: bool = False


@dataclass
class Plan:
    project_name: str
    makespan: float
    critical_chain: list[str]
    tasks: list[ScheduledTask]
    buffers: list[BufferInfo]
    # precedence-only critical path length, kept for comparison
    critical_path_length: float
    critical_path: list[str]
    # human-readable description of the scheduling rule applied
    rule: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "makespan": self.makespan,
            "critical_chain": self.critical_chain,
            "critical_path": self.critical_path,
            "critical_path_length": self.critical_path_length,
            "rule": self.rule,
            "tasks": [
                {
                    "name": t.name,
                    "start": t.start,
                    "finish": t.finish,
                    "aggressive": t.aggressive,
                    "conservative": t.conservative,
                    "is_critical": t.is_critical,
                    "resources": t.resources,
                    "predecessors": list(t.predecessors),
                    "resource_predecessors": t.resource_predecessors,
                }
                for t in self.tasks
            ],
            "buffers": [
                {
                    "kind": b.kind,
                    "start": b.start,
                    "finish": b.finish,
                    "size": b.size,
                    "feeding_from": b.feeding_from,
                    "feeding_into": b.feeding_into,
                    "overflow": b.overflow,
                }
                for b in self.buffers
            ],
        }
