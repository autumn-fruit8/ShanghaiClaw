---
name: Analyze
description: "Use when the user asks to analyze assets, check signals, or generate 4S evidence for symbols. This is the S1-S4 evidence generation layer (Species, Situation, System stub, Strategy)."
read_when:
  - User asks to analyze, check signals, or review evidence
  - User mentions specific symbols (e.g. "159263", "A50")
  - User asks about active holdings, watchlist, or void assets
allowed-tools: Bash(python:skills/analyze/scripts/analyze.py)
---

<!--
 soul: SOUL.md § Core standards
   "Evidence before claims"          → always cite source CSVs and config files
   "Keep the human in control"        → analyze is evidence only; position decisions stay with user
   "Readable workflows over hidden coupling" → snapshot JSON is the authoritative evidence artifact

 identity: IDENTITY.md § Scope
   "S1-S4 evidence generation" → produce objective signals, not allocation recommendations

 user: USER.md § Approval boundary
   "No auto-trade execution" → analyze never triggers position changes
-->

# Analyze

S1-S4 evidence generation for 7S decision pipeline.

## Human Triggers

**Keyword**: `analyze`

**When to trigger**: Human wants raw signal evidence — LDev, Z-Score, RSI per asset. No decision, no delivery.

**Natural Language** (exclusive territory — live/now signals. NOT historical performance, NOT drift decision):

| Intent | What to say |
|--------|-------------|
| Specific symbol | *"分析 CN 159263"* / *"看一下 A50 的信号"* |
| Active holdings | *"看看我 A 股持仓的信号"* |
| Watchlist | *"关注列表有什么信号"* |
| Void assets | *"已清仓的资产有什么信号"* |
| Batch symbols | *"分析 159263, 159201, 512880"* |
| Full region | *"看看今天 CN 市场所有信号"* |
| Scheduled run | *Cron job triggers automatically* |

**Cross-skill routing**:
- Want **historical performance**? → use `review`: *"看看历史收益"*
- Want **backtest**? → use `backtest`: *"回测 159207"*
- Want **drift decision**? → use `decide`: *"要不要调仓"*
- Want **Feishu report**? → use `report`: *"发日报"*
- `analyze` produces evidence only — no allocation recommendations

---

## Input Contract

| Input | Source | Description | Required |
|-------|--------|-------------|----------|
| `region` | User or cron | `cn` or `us` or `all` | ✅ |
| `selector` | User | `--symbol`, `--symbols`, `--active`, `--watchlist`, `--void` | ✅ (mutually exclusive) |
| `mode` | User or cron | `adhoc` (default) or `cron` | ❌ |
| `date` | System | Date for analysis (default: today) | ❌ |
| `push` | flag | Push report to Feishu (cron enables by default) | ❌ |
| `no-push` | flag | Suppress Feishu push in cron mode | ❌ |
| `weekly` | flag | Weekly report variant (use with `--cron`) | ❌ |
| `backtest` | flag | Also run 10-year backtest simulation | ❌ |
| `show-selection` | flag | Print resolved selection payload and exit | ❌ |
| `skip-map` | flag | Skip market map PNG generation | ❌ |
| `stop-on-error` | flag | Halt immediately if a pipeline step fails | ❌ |
| `volume` | flag | Enrich snapshot with volume ratio (vs 20-day avg) | ❌ |
| `flow` | flag | Enrich snapshot with capital flow data (10jqka, CN only) | ❌ |
| `backtest` | flag | Also run 10-year backtest simulation | ❌ |
| `show-selection` | flag | Print resolved selection payload and exit | ❌ |
| `skip-map` | flag | Skip market map PNG generation | ❌ |
| `stop-on-error` | flag | Halt immediately if a pipeline step fails | ❌ |

**Rules**:
- Exactly one selector (mutually exclusive group)
- `--cron` cannot combine with selectors
- `region` must be `cn` or `us`

---

## Standard Workflow: Select → Analyze → Output

```
┌─────────────────────────────────────────────────────────────┐
│  STEP 1 — Select (resolve symbols via selector)             │
│  STEP 2 — Analyze (S1 Species → S2 Situation → S3 → S4)   │
│  STEP 3 — Output (adhoc: manifest; cron: report + PNG)       │
└─────────────────────────────────────────────────────────────┘
```

### Step 1 — Select

Selector resolves to symbol list:

| Selector | Source |
|----------|--------|
| `--symbol` | Single symbol (e.g. `159263`) |
| `--symbols` | Comma-separated basket |
| `--active` | `config/states/active.json` |
| `--watchlist` | `config/states/watchlist.json` |
| `--void` | `config/states/void.json` |

### Step 2 — Analyze

Default (signals only, no backtest):

```
S1 Species   → Classify asset (STEADY, VOLATILE, MOMENTUM, BOND)
S2 Situation → Compute indicators (LDev, ZScore, RSI, MA250, MA60)
   Pulse     → Statistical classification (EXTREME_OB ~ EXTREME_OS)
S3 System    → Inject macro context (yield_pctile, vix_pctile)
S4 Strategy  → Evaluate tactic rules → strategy-defined signal (TREND_ENTRY, etc.)
   Alignment → Cross-product Pulse × Signal (CONFIRMED/DIVERGENT/NEUTRAL)
             → Per-row signal classification (signal_type, sim_action)
```

> **Note**: The "Advice" column is **scenario-based** — "若持仓: ...；若空仓: ...".
> No position data is read. Pure Layer 2 stateless output.

With `--backtest` flag, also runs:

```
S4 Backtest → 10-year simulation from pre-classified signals
            → Strategy_Ret, BuyHold_Ret, Sharpe, DD
```

### Step 3 — Output

| Mode | Output |
|------|--------|
| **adhoc** | Write selection manifest to `adhoc/`; return evidence for inspection |
| **cron** | Generate market map PNG; push Markdown report to Feishu |

---

## Output Contract

| Artifact | Path | When | Format |
|----------|------|------|--------|
| Selection manifest | `adhoc/{date}_{region}_{selector}_selection.json` | adhoc | JSON |
| Signal snapshot | `logs/snapshots/{date}_{region}.json` | cron | JSON |
| Market map PNG | `logs/reports/{date}_market_map_{region}.png` | cron | PNG |
| Markdown report | `logs/reports/{date}_{type}_{region}_report.md` | cron | Markdown |

**Signal Snapshot JSON Schema** (default, no backtest):
```json
{
  "date": "2026-05-04",
  "region": "cn",
  "selector": "active",
  "assets": [
    {
      "symbol": "159263",
      "name": "ETF Name",
      "species": "CN_ETF",
      "situation": "pullback",
      "system": "neutral",
      "strategy": {
        "ldev": -0.05,
        "zscore": -1.2,
        "rsi": 45.3,
        "signal": "neutral"
      }
    }
  ]
}
```

**Note**: Backtest fields (`strategy_ret`, `buyhold_ret`, `strat_sharpe`, etc.) are only present in the snapshot when `--backtest` was used. Daily cron snapshots are signals-only.

When `--fund-flow` is enabled, each asset also receives:
- `volume_note`: string with volume ratio + emoji + interpretive hint (e.g. `🔥 3.0x放量 | 🟢主力净流入 +5,230,000；趋势确认，主力建仓`)
- (CN only, during trading hours): fund flow data appended to note

---

## Error Handling

| Error | Response | Action |
|-------|----------|--------|
| Unknown symbol | Skip with warning | Continue with valid symbols |
| Missing data file | Flag in output | Use stub values |
| Selector + --cron | Reject command | "Cannot use selectors with --cron" |
| Unsupported region | Reject command | "Region must be cn or us" |
| No selection resolved | Return empty | "No symbols match selector" |

---

## Format Specifications

### Feishu Markdown Report

```markdown
# 7S Analysis Report — {region.upper()} — {date}

## Summary
- Total assets analyzed: {count}
- Signals: {bullish} bullish, {bearish} bearish, {neutral} neutral

## Asset Signals

| Symbol | Name | Species | Situation | Signal | LDev | Z-Score |
|--------|------|----------|-----------|--------|------|---------|
| 159263 | ETF | CN_ETF | pullback | neutral | -0.05 | -1.2 |

## Market Map
![Market Map](market_map_{date}.png)
```

### Signal Values

| Field | Values |
|-------|--------|
| `species` | CN_ETF, US_ETF, CN_INDEX, CN_OTC, HK_ETF, MACRO |
| `situation` | breakout, pullback, sideways, unknown |
| `system` | bullish, bearish, neutral (stub) |
| `signal` | strong_buy, buy, neutral, sell, strong_sell |

---

<!-- ─────────────────────────────────────────────────────────────── -->
<!-- machine section — technical reference, not for human reading   -->
<!-- ─────────────────────────────────────────────────────────────── -->

<!--
INVOKER NOTE:
  - Exactly one selector required (--symbol | --symbols | --active | --watchlist | --void)
  - --cron mode: no selectors; scans all assets in region
  - --weekly: weekly summary instead of daily
  - adhoc mode: writes manifest to adhoc/; does NOT push to Feishu
-->

## Invocation

```bash
# Signals only (default — fast, no backtest)
python3 skills/analyze/scripts/analyze.py --region cn --symbol 159263
python3 skills/analyze/scripts/analyze.py --region cn --symbols 159263,159201,512880
python3 skills/analyze/scripts/analyze.py --region cn --active
python3 skills/analyze/scripts/analyze.py --region us --watchlist
python3 skills/analyze/scripts/analyze.py --region cn --void

# Signals + 10-year backtest (adhoc deep-dive)
python3 skills/analyze/scripts/analyze.py --region cn --symbol 159263 --backtest
python3 skills/analyze/scripts/analyze.py --region cn --active --backtest

# Cron (signals only, generates PNG + pushes Markdown report to Feishu)
python3 skills/analyze/scripts/analyze.py --region cn --cron
python3 skills/analyze/scripts/analyze.py --region us --cron
python3 skills/analyze/scripts/analyze.py --region cn --cron --weekly

# Adhoc with push suppression / skip map
python3 skills/analyze/scripts/analyze.py --region cn --cron --no-push
python3 skills/analyze/scripts/analyze.py --region cn --cron --skip-map

# Adhoc with volume ratio / capital flow
python3 skills/analyze/scripts/analyze.py --region cn --symbol 159307 --volume
python3 skills/analyze/scripts/analyze.py --region cn --active --volume --flow
python3 skills/analyze/scripts/analyze.py --region us --active --volume

# Debug: show resolved selection payload
python3 skills/analyze/scripts/analyze.py --region cn --active --show-selection

<!-- ─────────────────────────────────────────────────────────────── -->
<!-- end machine section                                             -->
<!-- ─────────────────────────────────────────────────────────────── -->

---

## Architecture

```
analyze.py (ORCHESTRATOR — parses args, routes, aggregates)
  │
  ├── species.py (S1: asset type classification)
  │     CN_ETF, US_ETF, CN_INDEX, CN_OTC, HK_ETF, MACRO
  │
  ├── situation.py (S2: market status)
  │     breakout, pullback, sideways, unknown
  │
  ├── system.py (S3: market regime — STUB, returns neutral)
  │
  ├── s4_strategy/
  │     ├── pipeline.py      — run_strategy_pipeline (backtest) / run_analyze_pipeline (signals only)
  │     ├── signal_computer.py — compute_profile (indicator columns per profile)
  │     ├── tactic.py        — apply_tactic (rule evaluation per tactic YAML)
  │     ├── registry.py      — StrategyRegistry (loads profile+tactic from routing)
  │     ├── alignment.py     — classify_alignment (Pulse × Signal cross-product)
  │     ├── s3_context.py    — inject_s3_context (yield_pctile, vix_pctile columns)
  │     └── engine.py        — StrategyEngine (transitional: STEADY/VOLATILE/MOMENTUM old path)
  │
  ├── run_strategy.py (CLI — signals by default, --backtest for adhoc deep-dive)
  │
  └── (backtest standalone: see skills/backtest/ SKILL.md)
```

### S1-S4 Layer Definitions (Bilingual)

| Layer | EN | 中文 | Question | Output |
|-------|----|------|----------|--------|
| **S1** | Species | 资产分类 | "What type?" | STEADY, VOLATILE, MOMENTUM, BOND |
| **S2** | Situation | 统计指标 | "What's happening?" | LDev(σ), ZScore, RSI, MA250, MA60 |
| **—** | Pulse | 脉冲 | "How extreme?" | EXTREME_OB, OVERBOUGHT, STRONG, NEUTRAL, WEAK, OVERSOLD, EXTREME_OS |
| **S3** | System | 宏观环境 | "What's the regime?" | yield_pctile, vix_pctile (injected as data columns) |
| **S4** | Strategy | 策略信号 | "What does the strategy say?" | strategy-defined signal per rule (TREND_ENTRY, etc.) |
| **—** | Alignment | 一致性 | "Do Pulse and Signal agree?" | CONFIRMED, DIVERGENT, NEUTRAL |
| **E3** | Execution | 执行验证 | "Can I execute it?" | EXECUTED, SKIPPED (account_engine) |

---

## Responsibilities

- S1-S4 evidence generation (S3 returns neutral/stub)
- Signal computation: LDev, Z-Score, RSI per asset class
- Snapshot production for downstream (view_report, position)
- Does NOT make allocation decisions (that's Position's job)

---

## Evidence Requirements

Cite in every response:
```
来源：
- 资产选择：config/states/{active,watchlist,void}.json
- 价格数据：knowledge/<region>/3_processed/<symbol>.csv
- 信号计算：skills/analyze/scripts/ (S1-S4)
- 快照时间：{timestamp}
```
