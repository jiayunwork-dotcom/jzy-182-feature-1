"""Critical Chain project management backend.

Modules are split by responsibility:

- models:      input/output dataclasses
- validation:  network & threshold validation
- scheduler:   resource-constrained serial schedule generation
- chain:       critical chain & feeding chain identification
- buffers:     project / feeding buffer sizing and placement
- planning:    orchestration of the planning phase
- calendar:    work calendar (elapsed workdays between timestamps)
- reports:     append-only report log, ordering, idempotency
- state:       report stream -> per-task runtime state projection
- fever:       fever chart points, zones, historical replay
- storage:     SQLite persistence
- portfolio:   multi-project portfolios (pool, drum, staggering), own package
- api/main:    HTTP layer
"""
