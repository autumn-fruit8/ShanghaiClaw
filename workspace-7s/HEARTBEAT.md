# HEARTBEAT.md

## Daily checks

- confirm the root runner still builds analyze inputs correctly
- confirm the persistent baseline still matches the intended Jarvis-style state layout
- confirm temp manifest runs remain isolated
- confirm the latest snapshot and report artifacts are generated

## Refactor checks

- keep business orchestration in scripts/analyze/
- keep S1 to S7 logic explicit and inspectable
- avoid reintroducing active dependence on archived Jarvis-only skills

## Release checks

- run targeted pytest coverage
- run one real dry run
- inspect generated artifacts under `adhoc/`
