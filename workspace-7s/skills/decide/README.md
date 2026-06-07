# Decide

Decide is the **orchestrator** for S5 (self-portrait) + S6 (stake) decision layer for `workspace-7s`.

It routes commands to domain scripts and formats outputs — **no domain logic**.

## Source Of Truth

- runtime contract: `SKILL.md`

## Stable Entry Points

All commands go through `decide.py`:

```bash
# S5 Self-portrait (Plan CRUD)
python3 skills/decide/scripts/decide.py self-portrait list
python3 skills/decide/scripts/decide.py self-portrait show --plan-id cn_hb
python3 skills/decide/scripts/decide.py self-portrait create --plan-id cn_hb --json-input '{...}'

# S6 Stake (drift → buy/hold/sell)
python3 skills/decide/scripts/decide.py stake --plan cn_hb
python3 skills/decide/scripts/decide.py stake --plan cn_hb --date 2026-05-01 --format json

# Performance + Rebalancing Comparison
python3 skills/decide/scripts/decide.py performance --plan cn_hb --region CN --holdings <path>.json
```

**Update positions** is handled by a separate skill: `skills/update_position/SKILL.md`

## Domain Model

**Plan** (SSOT, stored in `config/plans/<plan_id>/v<ver>.json`):
```
plan_id, version, region, currency, target_market_value,
all_assets: [{symbol, target_weight, sleeve, role, preferred}],
constraints: {drift_threshold, max_weight, min_weight}
```

**Position** (daily snapshot, stored in `logs/positions/<date>_{plan_id}.json`):
```
plan_id, plan_version, snapshot_date,
total_market_value (derived),
positions: [{symbol, shares, current_price, market_value}]
```

**Drift formula (computed, not stored):**
```
position_weight = market_value / total_market_value
drift = position_weight − target_weight
→ |drift| > drift_threshold → rebalance
```
