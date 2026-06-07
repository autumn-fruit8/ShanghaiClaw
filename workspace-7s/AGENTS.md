# AGENTS.md - 7S Workspace Contract

## Purpose

7S is the upgraded decision agent derived from Jarvis patterns, but it runs as an independent OpenClaw workspace.

Its stable execution surface is the workspace root, not the legacy Jarvis skill path.

For human interaction, prefer a simple top-down model:

1. business state
2. human decision
3. 7S evidence

## Human-owned control files

These files define the workspace contract and should only change when explicitly requested by the human:

- AGENTS.md
- IDENTITY.md
- SOUL.md
- TOOLS.md
- HEARTBEAT.md
- USER.md

## Agent-maintained files

These are safe to evolve during normal work when the human asks for improvements:

- MEMORY.md
- scripts/
- skills/
- config/
- tests/
- README.md
- (requirements tracked in human conversation)

## Startup sequence

Before major work in this workspace:

1. Read IDENTITY.md
2. Read SOUL.md
3. Read USER.md
4. Read TOOLS.md
5. Review MEMORY.md
6. Use `skills/analyze/scripts/analyze.py` and workspace-owned scripts

## Operating rules

- Use [skills/analyze/scripts/analyze.py](skills/analyze/scripts/analyze.py) as the primary public analysis entry point
- Use [skills/decide/scripts/decide.py](skills/decide/scripts/decide.py) as the primary public Decide entry point
- **Analyze** human-triggered runs → use [skills/analyze/scripts/analyze.py](skills/analyze/scripts/analyze.py) directly
- **Decide** human-triggered runs → go directly to [workspace-7s/skills/decide/SKILL.md](workspace-7s/skills/decide/SKILL.md) (self-contained, no orchestration)

### Decide command execution (MANDATORY — do not skip)

**Before executing ANY decide command, you MUST read `skills/decide/SKILL.md` and follow the exact command template from the "Invocation" section.**

This is a hard rule, not a suggestion. The LLM MUST NOT construct decide CLI arguments from memory or reasoning alone. The SKILL.md defines the authoritative command template:

```
# Self-portrait (Plan CRUD):
python3 skills/decide/scripts/decide.py self-portrait list
python3 skills/decide/scripts/decide.py self-portrait show --plan-id cn_hb

# Stake (drift → buy/hold/sell):
python3 skills/decide/scripts/decide.py stake --plan cn_hb

# Update positions (separate skill):
python3 skills/update_position/scripts/update_position.py refresh --plan cn_hb us_hb
```

Position snapshots are in `logs/positions/` (SSOT).
- Scheduled runs should use explicit non-interactive commands that follow the same routing contract
- Direct CLI usage is fallback-only for ops, testing, and debugging, not the primary human UX
- Keep the persistent baseline state inside [workspace-7s/knowledge](workspace-7s/knowledge), [workspace-7s/logs](workspace-7s/logs), and [workspace-7s/memory](workspace-7s/memory)
- Mirror the original Jarvis state model for baseline parity
- Put dry-run and investigation artifacts only under `adhoc/`
- Treat chat and Feishu requests as human intents first, not parameter bundles first
- Treat business-state changes as manual human actions, not as automatic 7S output
- Use isolated dry runs when evidence needs to be gathered before a human state decision
- `decide` and `evolve` are standalone layers and must not be collapsed into business-state management
- For evidence-gathering requests, prefer explicit dry runs with region + one analysis input style (`symbol`, `symbols`, `use-default-watchlist`, or `use-active-state`)
- Treat archived legacy code as reference or compatibility material only
- Keep analysis-input logic, dry-run behavior, and evidence flow inside this workspace
- Keep public workflow routing at the workspace root and keep `skills/analyze/scripts/` limited to domain sequencing, normalization, branching, and runtime isolation
- Verify with tests or real dry runs before claiming success

- Treat `config/states/` as the live manual state database and `config/assets/asset-master.json` as the asset catalog only
- Treat `../.rules/ENGINEERING.md` as the authoritative engineering rules

## Skill Documentation Hygiene (MANDATORY)

**Root cause (2026-05-27)**: Skills drifted out of sync because every code change to `skills/*/scripts/*.py` was done without auditing and updating the corresponding `skills/*/SKILL.md`. The SKILL.md serves as the human-readable contract and agent invocation reference — when code evolves, the doc must too.

**Rule: Every skill code change MUST sync SKILL.md**

Whenever modifying a `.py` file under `skills/<name>/scripts/`, the agent MUST:

```
┌─────────────────────────────────────────────────────────────┐
│  STEP 1 — After code change, diff the following:            │
│    - argument names, types, defaults                        │
│    - CLI subcommand structure                               │
│    - output paths and formats                               │
│    - data flow / architecture changes                       │
│                                                             │
│  STEP 2 — Update skills/<name>/SKILL.md to match:           │
│    - Input Contract table (--args, types, defaults)         │
│    - Invocation examples                                    │
│    - Output Contract (file paths, formats)                  │
│    - Architecture diagram (if changed)                      │
│    - Error Handling section (if new failure modes)          │
│    - Data Resolution section (if data paths changed)        │
│                                                             │
│  STEP 3 — If the change is non-trivial, run:                │
│    python3 <script> --help                                  │
│    to verify the code's --help matches SKILL.md's docs      │
└─────────────────────────────────────────────────────────────┘
```

**Verification**: Before completing any PR/merge that touches `skills/*/scripts/*.py`, verify that `skills/*/SKILL.md` was also updated. If SKILL.md hasn't changed, the change is incomplete.

**Exception**: Pure bug fixes (no API surface change) don't require SKILL.md update. But if in doubt, update.

## Safety boundary

7S can analyze, summarize, and propose actions.

7S must not execute trades or mutate external systems without explicit human approval.

## OpenClaw readiness

This workspace is expected to be schedulable, inspectable, and reviewable through one stable root contract.
