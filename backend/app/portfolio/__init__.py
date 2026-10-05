"""Multi-project portfolios: drum-based staggering across projects.

Split by responsibility (nothing here modifies single-project behaviour):

- validation:    portfolio / pool / membership payload parsing, per-field errors
- drum:          choosing the drum resource (manual or by measured load)
- stagger:       pure scheduling math (earliest feasible start offset, load
                 profiles, non-drum overload windows)
- persistence:   SQLite tables + write serialization for portfolios
- service:       orchestration of join (validate -> resolve drum -> offset)
- api:           HTTP router mounted under /api/portfolios
"""
