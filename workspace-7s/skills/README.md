# Skills — workspace-7s

Domain modules for the 7S system. Each skill owns a specific business capability.

## Skills Index

| Skill | Domain | Entry Point |
|-------|--------|-------------|
| `analyze/` | 4S evidence pipeline (Species→Situation→System→Strategy) | `scripts/analyze.py` |
| `decide/` | S5 (self-portrait) + S6 (stake) decision layer | `scripts/decide.py` |
| `update_position/` | Position refresh, price updates, trade application | `scripts/update_position.py` |
| `review_plan/` | Plan review, drift analysis, performance checks | `scripts/review_plan.py` |
| `data-daily-update/` | Daily market data ingestion (CN + US) | `scripts/run_daily_update.py` |
| `data-calibration/` | Data calibration and backfill | `scripts/run_calibration.py` |
| `backtest/` | Historical backtest simulation | `scripts/run_backtest.py` |
| `momentum/` | Momentum factor ranking and scoring | `scripts/run_momentum.py` |
| `logarithm/` | Log-scale chart generation | `scripts/draw_log_chart.py` |
| `view_report/` | Report generation and Feishu push | `scripts/run_report.py` |
| `evolve/` | Self-evolution: outcome review and process improvement | `scripts/evolve.py` |

## Structure

Each skill follows the same layout:

```
skill-name/
├── SKILL.md              # Human-readable contract + agent invocation reference
├── __init__.py
├── scripts/              # Domain logic
│   ├── *.py
│   └── __init__.py
└── config/               # Optional: skill-specific config
```

## Convention

- Skills own **domain logic only** — no workflow routing
- Public entry points live in `scripts/` and are called from workspace root
- Each `SKILL.md` is the SSOT for invocation contracts
- See `AGENTS.md` for the mandatory SKILL.md sync rule
