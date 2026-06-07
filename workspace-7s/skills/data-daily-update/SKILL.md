---
name: Jarvis Daily Update
description: Incrementally update CN or US asset price history via API (akshare for CN, yfinance for US). Appends new daily rows to existing CSVs in 3_processed/. Calibration is only needed once on first setup to seed the initial history — it is NOT a precondition for routine daily updates.
read_when:
  - User asks for a daily market update
  - User asks to fetch the latest prices or update price data
  - User asks what today's prices or returns are
  - Running the scheduled daily update routine
  - User says "update CN data", "update US data", or "fetch today's prices"
allowed-tools: Bash(python:skills/data-daily-update/scripts/run_daily_update.py)
---

# Daily Update Skill

## Input Contract

Expected inputs:

- target region: `cn`, `us`, or `all`
- existing processed CSV files under `knowledge/{region}/3_processed/`
- configured data providers for the selected asset universe

Calibration is only needed once to seed missing histories. Routine daily updates assume the CSVs already exist.

## Overview

Fetches incremental price data from external APIs and appends new rows to the price history CSVs in `knowledge/{region}/3_processed/`.

- **CN assets**: dispatched by asset type (see table below)
- **US/HK assets** (`US_ETF`, `HK_ETF`): fetches via `yfinance`

Each asset is processed independently. An asset is skipped if its CSV does not exist (calibration needed) or if it is already up to date.

## Asset Type Dispatch (CN)

| Asset Type | Data Source | total_return | close |
|-----------|-------------|-------------|-------|
| **CN_ETF** (159xxx/51xxxx/56xxxx/58xxxx) | CSI TR API → Sina fallback | `stock_zh_index_hist_csindex(tr_code, start, end)` *收盘* | `fund_etf_hist_sina()` *close* |
| **CN_OTC** (003xxx-009xxx) non-equity | 天天基金 OTC API | `fund_open_fund_info_em(累计净值走势)` | `fund_open_fund_info_em(单位净值走势)` |
| **CN_OTC** equity, has CSI TR | CSI TR API + 天天基金 | `stock_zh_index_hist_csindex(tr_code, start, end)` | `fund_open_fund_info_em(单位净值走势)` |
| **CN_OTC** equity, no CSI TR | 天天基金 OTC API (fallback) | cum_nav from 天天基金 | unit_nav from 天天基金 |
| **CN_INDEX** | CSI TR or Sina | price index | price index |

⚠️ **CSI TR calls MUST include `start_date`/`end_date` params**. Without date params, akshare's `stock_zh_index_hist_csindex()` defaults to stale data (~2024). The code correctly passes date params.

## Usage

```bash
# Active-state only (default scope, safe for cron/analyze)
python3 skills/data-daily-update/scripts/run_daily_update.py --region cn
python3 skills/data-daily-update/scripts/run_daily_update.py --region us

# Explicit scope: active / watchlist / void / all
python3 skills/data-daily-update/scripts/run_daily_update.py --region cn --state active
python3 skills/data-daily-update/scripts/run_daily_update.py --region cn --state all  # full scan

# Single-symbol adhoc
python3 skills/data-daily-update/scripts/run_daily_update.py --symbol 159207
```

⚠️ **Scope is mandatory**: `--region` without `--state` defaults to `active` only.
Use `--state all` explicitly if you need to scan all CN assets (e.g. mass bootstrap).

## Cron Schedule

This skill is triggered automatically via OpenClaw cron jobs:

- CN: 4:00 AM Beijing, Tue-Sat
- US: 6:00 AM Beijing, Tue-Sat

See `~/.openclaw/cron/jobs.json` for full configuration.

## Data Sources

| Provider | Asset Types | Access | Notes |
|----------|-------------|--------|-------|
| **CSIndex TR API** | CN_ETF / CN_OTC (equity) with `tr_index` | `stock_zh_index_hist_csindex(tr_code, start_date, end_date)` | **Must pass start/end dates**. CSI TR values used for total_return pct calculation. |
| **Sina Finance** | CN_ETF (fallback) | `fund_etf_hist_sina(symbol)` | Used when CSI TR unavailable or fails. |
| **天天基金 (East Money)** | CN_OTC (non-equity: bond/gold) | `fund_open_fund_info_em(symbol, 累计净值走势\|单位净值走势)` | Unit NAV as `close`, cum NAV as `total_return`. Primary source for non-equity OTC. |
| **天天基金 (East Money)** | CN_OTC (equity, fallback) | `fund_open_fund_info_em(symbol, 单位净值走势)` | Only for `close`. `total_return` comes from CSI TR. |
| **国证 (CNI)** | CN_ETF with provider=国证指数 | Sina only | No CSIndex API for 48xxxx codes. Always Sina. |

### CSI TR Fallback
If CSI API call fails, the pipeline silently falls back to Sina chain — no disruption.

## Workflow

1. resolve the target region and asset universe
2. check whether each asset has an existing processed CSV
3. fetch only the missing new rows from the configured provider
4. append validated rows to the processed history
5. report which assets updated, skipped, or failed

## Output Contract

The skill should produce:

- updated processed CSV files under `knowledge/{region}/3_processed/`
- a concise per-run status summary covering updated, skipped, and failed assets
- exit code `0` on success or partial skip, `1` when one or more assets fail

## Notes

- Intraday data is filtered out automatically if run before the cutoff time.
- Exit code `0` = success (including partial skips), `1` = one or more assets failed to update.
- Anti-ban delays are applied automatically between API calls.

## Error Handling

- missing CSV for an asset: skip it and report that calibration is required
- provider failure for one asset: continue other assets and mark the run failed overall if any asset failed
- intraday or incomplete rows: filter them out rather than appending partial data

## Format Specifications

- status output should distinguish updated, skipped, and failed assets
- file writes must append only new daily rows and preserve existing history
- do not present skipped assets as successful updates
