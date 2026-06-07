---
name: Update Position
description: "Refresh prices and apply broker trades to position snapshots. Execute layer for 7S: refreshes position data from knowledge CSVs and applies manual trade adjustments."
read_when:
  - User asks to refresh prices or update positions
  - User asks to apply broker trades
  - User asks to export positions to CSV
  - User asks to check plan vs position integrity
allowed-tools:
  Bash(python:skills/update_position/scripts/update_position.py)
---

<!--
 soul: SOUL.md § Core standards
   "Evidence before claims"          → always show what changed before asking for approval
   "Keep the human in control"        → every apply is gated behind a confirmation step
   "Readable workflows over hidden coupling" → two-step pattern: preview then confirm

 identity: IDENTITY.md § Scope
   "signal synthesis and reporting artifacts" → position snapshots are the evidence layer

 user: USER.md § Approval boundary
   "Ask before changing: position composition" → apply trades requires explicit approval
-->

# Update Position

> **Role in 7S**: The *execute* layer of the position workflow. Produces refreshed position snapshots — the evidence layer that feeds into Stake decisions.
> **Audience**: Human plan manager. 7S never auto-pushes changes; every trade application requires your explicit approval.

---

## Human Triggers

**Keyword**: `position`

**When to trigger**: Human wants to execute trade cycle — refresh prices, export for editing, or apply confirmed trades.

**Natural Language** (exclusive territory — execute, prices, export, apply. NOT signals, NOT decisions):

| Intent | What to say |
|--------|-------------|
| Refresh prices | *"刷新 A 股仓位价格"* / *"更新最新持仓"* |
| Export to CSV | *"导出一份持仓表格"* / *"导出 us_hb 的持仓"* |
| Apply trades | *"执行这笔调仓"* / *"应用这笔交易"* |
| Check integrity | *"核对计划和持仓是否一致"* |

**Cross-skill routing**:
- Want **raw signals**? → use `analyze`: *"分析今天的市场"*
- Want **drift decision**? → use `decide`: *"cn_hb 需要调仓吗"*
- `position` is execute-only — 7S never auto-applies; requires explicit human approval

---

## Standard Workflow: Export → Edit → Apply

Every manual trade cycle follows three steps. **7S never skips steps 1 or 2.**

```
┌─────────────────────────────────────────────────────────────┐
│  STEP 1 — Export (your request → 7S generates CSV)         │
│  STEP 2 — You edit the CSV (spreadsheet, any editor)       │
│  STEP 3 — Apply (7S: preview → ask approval → update)      │
└─────────────────────────────────────────────────────────────┘
```

### Step 1 — Export

Ask 7S to export your current positions.

**Trigger**: *"Export my CN/US plan positions"* / *"导出现有持仓"*

**What happens**:
- 7S reads today's position snapshot
- 7S writes a CSV to `logs/positions/apply_trade.csv`
- 7S tells you where the file is and what to edit

### Step 2 — You Edit

Open `logs/positions/apply_trade.csv` in any spreadsheet editor.

| What you change | What 7S does |
|-----------------|--------------|
| Edit `new_shares` | Updates that asset's share count |
| Leave `new_shares` blank | No change to that asset |
| Set `new_shares = 0` | Removes the asset from the snapshot |
| Delete a row | Removes that asset from the snapshot |
| Add a new row | Adds that asset to the snapshot |

### Step 3 — Apply (with Approval Gate)

**Trigger**: *"Apply these trades"* / *"执行调仓"*

**Before applying, 7S will always:**
1. Run a **dry-run preview** — show exactly what will change
2. **Ask for your approval** — *"This will update X positions. Confirm?"*
3. Only then — write to snapshots and archive the CSV

**Approval gate**:
```
7S: Dry-run complete. Changes:
  - SPYM: 20 → 25 shares (+5)
  - GLDM: 30 → 0 shares (remove)

  Proceed to apply? (yes/no)
```

---

<!-- ─────────────────────────────────────────────────────────────── -->
<!-- machine section — technical reference, not for human reading   -->
<!-- ─────────────────────────────────────────────────────────────── -->

<!--
INVOKER NOTE:
  - Always run `export` first, never skip the CSV step.
  - Always run `apply --dry-run` before `apply` without flags.
  - Never skip the human approval gate before confirming `apply`.
  - Archive happens automatically after successful apply.
-->

## Entry Point

```
update_position.py <command>
```

## Commands

| Command | Responsibility | When to use |
|---------|---------------|-------------|
| `refresh` | Fetch latest prices | Daily price refresh |
| `apply` | Apply trades from CSV | After manual editing |
| `export` | Export positions to CSV | Prepare for manual editing |
| `check` | Verify plan vs position | Integrity check |

## Invocation

```bash
# Refresh prices for specific plans
python3 skills/update_position/scripts/update_position.py refresh --plan cn_hb us_hb

# Refresh all plans
python3 skills/update_position/scripts/update_position.py refresh --plan cn_hb us_hb

# Apply trades from CSV (edits positions, archives CSV)
python3 skills/update_position/scripts/update_position.py apply --csv logs/positions/apply_trade.csv

# Dry run (preview only, no changes)
python3 skills/update_position/scripts/update_position.py apply --csv logs/positions/apply_trade.csv --dry-run

# Export positions to CSV
python3 skills/update_position/scripts/update_position.py export --plan cn_hb us_hb

# Check integrity
python3 skills/update_position/scripts/update_position.py check --plan cn_hb
python3 skills/update_position/scripts/update_position.py check --plan cn_hb --version 1
python3 skills/update_position/scripts/update_position.py check --all
```

## Data Sources

| Data | Source |
|------|--------|
| Plan metadata | `config/plans/<plan_id>/v<ver>.json` |
| Position snapshots | `logs/positions/<plan_id>/<date>.json` |
| Prices | `knowledge/{region}/3_processed/*.csv` (via data-daily-update) |

## Price Fallback Logic

`refresh` uses the following priority to fetch prices:

1. **close column** (3rd column) - preferred, used for market value calculation
2. **total_return column** (2nd column) - fallback when close is unavailable
3. **Re-run daily_update** - automatic retry once when both close and total_return are unavailable
4. **Report failure** - record to `failed` field if still unavailable

Return values include:
- `updated`: number of assets successfully updated
- `prices`: dict of symbol → price
- `failed`: symbols without available price
- `daily_update_retried`: whether daily_update was re-run

## apply_trade — Apply Broker Trades

Uses `apply_trades.py` to apply trade instructions from CSV to position snapshots.

### CSV Format

`logs/positions/apply_trade.csv`:
```csv
plan_id,symbol,name,current_shares,new_shares
us_hb,SPYM,S&P 500,20,25
us_hb,TLT,Treasury Bond,50,50
us_hb,GLDM,Gold,30,
```

| Column | Description |
|--------|-------------|
| `plan_id` | Plan identifier (e.g. `us_hb`, `cn_hb`) |
| `symbol` | Ticker symbol |
| `name` | Asset name |
| `current_shares` | Current share count (decorative; not validated) |
| `new_shares` | Target share count. Blank = no change. Set to 0 to remove. |

### Usage

```bash
# Export current positions to CSV for manual editing
python3 skills/update_position/scripts/update_position.py export --plan cn_hb us_hb

# Preview trades (dry run — no file changes, no archive)
python3 skills/update_position/scripts/update_position.py apply --csv logs/positions/apply_trade.csv --dry-run

# Apply trades (updates snapshots, archives CSV to archive/apply_trade_YYYY-MM-DD.csv)
python3 skills/update_position/scripts/update_position.py apply --csv logs/positions/apply_trade.csv
```

### Behavior

| Action | Condition | Result |
|--------|-----------|--------|
| **Update** | `new_shares` != `current_shares` | Updates position snapshot |
| **Skip** | `new_shares` == `current_shares` or blank | No change |
| **Delete** | Symbol in position but NOT in CSV | Symbol removed from snapshot |
| **Add** | Symbol in CSV but not in position | Adds to snapshot with `shares=new_shares` |

- **Archive**: After successful apply, CSV is moved to `logs/positions/archive/apply_trade_<date>.csv`
- **Dry run**: Validates and returns preview; does NOT save or archive

### Notes

- `current_shares` is decorative — the code always reads actual position from JSON file
- Blank `new_shares` means "no change" (skip), NOT delete
- Non-existent plan_id → creates new empty position and adds symbol

## Architecture

```
update_position.py (ORCHESTRATOR)
  ├── refresh → refresh_prices.py (fetch prices from knowledge CSVs)
  ├── apply   → apply_trades.py (apply CSV trades to snapshots)
  ├── export  → apply_trades.py (export snapshots to CSV)
  └── check   → integrity check (Plan vs Position)
```

## Evidence Requirements

```
来源：
- 计划配置：config/plans/<plan_id>/v<ver>.json
- 持仓快照：logs/positions/<plan_id>/<date>.json
- 价格数据：knowledge/{region}/3_processed/*.csv (via data-daily-update)
```
