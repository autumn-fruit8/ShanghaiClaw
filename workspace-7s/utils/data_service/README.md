# Data Service Layer — 7S Workspace

## Directory Structure

```
utils/data_service/
├── README.md              ← This file
├── data_resolver.py       ← 3-tier CSV resolver (knowledge → adhoc → bootstrap)
├── market_service.py      ← All external API calls (yfinance, akshare, FRED, Tiingo)
├── bond_service.py        ← Bond yield/price data (CGB, US Treasury)
├── bond_yield.py          ← Yield curve computation & percentile analysis
├── snapshot_enricher.py   ← Volume ratio & fund flow enrichment for signal snapshots
└── file_parser.py         ← CSV/HTML/file parsing utilities
```

## Data Flow

```
User Request (analyze 159307)
    │
    ▼
data_resolver.resolve_price_data()
    │  Tier 1: knowledge/{region}/3_processed/{symbol}.csv  ← live state
    │  Tier 2: adhoc/cache/{symbol}.csv                      ← temp dry-run
    │  Tier 3: bootstrap from API → write to adhoc/cache/     ← first-time
    │
    ▼
market_service.fetch_*(symbol, region)
    │  Per region/symbol type:
    │  CN_ETF     → CSI TR API → Sina OHLCV (volume)
    │  CN_OTC     → 天天基金 NAV (unit + cumulative)
    │  CN_INDEX   → CSI price index
    │  US_ETF/HK  → yfinance → Tiingo (fallback)
    │
    ▼
snapshot_enricher.enrich_snapshot()
    │  volume_note: latest/20d_avg (with Tiingo fallback for US)
    │
    ▼
Consumer (strategy.py, s3_system, report_pusher)
```

---

## Data Sources by Region

### 🇨🇳 CN (A-Share ETFs & Funds)

| Asset Type | Total Return (price) | Close (unit price) | Volume | Provider |
|-----------|---------------------|-------------------|--------|----------|
| **CN_ETF** 159xxx, 51xxxx, 56xxxx | CSI TR API (`stock_zh_index_hist_csindex`) | Sina Finance (`fund_etf_hist_sina`) | Sina Finance | CSI + Sina |
| **CN_OTC** 00xxxx, 01xxxx | 天天基金 cumulative NAV (`fund_open_fund_info_em`) | 天天基金 unit NAV | N/A (no volume for OTC) | 天天基金 |
| **CN_INDEX** 000xxx, 399xxx | CSI price index | CSI price index | N/A | CSI |

**CSI TR API**: Prefers total-return index codes (H20955, H00300, etc.). 
  - Requires `start_date`/`end_date` params — without them, akshare returns stale ~2024 data.
  - Falls back to Sina chain on failure.

**Sina Finance** (`fund_etf_hist_sina`): Provides OHLCV for CN ETFs.
  - Symbol prefix: `sz` for Shenzhen (159xxx), `sh` for Shanghai (51xxxx, 56xxxx).
  - "volume" column is in shares (手 = 100 shares in some APIs, raw shares in others).

**天天基金** (`fund_open_fund_info_em`): For OTC mutual funds.
  - `"累计净值走势"` → cum_nav (proxy for total_return)
  - `"单位净值走势"` → unit_nav (actual close price)

### 🇺🇸 US (US ETFs)

| Asset Type | Total Return | Close | Volume | Provider |
|-----------|-------------|-------|--------|----------|
| **US_ETF** (SPY, TLT, etc.) | yfinance `Ticker.history()` | yfinance | yfinance → **Tiingo** (fallback) | Yahoo + Tiingo |

**yfinance**: Primary source for US ETF data.
  - `yf.download()` for bulk, `Ticker.history()` for individual.
  - Subject to rate limiting (YFRateLimitError) — typically clears in 15-60 min.

**Tiingo** (fallback): Used when yfinance is rate-limited.
  - Provides daily OHLCV for US ETFs.
  - `TiingoClient.get_dataframe(symbol, frequency="daily")`.
  - API key from `.env`: `TIINGO_API_KEY`.

### 🇭🇰 HK (Hong Kong ETFs)

| Asset Type | Total Return | Close | Volume | Provider |
|-----------|-------------|-------|--------|----------|
| **HK_ETF** (xxxx.HK) | yfinance | yfinance | yfinance | Yahoo |

---

## Fallback Mechanisms

### Volume Data (3-layer fallback)

```
_get_volume_data(symbol, region)
    │
    ├── ① Read CSV → latest row's volume
    │
    ├── ② If latest == 0 AND region == "US":
    │      → Try Tiingo API for actual latest volume
    │
    └── ③ If still 0:
           → Use previous row's volume (avoid 0.0x ratio)
```

This handles:
- yfinance rate limits (US) → Tiingo → prev row
- CSI TR only returns price, not volume → volume from Sina (already written to CSV)
- ETF not traded on a given day → volume naturally 0, but 20d avg still valid

### Total Return Data (CSI TR fallback chain)

```
fetch_incremental_data(symbol, asset_info, last_date)
    │
    ├── ① CSI TR API (中证指数 total return)
    │      → stock_zh_index_hist_csindex(tr_code, start_date, end_date)
    │
    └── ② Sina/EM fallback (when CSI TR fails):
           CN_ETF → fund_etf_hist_sina(sina_symbol, period)
           CN_OTC → fund_open_fund_info_em(symbol, "累计净值走势")
```

### US Fund Flow (capital flow data — CURRENTLY UNAVAILABLE)

The East Money fund flow API (`push2his.eastmoney.com`) is **blocked from this host** (502/connection aborted).
All alternatives also blocked or limited:
- 同花顺 ranking API: Only covers actively traded stocks (159307 not guaranteed)
- Sina moneyflow: Endpoint deprecated/invalid
- FRED: No PE data for US equities on free tier
- Finnhub: 403 Forbidden for OHLCV, no PE metric on free plan

Volume ratio is used instead of fund flow.

---

## CSV Contract

Every processed CSV in `knowledge/{region}/3_processed/{symbol}.csv`:

```csv
date,total_return,close,volume
2026-05-20,22658.01,1.048,106665500
2026-05-21,22562.82,1.044,114157000
```

| Column | Description | Data Source |
|--------|-------------|-------------|
| `date` | Trading date (YYYY-MM-DD) | All |
| `total_return` | Dividend-adjusted total return (normalized to base) | CSI TR / yfinance / 天天基金 cum_nav |
| `close` | Raw unit price (NAV or market close) | Sina / yfinance / 天天基金 unit_nav |
| `volume` | Daily trading volume (shares) | Sina / yfinance / Tiingo |

- `volume` column is **optional** — OTC funds and some US ETFs may lack it.
- `total_return` and `close` are **mandatory** (signal layer uses total_return, position layer uses close).
- Never cross the streams: total_return for signals, close for pricing.

---

## S3 System — Macro Data Sources

The S3 system assessment uses external macro data for regime classification:

| Data | Source | API | Cache |
|------|--------|-----|-------|
| Fed Funds Rate | FRED | `fredapi.Fred.get_series("FEDFUNDS")` | 4h TTL |
| 10Y Treasury | FRED | `fredapi.Fred.get_series("DGS10")` | 4h TTL |
| 10Y TIPS Real Yield | FRED | `fredapi.Fred.get_series("DFII10")` | 4h TTL |
| Breakeven Inflation | FRED | `fredapi.Fred.get_series("T10YIE")` | 4h TTL |
| CPI | FRED | `fredapi.Fred.get_series("CPIAUCSL")` | 4h TTL |
| Unemployment | FRED | `fredapi.Fred.get_series("UNRATE")` | 4h TTL |
| Credit Spread (BAA-10Y) | FRED | `fredapi.Fred.get_series("BAA10Y")` | 4h TTL |
| Industrial Production | FRED | `fredapi.Fred.get_series("INDPRO")` | 4h TTL |
| VIX | Yahoo Finance | `yf.download("^VIX")` | 4h TTL |
| **CN 10Y CGB** | akshare | `bond_zh_us_rate()` → 中国国债收益率10年 | per-call |
| **US ERP** | yfinance → Tiingo | SPY P/E → earnings yield - DGS10 | in-memory |
| **CN ERP** | akshare | CSI 300 PE + CGB 10Y → earnings yield - CGB | in-memory |

FRED data is cached in `adhoc/cache/system/fred_*.json` with 4-hour TTL.

---

## Volume Ratio Interpretation

The `volume_note` field appended to each signal snapshot:

| Ratio | Label | Interpretation |
|-------|-------|---------------|
| ≥ 2.0x | 🔥 N.x倍放量 | Abnormal volume surge — possible breakout or distribution |
| 1.2–1.99x | 📊 N.x倍 | Elevated volume — active trading interest |
| 0.7–1.19x | 量比N.x倍 | Normal volume — baseline activity |
| < 0.7x | 💤 N.x倍缩量 | Low volume — lack of interest or consolidation |

Calculated as: `latest_trading_day_volume / 20_day_moving_average_volume`

---

## Cache Convention

Two levels of cache under `adhoc/cache/`:

```
adhoc/cache/
├── {symbol}.csv             ← Symbol data cache (bootstrap temp, dry-run)
│                               Used by data_resolver, market_service, log charts
│
└── system/                  ← System cache (FRED, VIX, bond yields — 4h TTL)
    ├── fred_{series}.json     S3 macro assessment
    ├── vix_history.json       VIX analysis
    └── bond_yield/            Bond yield series
        └── bond_yield_{region}_{tenor}.csv
```

- `cache/{symbol}.csv` = transient bootstrap data, cleaned on refresh.
- `cache/system/` = stateful system cache backed by persistent APIs (FRED, akshare), survives adhoc refreshes.
- `CACHE_ROOT` = `adhoc/cache/system/`, defined as single source of truth in `config/__init__.py`.
- `bond_yield.py` was removed 2026-05-28 (dead code, superseded by `bond_service.py`).
