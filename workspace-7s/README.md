# Workspace 7S

This is the staged migration root for the Jarvis 7S system.

7S is an evidence and decision engine, split into two concerns:

**7S = 4S (analyze) + 3S (decide management)**

| Layer | Name | Concern | Status |
|-------|------|---------|--------|
| S1 | Species | Asset fundamentals, category, structure | ✅ Implemented |
| S2 | Situation | Current price, momentum, sentiment | ✅ Implemented |
| S3 | System | Market regime, sector/country rules | ⚠️ Stub only |
| S4 | Strategy | Multi-factor signal synthesis | ✅ Implemented |
| S5 | Self-portrait | Investor constraints (weight, drawdown) | ⚠️ Stub only |
| S6 | Stake | Allocation decisions, rebalancing | ✅ Implemented |
| S7 | Self-evolution | Outcome review, process improvement | ℹ️ Informative only |

**Analyze** = 4S (S1–S4). Produces objective evidence about assets.
**Decide** = 3S (S5 + S6). Applies decisions on top of 4S evidence.

Business states (`void`, `watchlist`, `active`) are maintained manually by the human in `config/states/`.

Current status:

- Analyze action: `skills/analyze/scripts/analyze.py` — produces 4S evidence
- Decide action: `skills/decide/scripts/decide.py` — applies S5+S6 decisions
- Adhoc runs are isolated under `adhoc/` so they do not pollute persistent output
- Cron runs use permanent paths (`logs/`, `knowledge/`) and push by default

## What people use 7S for

### 1. Analyze — 4S evidence

Use `analyze` to gather evidence for a symbol, basket, watchlist slice, or active-holdings slice.

Typical asks:
- what does 4S say about this asset?
- what is the current evidence quality?
- what should I review manually?

### 2. Decide — S5+S6 decisions

Use `decide` after the human has placed assets into `active`. It applies the S6 stake logic on top of the 4S evidence.

Typical asks:
- how should the active set be sized?
- how should a named stake plan be organized?
- does the current active basket need rebalancing?

`decide` reads the 4S output produced by `analyze`; it does not regenerate evidence.

## Root usage

Run the workspace-owned root entry points instead of calling internal skill scripts directly.

Examples:

- single symbol (adhoc, dry-run by default)
  - python3 skills/analyze/scripts/analyze.py --region cn --symbol 005223 --skip-map
- basket selection (adhoc, dry-run by default)
  - python3 skills/analyze/scripts/analyze.py --region cn --symbols 159611,159930,512400 --skip-map
- baseline full-portfolio production run (pushes report, permanent paths)
  - python3 skills/analyze/scripts/analyze.py --region cn --cron --skip-map
- watchlist slice (adhoc, dry-run by default)
  - python3 skills/analyze/scripts/analyze.py --region cn --watchlist --skip-map
- active-holdings slice (adhoc, dry-run by default)
  - python3 skills/analyze/scripts/analyze.py --region cn --active --skip-map
- inspect the normalized analyze input payload
  - python3 skills/analyze/scripts/analyze.py --region cn --symbols 159611,159930,512400 --show-selection
- inspect the S1-S7 decision context
  - python3 skills/analyze/scripts/analyze.py --region cn --symbols 159611,159930,512400 --show-selection

Stake has its own entry point:

- named stake plan
  - python3 skills/decide/scripts/decide.py stake --plan cn_hb
- symbol basket
  - python3 skills/decide/scripts/decide.py stake --basket 005223,159611,159930

Architecture rule:

- `skills/analyze/scripts/analyze.py` and `skills/decide/scripts/decide.py` are the public workspace surfaces
- `skills/analyze/scripts/` owns domain sequencing, normalization, branching, and runtime isolation
- `skills/` owns domain execution logic and adapters, not top-level workflow routing
- dry-run investigation is a runtime mode under `adhoc/`, not a separate subsystem

## What the config files mean

- `config/assets/asset-master.json`
  - the live canonical asset registry loaded by `AssetManifest`
- `config/assets/asset-master.schema.json`
  - the schema for the canonical asset registry and metadata
- `config/states/`
  - the manual business-state database for `void`, `watchlist`, and `active`
- `config/plans/<name>/v<ver>.json`
  - named stake logic and reusable allocation templates (e.g., `cn_hb`, `cn_anti_lost_30`, `us_hb`, `us_anti_inflation`)
- `config/active-regions.json`
  - region enablement and scheduling control for dual-region operation
- `config/engine.yaml`
  - core engine runtime, path, strategy, API, and logging settings

In plain language:

- asset master answers: what assets exist in the durable catalog?
- state db answers: which assets are currently `void`, `watchlist`, or `active`?
- stake plans answer: how should a chosen basket be organized or weighted?
- self profiles answer: how should the investor's constraints modify S7 decisions? (stub)
- logs/decisions/ answers: what decisions were made for each plan?

## Feishu usage

When interacting through Feishu or chat, speak in use-case language instead of file or script language.

Recommended prompt shapes:

- `Analyze CN symbol 005223 and summarize the evidence.`
- `Review the CN watchlist and summarize the evidence.`
- `Decide CN active — what does the stake say?`
- `Decide CN — apply stake plan cn_core_satellite.`

Practical rules:

- include an explicit region
- for `analyze`: use one selector at a time (`--active`, `--watchlist`, `--void`, `--symbol`, `--symbols`)
- for `decide`: use `--active`, `--plan PLAN`, or `--basket`
- cron runs use `--cron` and push by default; adhoc runs are dry by default
- ask for the artifact path under `adhoc/` in the reply
