---
name: Review Plan
description: "Historical position analysis: cumulative return chart + metrics table + rebalancing comparison. The review layer of 7S: generates evidence from historical data to evaluate position performance."
read_when:
  - User asks for historical/review analysis
  - User asks for position performance review
  - User asks for rebalancing comparison
allowed-tools:
  - Bash(python:skills/review_plan/scripts/review_plan.py)
---

<!--
soul: SOUL.md § Core standards
  "Evidence before claims"          → always show metrics + chart before conclusions
  "Keep the human in control"       → review is read-only; no trades triggered

identity: IDENTITY.md § Scope
  "signal synthesis and reporting artifacts" → review charts and metrics are reporting artifacts
-->

# Review Plan

> **Role in 7S**: The *review* layer. Produces historical performance evidence — charts, risk metrics, and rebalancing comparisons — that feed into investor review.
> **Audience**: Human plan manager reviewing position history.

---

## Human Triggers

**Keyword**: `review`

**When to trigger**: Human wants historical performance evidence — charts, risk metrics, rebalancing history.

**Natural Language** (exclusive territory — PAST performance. NOT live signals, NOT drift decision):

| Intent | What to say |
|--------|-------------|
| Historical performance | *"cn_hb 历史业绩分析"* / *"us_hb 过往收益"* |
| With custom threshold | *"cn_hb 历史业绩，漂移阈值 8%"* |
| Rebalancing comparison | *"看看 us_hb 的调仓对比"* |
| Per-asset breakdown | *"各个标的的历史表现"* |
| Full history review | *"看看过去这段时期的复盘"* |

**Cross-skill routing**:
- Want **live signals**? → use `analyze`: *"今天有什么信号"*
- Want **drift decision**? → use `decide`: *"要不要调仓"*
- Want **Feishu delivery**? → use `report`: *"发日报"*
- `review` is read-only — no trades triggered

---

## What Gets Generated

```
review_plan.py
    → logs/review/{date}_{plan}_{region}_review.png  (chart + metrics table)
    → review_section (markdown)
    → Feishu push (optional)
```

### Chart Contents
- Combined position line (bold)
- Individual asset lines (muted)
- Metrics table below chart

### Metrics Computed
| Metric | Description |
|--------|-------------|
| Total Return | Period return |
| Annualized Return | Geometric mean, 252 trading days |
| Volatility | Annualised daily std |
| Max Drawdown | Largest peak-to-trough |
| Sharpe Ratio | (ann_return - rf) / ann_vol |
| Sortino Ratio | (ann_return - rf) / downside_dev |
| Calmar Ratio | ann_return / max_drawdown |
| Worst Day | Single worst daily return |

### Rebalancing Comparison
- **With rebalancing**: drift > threshold triggers rebalance back to target
- **Without rebalancing**: hold initial weights throughout period
- Compares: total return, max drawdown, Sharpe, Sortino, Calmar, rebalance count

---

<!-- ─────────────────────────────────────────────────────────────── -->
<!-- machine section — technical reference                         -->
<!-- ─────────────────────────────────────────────────────────────── -->

## Entry Point

```
review_plan.py <command>
```

## Invocation

```bash
# Basic review (no holdings JSON needed — reads plan config directly)
python3 skills/review_plan/scripts/review_plan.py \
  --plan cn_hb \
  --region CN

# With custom drift threshold
python3 skills/review_plan/scripts/review_plan.py \
  --plan us_hb \
  --region US \
  --drift 0.08

# With custom lookback
python3 skills/review_plan/scripts/review_plan.py \
  --plan cn_hb \
  --region CN \
  --lookback 1000

# With custom risk-free rate
python3 skills/review_plan/scripts/review_plan.py \
  --plan us_hb \
  --region US \
  --rf 0.03
```

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--plan` | Yes | — | Plan name, e.g. `cn_hb`, `us_hb` |
| `--plan-version` | No | latest | Plan version number |
| `--region` | Yes | — | Region: `CN` or `US` |
| `--holdings` | No | — | Path to holdings JSON (optional, reads plan config directly by default) |
| `--lookback` | No | 500 | Lookback trading days |
| `--drift` | No | 0.10 | Rebalance trigger threshold (decimal) |
| `--rf` | No | region default | Risk-free rate override |
| `--output-dir` | No | logs/review/ | Output directory |

## Data Sources

| Data | Source |
|------|--------|
| Plan config | `config/plans/{plan_id}/v{ver}.json` (asset list + target weights) |
| Asset prices | `knowledge/{region}/3_processed/*.csv` |
| Live fallback | `data-daily-update` (CN: akshare, US: yfinance) |
| Risk-free rate | Region default (CN: 2%, US: 4%) or `--rf` override |

## Output Files

| File | Location |
|------|----------|
| Chart | `logs/review/{date}_{plan}_{region}_review.png` |
| Review | stdout (markdown section) |

## Architecture

```
review_plan.py
  ├── load_total_return_series()  → from knowledge CSVs
  ├── _fetch_live_total_return() → from data-daily-update
  ├── compute_position_series()  → weighted combination
  ├── _simulate_position()       → rebalancing simulation
  ├── compute_metrics()          → risk metrics
  ├── _generate_chart()           → PNG output
  └── _build_review_section()     → markdown output
```

## Evidence Requirements

```
来源：
- 持仓快照：logs/positions/{date}_{plan}_{region}.json
- 价格数据：knowledge/{region}/3_processed/*.csv (via data-daily-update)
- 漂移阈值：--drift 参数（或默认 0.10）
```
