# Logs — Runtime Artifacts

Persistent baseline output from the 7S engine (cron universe). Adhoc runs write to `adhoc/` instead.

## Structure

```
logs/
├── backtest/       # Backtest results (JSON reports + charts per date)
├── snapshots/      # Daily system snapshots (JSON: state, signals, decisions)
├── decisions/      # Decision outputs per plan (markdown)
├── review/         # Review outputs per plan (charts)
├── reports/        # Daily/weekly reports (markdown + PDF + market maps)
├── positions/      # Position snapshots per plan per date (JSON)
├── holdings/       # Holdings CSV snapshots from broker/external sources
└── tests/          # Test output artifacts (charts, CSVs)
```

## Convention

- **Cron runs** → write to `logs/` (persistent, permanent)
- **Adhoc runs** → write to `adhoc/{date}_{region}_{selection}/` (temporary, isolated)
- Stale adhoc runs (older than 2 weeks) may be cleaned up
- Position snapshots in `positions/` are the SSOT for holdings
