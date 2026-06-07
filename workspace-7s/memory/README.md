# Memory — Agent Session Logs

Dated markdown files recording key decisions, reasoning, and state changes from agent sessions.

## Format

```
memory/
├── YYYY-MM-DD.md          # Session records
└── .gitkeep
```

Each file captures a single agent session's relevant context: what was analyzed, what decisions were made, and what changed in configs or state.

## Purpose

- Traceability: reconstruct why a decision was made on a given date
- Continuity: carry context across agent sessions without re-reading all prior work
- Audit: review decision history for process improvement

Not a substitute for configs or logs — it's meta-context about the agent's reasoning.
