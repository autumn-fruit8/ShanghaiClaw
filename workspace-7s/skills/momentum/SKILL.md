---
name: Momentum
description: "Momentum rotation — rank assets by momentum, dual-threshold BUY/HOLD/SELL signals, multi-period composite, rotation simulation with equity curve. Layer-2 Decision Skill."
read_when:
  - User asks to rank assets by momentum
  - User mentions "动量轮动", "动量排名", "哪个最强"
  - User wants rotation strategy simulation
  - User wants to backtest momentum rotation (NOT single-asset backtest — see backtest skill)
allowed-tools: Bash(python:skills/momentum/scripts/run_momentum.py)
---

# Momentum Rotation

> **Role in 7S**: Layer-2 Decision Skill — rank, signal, simulate.
> **Output**: Markdown table + chart PNG + decision text.

---

## Human Triggers

**Keyword**: `momentum`

| Intent | What to say |
|--------|-------------|
| Scan active | *"动量扫描"* / *"动量排名"* |
| Scan custom basket | *"动量轮动 159259,512050,159263,159207"* |
| Scan multi-period | *"多周期动量扫描"* |
| Rotation simulation | *"动量轮动模拟"* → `--active --rotate --reb-period 20 --top-n 2` |
| Rotation on basket | *"模拟动量轮动 159259,512050 每30天调仓 选3个"* → `--symbol X,Y --rotate --reb-period 30 --top-n 3` |

**Cross-skill routing**:
- Want **single-asset backtest**? → use `backtest`
- Want **momentum rotation**? → use `momentum`
- Want **drift decision**? → use `decide`

---

## Input Contract

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--symbol` | str | — | Symbol or comma-separated basket |
| `--active` | flag | False | Scan active holdings |
| `--watchlist` | flag | False | Scan watchlist |
| `--void` | flag | False | Scan void assets |
| `--region` | str | `all` | Region filter |
| `--method` | str | `simple` | `simple`, `slope`, `composite`, `multi` (weighted 20/60/120d) |
| `--period` | int | 20 | Base lookback (trading days) |
| `--buy-th` | float | +0.05 | BUY threshold (score above) |
| `--sell-th` | float | -0.02 | SELL threshold (score below) |
| `--rotate` | flag | False | Rotation simulation |
| `--reb-period` | int | 20 | Rebalance frequency (trading days, rotate mode) |
| `--top-n` | int | 2 | Top-N assets to hold (rotate mode) |
| `--ttm` | int | — | Trailing N trading days only (rotate mode, default const=252=1yr) |
| `--spread-th` | float | 0.0 | Spread filter: hold both when gap < threshold (0=off) |
| `--slow-confirm` | flag | False | Require 20d & 60d alignment |
| `--vol-cap` | float | 0.0 | Vol cap: equal-weight when vol exceeds (0=off) |

---

## Output

| Mode | Table | Chart | Path |
|------|-------|-------|------|
| Scan | Rank + score + signal | Summary bar + per-asset price/score | `adhoc/momentum/{date}_scan_momentum.png` |
| Rotate | Return + trades + holdings | Log price + trade markers + equity curve | `adhoc/momentum/{date}_rotate_momentum.png` |

---

## Invocation

```bash
# Scan
python3 skills/momentum/scripts/run_momentum.py --active
python3 skills/momentum/scripts/run_momentum.py --symbol 159259,512050,159263,159207
python3 skills/momentum/scripts/run_momentum.py --active --method multi

# Rotate simulation
python3 skills/momentum/scripts/run_momentum.py --active --rotate
python3 skills/momentum/scripts/run_momentum.py --symbol A,B,C --rotate --reb-period 20 --top-n 2
```
