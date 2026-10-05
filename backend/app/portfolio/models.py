"""Portfolio input/output data structures.

A portfolio owns a *resource pool* (name + capacity). When a project joins, it
declares which of its own resources are actually borrowed from that pool via
``shared_resources`` (project-local resource name -> pool resource name).
Anything left out of the declaration stays project-private and never appears
on the portfolio timeline.

Each membership stores the computed drum ``offset`` in workdays from the
portfolio origin. The project's own plan (critical chain, chain length and
buffers) is never altered — only shifted as a rigid block.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# sentinel: the drum is chosen automatically from measured member load
AUTO_DRUM = ""


@dataclass(frozen=True)
class PoolResource:
    name: str
    capacity: float


@dataclass
class PortfolioInput:
    name: str
    resources: list[PoolResource]
    drum: str = AUTO_DRUM  # pool resource name, or "" for automatic choice


@dataclass
class Segment:
    """One task's usage of one resource, already shifted onto the portfolio."""

    project_id: str
    task: str
    resource: str  # pool resource name
    start: float
    finish: float
    qty: float


@dataclass
class Member:
    project_id: str
    project_name: str
    shared_resources: dict[str, str]  # project resource -> pool resource
    offset: float
    position: int
    makespan: float = 0.0
    critical_chain: list[str] = field(default_factory=list)
