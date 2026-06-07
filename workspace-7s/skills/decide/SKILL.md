---
name: Decide
description: "Use when the user asks for target weights, sizing, allocation, stake review, or rebalance framing inside 7S. This is the S5 (self-portrait) + S6 (Stake) decision layer."
read_when:
  - User asks to review a position plan (e.g. "cn_hb needs rebalance?", "show cn_hb positions")
  - User asks for target weights, allocation, or rebalance framing
  - User asks to create, view, update, or delete a plan
  - User asks to check consistency between plan and positions
  - User mentions "cn_hb", "us_hb", "cn_anti_lost_30", "us_anti_inflation" in a position context
allowed-tools:
  Bash(python:skills/decide/scripts/decide.py)
---

<!--
 soul: SOUL.md § Core standards
   "Evidence before claims"          → always cite plan + position files before recommending
   "Keep the human in control"        → push to Feishu is automatic; user reviews and approves trades
   "Readable workflows over hidden coupling" → stdout is logging only; Feishu file is authoritative

 identity: IDENTITY.md § Scope
   "S5 + S6 decision layer" → orchestrates plan constraints + drift computation

 user: USER.md § Approval boundary
   "Ask before executing trades" → Feishu push is report only; no auto-trade execution
-->

# Decide

S5 (self-portrait) + S6 (Stake) decision layer.

---

## Human Triggers

**Keyword**: `decide`

**When to trigger**: Human wants drift assessment and rebalancing recommendation for a specific plan.

**Natural Language** (exclusive territory — drift, rebalancing, plan config. NOT signals, NOT history):

| Intent | What to say |
|--------|-------------|
| Drift check | *"cn_hb 需要调仓吗"* / *"us_hb 漂移情况"* / *"要不要调仓"* |
| Snapshot at date | *"cn_hb 2026-05-01 的漂移"* |
| View plan config | *"看看 cn_hb 的计划配置"* |
| Create plan | *"新建计划 xxx，配置如下：..."* |
| Update plan | *"更新 cn_hb 的计划..."* |
| Self-portrait | *"查看 cn_hb 持仓现状"* / *"cn_hb 当前仓位"* |
| Performance review | → use `review`: *"看看 cn_hb 的历史收益"* |

**Cross-skill routing**:
- Want **raw signals**? → use `analyze`: *"分析今天的市场信号"*
- Want **historical metrics**? → use `review`: *"看看历史业绩"*
- Want **execute trade**? → use `position`: *"执行这笔调仓"*
- `decide` is orchestration only — S5 (plan config) + S6 (drift) + holdings concentration + Feishu push
- Concentration analysis (`--concentration`) is always enabled by default for every decide run

---

## Standard Workflow: Review → Recommend → Push

```
┌─────────────────────────────────────────────────────────────┐
│  STEP 1 — Review (你触发 → 7S 加载持仓快照)                    │
│  STEP 2 — Recommend (7S 计算漂移 → 生成买卖建议)              │
│  STEP 3 — Push (7S 保存文件 → 自动推送飞书)                    │
└─────────────────────────────────────────────────────────────┘
```

### Step 1 — Review

**Trigger**: *"帮我看看 cn_hb 的最新情况"*

**What happens**:
- 7S reads plan config: `config/plans/<plan_id>/v<ver>.json`
- 7S reads today's position snapshot: `logs/positions/<date>_{plan_id}.json`
- 7S reads prices from `knowledge/<region>/3_processed/*.csv`
- 7S fetches ETF holdings (cached, auto-refreshed) for concentration analysis

### Step 2 — Recommend

**What happens**:
- Compute current weight = market_value / total_market_value
- Compute drift = current_weight − target_weight
- If |drift| > threshold → generate buy/sell recommendation
- Format output as Markdown report

### Step 3 — Push

**What happens**:
- Save report to `logs/decisions/<date>_<plan_id>_decide.md`
- Push file attachment to Feishu (if `SEVENS_FEISHU_CHAT_ID` is set)
- Print to stdout for logging only

**Your role**: Review the Feishu message and approve/execute trades yourself.

---

## Entry Point

```
decide.py <command>
```

## Commands

| Command | Responsibility | When to use |
|---------|---------------|-------------|
| `decide` | **Full workflow**: S5 + S6 + output | Primary entry point |
| `self-portrait` | S5 — Plan CRUD | Admin: create/update/delete plans |
| `stake` | S6 — Compute drift | Debug: drift calculation only |
| `performance` | (Moved to **Review Plan** skill) | 历史业绩和调仓对比 |

## Human-friendly usage

直接给机器人发消息即可：

### 查看某个计划是否需要调仓（自动推送飞书）
```
帮我看看 cn_hb 的最新情况
```
→ 自动加载今日持仓，计算漂移，输出买卖建议，推送飞书

### 指定日期持仓快照
```
cn_hb 2026-05-01 的情况
```

### 查看/管理计划
```
看看 cn_hb 的计划配置
```
```
新建一个计划叫 xxx，配置如下：...
```
```
更新 cn_hb 的计划...
```

<!-- ─────────────────────────────────────────────────────────────── -->
<!-- machine section — technical reference, not for human reading   -->
<!-- ─────────────────────────────────────────────────────────────── -->

<!--
INVOKER NOTE:
  - decide: auto-saves to outputs/ + pushes to Feishu; DO NOT reformat stdout
  - self-portrait: plan CRUD; list → show → create → update → delete
  - stake: drift computation only; for debugging drift calculation
  - performance: MOVED to Review Plan skill
-->

## Invocation

```bash
# ── Primary: Full decision workflow (auto-saves to outputs/ and pushes to Feishu) ──
python3 skills/decide/scripts/decide.py decide --plan cn_hb --concentration
python3 skills/decide/scripts/decide.py decide --plan cn_hb --date 2026-05-01 --concentration

# With specific plan version
python3 skills/decide/scripts/decide.py decide --plan cn_hb --version 1 --concentration

# ── Refresh holdings cache (run on server after deploy) ───────────────────
python3 skills/decide/scripts/holdings/refresh_all.py
# decide --concentration never makes live API calls; refresh is separate

# Output is auto-saved to: logs/decisions/<date>_<plan_id>_decide.md
# File is automatically pushed to Feishu if SEVENS_FEISHU_CHAT_ID is set

# ── Historical performance: use Review Plan skill instead ────────────────────
# python3 skills/review_plan/scripts/review_plan.py --plan cn_hb --region CN --holdings <path>.json

# ── With explicit output path ────────────────────────────────────────────────
python3 skills/decide/scripts/decide.py decide --plan cn_hb --json
python3 skills/decide/scripts/decide.py decide --plan cn_hb --output /path/to/file.md

# ── With holdings concentration analysis ────────────────────────
python3 skills/decide/scripts/decide.py decide --plan us_anti_inflation --concentration
# Cross-references top-10 holdings across all ETFs in the plan.
# Flags any single stock exceeding 5% combined portfolio weight.

# ── S5: Plan CRUD ─────────────────────────────────────────────────────────────
python3 skills/decide/scripts/decide.py self-portrait list
python3 skills/decide/scripts/decide.py self-portrait show --plan-id cn_hb
python3 skills/decide/scripts/decide.py self-portrait create --plan-id new_plan --json-input '{...}'
python3 skills/decide/scripts/decide.py self-portrait update --plan-id cn_hb --json-input '{...}'
python3 skills/decide/scripts/decide.py self-portrait delete --plan-id cn_hb --force

# ── S6: Drift only ───────────────────────────────────────────────────────────
python3 skills/decide/scripts/decide.py stake --plan cn_hb
```

<!-- ─────────────────────────────────────────────────────────────── -->
<!-- end machine section                                             -->
<!-- ─────────────────────────────────────────────────────────────── -->

## Output Behavior

When running `decide` command without `--output`:
1. Result is saved to `logs/decisions/<date>_<plan_id>_decide.md`
2. **File is automatically pushed to Feishu (via `openclaw message send`) if `SEVENS_FEISHU_CHAT_ID` env var is set**
3. Result is printed to stdout for logging purposes only

### Agent Instruction (CRITICAL)

**After running `decide`, do NOT reformat or summarize the stdout markdown.** The script pushes the file to Feishu directly.

- **On success**: Reply with a brief acknowledgment, e.g. "✅ cn_hb decide 报告已推送至飞书群"
- **On failure** (error in stderr about missing env var): Reply with the error so it's visible
- **DO NOT**: Paste or reformat the markdown table into your reply
- **DO NOT**: Regenerate a summary table that omits columns (especially Build Progress and Funding Gap)

**Always include `--concentration`** on every decide run (unless CN-only plan where holdings data is unavailable). Concentration analysis is cache-only — it never makes live API calls. The cache is pre-populated via `holdings/refresh_all.py` on the server.

The Feishu file attachment is the authoritative deliverable. The stdout print is only for your reference.

## Architecture

```
decide.py (ORCHESTRATOR — routes & formats, NO domain logic)
  │
  ├── decide --plan cn_hb [--concentration]
  │   ├── Load Plan metadata (region, currency, target_mv)
  │   ├── Call stake.py --format json → get drift + recommendations
  │   ├── [optional] holdings/overlap.py → concentration analysis
  │   └── Format output + push to Feishu
  │
  ├── self-portrait ... → self_portrait.py (S5: Plan CRUD)
  └── stake ... → stake.py (S6: drift computation)
```

### apply_trade.csv 生成规则

不自动生成。通过 `update_position export` 导出后用户编辑再 apply。

stake.py (S6 DOMAIN — pure logic)
  ├── compute_drift()
  ├── recommend_action()
  └── Outputs: JSON/Markdown with buy/hold/sell recommendations

holdings/ (CONCENTRATION)
  ├── fetcher.py       → cache-first holdings fetch (yfinance → tiingo → finnhub)
  ├── overlap.py       → cross-reference engine, combined weight computation
  └── refresh_all.py   → CLI entry point to refresh cache

### S5 vs S6 vs Decide

| Layer | Question | Responsibility |
|-------|----------|----------------|
| **S5** | "What do I WANT?" | Define investor constraints, target weights |
| **S6** | "How do I GET there?" | Compute drift, determine action |
| **Decide** | "What do I DECIDE?" | Orchestrate S5 + S6 + format output |

## Domain Model

**Plan** (SSOT):
```
plan_id, version, region, currency, target_market_value,
all_assets: [{symbol, target_weight, sleeve, role}],
constraints: {drift_threshold, max_weight, min_weight}
```

**Position** (daily snapshot):
```
plan_id, plan_version, snapshot_date,
positions: [{symbol, shares, current_price, market_value}]
```

**Drift** (computed):
```
current_weight = market_value / total_market_value
drift = current_weight − target_weight
|drift| > threshold → rebalance
```

## Data Sources

| Data | Source |
|------|--------|
| Plan SSOT | `config/plans/<plan_id>/v<ver>.json` |
| Position snapshot | `logs/positions/<date>_{plan_id}.json` |
| Prices | `knowledge/{region}/3_processed/*.csv` (via data-daily-update) |
| ETF holdings | `data/holdings/{symbol}.csv` (cached, 30-day TTL, auto-refreshed via yfinance) |

## Evidence Requirements

```
来源：
- 计划配置：config/plans/<plan_id>/v<ver>.json
- 持仓快照：logs/positions/<date>_{plan_id}.json
- 价格数据：knowledge/{region}/3_processed/*.csv (via data-daily-update)
```
