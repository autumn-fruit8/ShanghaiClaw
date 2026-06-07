# Infrastructure Layer

Execution scaffolding for workspace-7s: process isolation, runtime environment, and pipeline orchestration.

## Files

| File | Purpose |
|------|---------|
| `runtime_env.py` | Environment setup (paths, API keys, region config) |
| `runtime_paths.py` | Canonical path resolution for all data directories |
| `runtime_pipeline.py` | Pipeline orchestration (sequencing, branching, error handling) |
| `pipeline_io.py` | I/O utilities for pipeline stages (read/write artifacts) |
| `process_runner.py` | Subprocess management and dry-run isolation |

## Design principle

Infra provides **plumbing only** — no business logic, no domain knowledge. Skills import infra for path resolution and execution isolation, but own their domain logic.
