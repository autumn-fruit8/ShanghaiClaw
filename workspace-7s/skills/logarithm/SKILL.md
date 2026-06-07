---
name: Logarithm
description: "Draw semi-log total-return charts with CAGR trend line / rolling OLS curve and drawdown panel. Supports any symbol via adhoc cache + CSI TR bootstrap data resolution, plus plan-weighted portfolio charts and state-based batch charts."
read_when:
  - User asks to draw log-scale charts, total-return trend, CAGR visualization
  - User mentions "对数图", "走势图", "CAGR", "回撤", "全收益"
  - User wants to inspect historical total-return trajectory for any symbol
  - User wants plan-weighted portfolio chart
  - User wants batch charts for active/watchlist/void assets
allowed-tools: Bash(python:skills/logarithm/scripts/draw_log_chart.py)
allowed_tools:
  - bash
---

<!--
 soul: SOUL.md § Core standards
   "Evidence before claims" → always show the chart as visual evidence
   "Clear boundaries between orchestration and skills" → standalone skill, reusable

 identity: IDENTITY.md § Scope
   "signal synthesis and reporting artifacts" → charts are reporting artifacts

 user: USER.md § Approval boundary
   "Ask before changing: external push behavior" → no auto-push, output to adhoc/
-->

# Logarithm

> **Role in 7S**: Visual evidence layer — convert total-return time series into a professional semi-logarithmic chart with trend line (global OLS or rolling) and drawdown/yield panel.
> **Data resolution**: Cache-first with CSI TR bootstrap for CN, yfinance (US), adhoc cache for non-active symbols, knowledge/ for active.
> **Output**: PNG saved to `adhoc/logarithm/`.

---

## Human Triggers

**Keyword**: `log`

**When to trigger**: Human wants a visual chart of total-return logarithmic trajectory.

**Natural Language**:

| Intent | What to say |
|--------|----------------------------------------------------------------------|
| Single symbol, 10Y default | `log 159263` / *"画 159263 的对数图"* |
| With custom years | `log XLV --years 5` / *"画 XLV 最近5年的走势"* |
| All history | `log TLT --all-history` / *"TLT 全历史走势图"* |
| Rolling trend (5Y) | `log 159207 --rolling` / *"看看 159207 的滚动趋势"* |
| Rolling trend (custom N) | `log TLT --rolling 3` / *"TLT 3年滚动趋势"* |
| Explicit region | `log 000218 --region cn` |
| Plan-weighted portfolio | `log --plan cn_bond` / *"画 cn_bond 组合的对数图"* |
| Batch active state | `log --active` / *"为所有活跃持仓画图"* |

**Design rules** (hard-coded in tool, do not override):
- Default = 10Y chart period, global OLS trend line (labeled "CAGR"), 10Y OLS residual LDev
- `--rolling N` changes to rolling OLS trend + N-year rolling residual LDev (chart stays 10Y)
- If data < 10Y, use data length as upper bound
- Bond symbols (long/medium duration) get yield history panel instead of drawdown panel

**Cross-skill routing**:
- Want **raw signals** (LDev, ZScore)? → use `analyze`
- Want **drift decision**? → use `decide`

## Workflow (Agent Execution Sequence)

When triggered via Feishu chat, the agent MUST follow these steps:

```
STEP 1 — Parse intent from chat message
    e.g., "log XLV --years 5" → symbol=XLV, years=5

STEP 2 — Run the chart script with --no-display (server mode)
    python3 skills/logarithm/scripts/draw_log_chart.py --symbol XLV --years 5 --no-display

STEP 3 — Send the generated PNG to the chat
    Use the `message` tool with filePath pointing to:
    adhoc/logarithm/{symbol}_{years}Y_{date}.png
```

> **IMPORTANT**: The script saves charts to `adhoc/logarithm/`. After the script exits,
> the agent MUST send the PNG image to the Feishu chat so the user can see it.

---

## Input Contract

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--symbol` | str | — | Single symbol or comma-separated (e.g., SPYM,TLT,GLDM) |
| `--plan` | str | — | Plan ID to chart as weighted portfolio (e.g., cn_bond) |
| `--active` | flag | — | Draw charts for all active-state symbols |
| `--watchlist` | flag | — | Draw charts for all watchlist symbols |
| `--void` | flag | — | Draw charts for all void symbols |
| `--region` | `{cn, us}` | auto-detect | Region (auto from symbol pattern) |
| `--years` | int | 10 | Backward years; auto-shrink if data insufficient |
| `--all-history` | flag | False | Ignore years limit, use full data |
| `--rolling` | int | 0 (off) | nargs="?", const=5. >0 → rolling OLS trend + rolling residual LDev. `--rolling` = 5Y, `--rolling 3` = 3Y. |
| `--output` | path | auto | Custom output path |
| `--no-display` | flag | False | Save only, no interactive display |
| `--push` | flag | False | Print output path for Feishu push pickup |

### Argument Groups

The following are mutually exclusive:
- `--symbol` (specific asset(s))
- `--plan` (weighted portfolio of a plan's assets)
- `--active` (all active-state assets)
- `--watchlist` (all watchlist assets)
- `--void` (all void assets)

One of the above is required.

---

## Data Resolution

`load_data()` uses a cache-first strategy with CSI TR bootstrap:

```
Symbol input
    │
    ├─ ① adhoc/cache/{symbol}.csv exists?
    │     → Load directly (zero API for subsequent runs)
    │     → Try incremental CSI TR refresh (appends new rows)
    │
    ├─ ② Cache miss → bootstrap (non-active only):
    │     ├─ CSI Total Return index (CN_ETF with TR tracking)
    │     ├─ 天天基金 累计净值 (CN_OTC / bond funds)
    │     └─ Write to adhoc/cache/{symbol}.csv
    │
    ├─ ③ Active state → knowledge/{region}/3_processed/{symbol}.csv
    │
    ├─ ④ Watchlist/void → scan adhoc/{run}/knowledge/.../{symbol}.csv
    │
    ├─ ⑤ Live fetch → DailyUpdateCN / DailyUpdateUS bootstrap
    │
    └─ ⑥ Symbol resolver → final fallback
```

> **⚠️ CSI TR bootstrap**:
> When resolving CSI Total Return indices, always call
> `stock_zh_index_hist_csindex(tr_code, start_date="20000101", ...)` to capture
> full historical data. A conservative date truncates the chart.

---

## Chart Layout

### Non-bond (equity, commodity):
```
┌─────────────────────────────────────────────────────────┐
│ Title: 159263 全收益对数坐标图 (10Y LDev 10Y rolling)     │
├─────────────────────────────────────────────────────────┤
│ ▲ Total Return (对数坐标)                                │
│ │    ┌────────────── 曲线 ────────────────────┐          │
│ │    │     - - - 趋势线 - - -                  │          │
│ │    │  ▓ 过热区  ▓ 低估区 ▓                    │          │
│ │    └─────────────────────────────────────────┘          │
│ └──────────────────────────────→ 时间                    │
├─────────────────────────────────────────────────────────┤
│ ▲ Drawdown                                              │
│ 0% ─────────────────────────────────────────             │
│     \     \    \    \                                     │
│ -20% ──\────\──────\────\───────                          │
│         \     \    \                                      │
│         └────────────────────────→ 时间                  │
└─────────────────────────────────────────────────────────┘
```

### Bond (long/medium duration):
- Top panel: same total-return log chart
- Bottom panel: yield history (10Y or 30Y CGB/US Treasury) with percentile markers

## Output

| Item | Path |
|------|------|
| Single chart PNG | `adhoc/logarithm/{symbol}_{years}Y_{date}.png` |
| Multi/plan PNG | `adhoc/logarithm/{multi}_{years}Y_{date}.png` |
| Cache CSV | `adhoc/cache/{symbol}.csv` (Tier ① only) |

---

## LDev Bands (Overheat / Undervalued Zones)

Thresholds are determined per-symbol via `strategy_type` in asset master:

| Strategy Type | Low σ (undervalued) | High σ (overheat) |
|--------------|--------------------|--------------------|
| STEADY (default) | -1.5σ | +3.0σ |
| VOLATILE | -2.0σ | +1.5σ |
| MOMENTUM | -1.0σ | +3.0σ |

- `global_sigma=True` when not rolling → fixed σ → straight parallel band lines
- `global_sigma=False` when rolling → rolling σ per point (min 500, max 1250 lookback)

---

## Error Handling

| Failure Mode | Detection | Recovery |
|-------------|-----------|----------|
| Cache CSV corrupted | CSV parse error | Re-bootstrap from CSI TR / EM |
| CSI TR API returns empty | DataFrame empty or < 252 rows | Fallback to CN_OTC / Sina |
| CJK font missing | UserWarning: missing glyph | Fallback to DejaVu Sans (no Chinese) |
| Multi-symbol partial failure | Some dfs empty | Continue with remaining symbols |
| No data for any symbol | Empty symbol list | Exit code 1 |

## Invocation

```bash
# Single symbol, default 10 years
python3 skills/logarithm/scripts/draw_log_chart.py --symbol 159263

# Single symbol, custom time window
python3 skills/logarithm/scripts/draw_log_chart.py --symbol XLV --years 5

# All available historical data
python3 skills/logarithm/scripts/draw_log_chart.py --symbol TLT --all-history

# Multi-symbol overlay comparison
python3 skills/logarithm/scripts/draw_log_chart.py --symbol SPYM,TLT,GLDM

# Explicit region override
python3 skills/logarithm/scripts/draw_log_chart.py --symbol 000218 --region cn --years 3

# Rolling trend + LDev (5Y default)
python3 skills/logarithm/scripts/draw_log_chart.py --symbol 003376 --rolling

# Rolling trend with custom window (3Y)
python3 skills/logarithm/scripts/draw_log_chart.py --symbol SPY --years 20 --rolling 3

# Plan-weighted portfolio chart
python3 skills/logarithm/scripts/draw_log_chart.py --plan cn_bond
python3 skills/logarithm/scripts/draw_log_chart.py --plan cn_hb --rolling

# Batch by state
python3 skills/logarithm/scripts/draw_log_chart.py --active
python3 skills/logarithm/scripts/draw_log_chart.py --watchlist --region cn

# Custom output path
python3 skills/logarithm/scripts/draw_log_chart.py --symbol 159263 --output ~/Desktop/chart.png

# Server mode + push marker
python3 skills/logarithm/scripts/draw_log_chart.py --symbol XLV --no-display --push
```

### Region Auto-Detection Rules

| Symbol Pattern | Region | Example |
|----------------|--------|---------|
| 6-digit number | `cn` | 159263, 003376 |
| Ends with `.HK` | `us` | 3032.HK, 3110.HK |
| Alphabetic | `us` | SPYM, XLV, TLT |
| `--region` flag | Override | Always wins if specified |
