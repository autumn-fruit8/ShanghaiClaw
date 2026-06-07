---
name: Backtest
description: "Run 10-year backtest simulation for any symbol or region. Generates performance report with equity curves, trade log, and metrics. Like logarithm — on-demand visualization only, never part of daily cron."
read_when:
  - User asks to backtest, simulate, or review theoretical strategy performance
  - User mentions "回测", "模拟", "策略表现"
  - User wants to see "what if" for a specific asset
  - User asks for backtest universe report
allowed-tools: Bash(python:skills/backtest/scripts/run_backtest.py)
---

<!--
 soul: SOUL.md § Core standards
   "Evidence before claims" → backtest is theoretical simulation, clearly labeled as such
   "Keep the human in control" → pure visualization, no state mutation

 identity: IDENTITY.md § Scope
   "signal synthesis and reporting artifacts" → backtest reports are reporting artifacts

 user: USER.md § Approval boundary
   "No auto-trade execution" → backtest never triggers position changes
-->

# Backtest

> **Role in 7S**: Simulation evidence layer — convert signal-classified data into historical performance visualization.
> **Output**: PNG for single-symbol (Feishu-friendly), Markdown + PNGs for region universe report.

---

## Human Triggers

**Keyword**: `backtest`

**When to trigger**: Human wants theoretical backtest performance of one or more assets.

**Natural Language**:

| Intent | What to say |
|--------|-------------|
| Single symbol | `backtest 159207` / "回测 159207" |
| Full region | `backtest CN` / "回测 CN" |
| With report | `backtest CN --report` / "回测 CN 并生成报告" |
| Report from cached data | `backtest CN report` / "刷新回测报告" |

**Cross-skill routing**:
- Want **current signals** (LDev, ZScore)? → use `analyze`
- Want **log chart**? → use `log`
- Want **drift decision**? → use `decide`

## Workflow (Agent Execution Sequence)

When triggered via Feishu chat, the agent MUST follow these steps:

```
STEP 1 — Parse intent from chat message
    e.g., "回测 159207" → symbol=159207
    e.g., "回测 CN" → region=cn

STEP 2 — Run the backtest script
    Single symbol:
      python3 skills/backtest/scripts/run_backtest.py --symbol 159207
    Full region:
      python3 skills/backtest/scripts/run_backtest.py --region cn

STEP 3 — Send the output to the chat
    Single symbol: send the generated PNG to the chat using the `message` tool
    Region: send a summary text (report location, asset count, top performers)
```

> **IMPORTANT**: For single-symbol runs, the script outputs a PNG path.
> The agent MUST send the PNG image to the Feishu chat so the user can see it.
> For region runs, the agent sends a Markdown summary, not the full report.

---

## Default Strategy Routing

Running backtest without `--strategy` selects profile+tactic based on the asset's `strategy_type` (species), resolved from `config/strategies/routing.yaml`:

| species | Profile | Tactic |
|---------|---------|--------|
| **MOMENTUM** | `momentum` | `follow` |
| **VOLATILE** | `7s-base` | `deep-value` |
| **STEADY** | `7s-base` | `dca` |
| **BOND** | `7s-base` | `dca` |
| *fallback* | `7s-base` | `dca` |

Per-symbol overrides (in `symbols:` section) take priority over species defaults.

Explicit `--strategy` flag overrides routing. Valid values: any species key (`STEADY`, `MOMENTUM`, etc.) or a legacy strategy name with YAML file.

## Input Contract

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--symbol` | str | — | Single symbol to backtest (mutually exclusive with `--region`, `--symbols`, `--active`, `--watchlist`, `--void`) |
| `--symbols` | str | — | Comma-separated symbol basket (e.g. `159263,159207`) |
| `--active` | flag | False | Backtest all active-state assets |
| `--watchlist` | flag | False | Backtest all watchlist assets |
| `--void` | flag | False | Backtest all void assets |
| `--region` | str | `all` | Region to run: `cn`, `us`, `all` (mutually exclusive with `--symbol`) |
| `--date` | str | today | Date marker |
| `--strategy` | str | *(auto)* | Override default routing. Accepts species key (STEADY, MOMENTUM, etc.) or legacy strategy YAML name. Auto-resolved from `strategy_routing.yaml` otherwise. |
| `--report` | flag | False | Generate universe report (region only) |
| `--report-only` | flag | False | Report from cached data, no re-run |

---

## Output

| Intent | Path | Format |
|--------|------|--------|
| Single symbol | `adhoc/backtest/{symbol}_backtest_{date}.png` | Combined PNG (Simulated Actions + Performance) |
| Region | `logs/backtest/{date}_{region}.json` | JSON |
| Region + `--report` | `logs/backtest/{date}_{region}_backtest_report.md` + `{date}_{region}_charts/` | Markdown + PNGs |

---

## Error Handling

| Failure Mode | Recovery |
|-------------|----------|
| Symbol not found in any state | Print error, exit |
| Price data missing | Skip symbol, continue |
| Insufficient history (< 250 rows) | Skip symbol, explain minimum |
| Region has no assets | Print warning, exit |

---

## Invocation

```bash
# Single symbol — generates combined PNG to adhoc/backtest/
python3 skills/backtest/scripts/run_backtest.py --symbol 159207

# Symbol basket
python3 skills/backtest/scripts/run_backtest.py --symbols 159263,159207

# Active-state assets
python3 skills/backtest/scripts/run_backtest.py --active

# Watchlist assets
python3 skills/backtest/scripts/run_backtest.py --watchlist

# Full region backtest + monthly JSON
python3 skills/backtest/scripts/run_backtest.py --region cn

# Full region + universe report + per-asset charts
python3 skills/backtest/scripts/run_backtest.py --region cn --report

# Report from cached data (no re-run)
python3 skills/backtest/scripts/run_backtest.py --region cn --report-only
```
