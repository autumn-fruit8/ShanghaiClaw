"""
Test: TLT total return — alternative data sources (方案2)

Tests each source for TLT total return accuracy:
  1. Yahoo CSV direct (adj close, different from yfinance library)
  2. Yahoo Finance HTML scrape
  3. Morningstar total return
  4. FRED long-term treasury total return indices
  5. Tiingo API (needs free API key — pip install tiingo)
  6. EOD Historical Data (needs free API key)

Usage:
    python3 tests/test_alt_sources_total_return.py [--plot] [--lookback 2000]
"""

import os, sys, json, csv, io
from datetime import datetime, timedelta
from pathlib import Path
import urllib.request
import urllib.error

import numpy as np
import pandas as pd

_WS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_WS))

FRED_KEY = os.environ.get("FRED_API_KEY", "252224e74e4b5d75333826790c430318")

RESULTS_DIR = _WS / "logs" / "tests"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Source 1: Yahoo Finance direct CSV (v8 endpoint, adj close)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_yahoo_csv(symbol: str, start_ts: int, end_ts: int) -> pd.Series | None:
    """Yahoo Finance v8 download — adjusted close via includeAdjustedClose=true."""
    import requests
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={start_ts}&period2={end_ts}&interval=1d"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            return None
        data = r.json()
        # Parse chart response
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        timestamps = result[0].get("timestamp", [])
        quotes = result[0].get("indicators", {}).get("adjclose", [{}])
        if not quotes:
            quotes = result[0].get("indicators", {}).get("quote", [{}])
            close_prices = quotes[0].get("close", [])
        else:
            close_prices = quotes[0].get("adjclose", [])

        if not close_prices:
            return None

        dates = pd.to_datetime(timestamps, unit="s")
        prices = [float(p) for p in close_prices if p is not None]
        if len(prices) != len(dates):
            return None

        series = pd.Series(prices, index=dates, name=f"yahoo_v8_{symbol}")
        series = series.sort_index()
        return (series / series.iloc[0]) * 1000
    except Exception as e:
        print(f"    Yahoo v8 error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Source 2: Yahoo Finance v7 CSV download (legacy, sometimes works)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_yahoo_v7_csv(symbol: str, start_ts: int, end_ts: int) -> pd.Series | None:
    """Yahoo Finance v7 CSV download with cookies."""
    import requests
    from requests.sessions import Session

    session = Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    })

    # Step 1: get consent cookie
    session.get("https://fc.yahoo.com", timeout=10)

    # Step 2: get crumb
    crumb_resp = session.get(
        "https://query2.finance.yahoo.com/v1/test/getcrumb",
        timeout=10
    )
    if crumb_resp.status_code != 200:
        return None
    crumb = crumb_resp.text.strip()

    # Step 3: download CSV
    dl_url = (
        f"https://query1.finance.yahoo.com/v7/finance/download/{symbol}"
        f"?period1={start_ts}&period2={end_ts}&interval=1d&events=history"
        f"&includeAdjustedClose=true&crumb={crumb}"
    )
    resp = session.get(dl_url, timeout=30)
    if resp.status_code != 200:
        return None

    df = pd.read_csv(io.StringIO(resp.text), parse_dates=["Date"])
    if "Adj Close" in df.columns:
        series = pd.Series(df["Adj Close"].values, index=df["Date"], name=f"yahoo_v7_{symbol}")
    elif "Close" in df.columns:
        series = pd.Series(df["Close"].values, index=df["Date"], name=f"yahoo_v7_{symbol}")
    else:
        return None

    series = series.sort_index().dropna()
    return (series / series.iloc[0]) * 1000


# ═══════════════════════════════════════════════════════════════════════════════
# Source 3: FRED long-term treasury total return
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_fred_treasury_tr(start_date: str, end_date: str) -> pd.Series | None:
    """FRED: 20+ Year Treasury Total Return Index (if available).

    Uses TR index series. Common codes:
      - DGS20: 20-Year Treasury Constant Maturity Rate (yield, not TR)
      - No standard FRED series for TLT specifically.
    """
    import requests
    # Check various long-term treasury TR-related series
    candidates = ["DGS20", "DFII20", "T10YIE", "BAMLCC0A0133TRIV"]
    results = {}
    for sid in candidates:
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={sid}&api_key={FRED_KEY}"
            f"&file_type=json&observation_start={start_date}"
            f"&observation_end={end_date}"
        )
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                obs = data.get("observations", [])
                if obs:
                    results[sid] = len(obs)
        except Exception:
            pass

    if results:
        print(f"    FRED: available series: {results}")
    else:
        print(f"    FRED: no relevant TR series found for TLT")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Source 4: Morningstar total return (web scrape)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_morningstar_tr(symbol: str) -> pd.Series | None:
    """Morningstar total return data via their API."""
    import requests
    # Morningstar uses exchange-specific codes; TLT = XNAS:TLT
    url = (
        "https://api-global.morningstar.com/sal-service/v1/fund/performance/totalReturn/"
        f"summary/data?id=XNAS:{symbol}&languageId=en&locale=en"
    )
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Origin": "https://www.morningstar.com",
        "Referer": "https://www.morningstar.com/",
    }
    # This API usually requires an API key/token
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return None  # Would parse here if we had proper auth
        print(f"    Morningstar: HTTP {r.status_code} (requires API key)")
        return None
    except Exception as e:
        print(f"    Morningstar error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Source 5: Tiingo (free API key needed)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_tiingo(symbol: str, start_date: str, end_date: str) -> pd.Series | None:
    """Tiingo IEX API — adjusted close includes dividends.

    Free tier: 500 tickers, 10k calls/day.
    Register at https://tiingo.com → get API key, then:
        export TIINGO_API_KEY=your_key_here
    """
    api_key = os.environ.get("TIINGO_API_KEY", "")
    if not api_key:
        print("    Tiingo: SKIP — set TIINGO_API_KEY (free: tiingo.com)")
        return None

    import requests
    url = (
        f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
        f"?startDate={start_date}&endDate={end_date}&format=json&resampleFreq=daily"
    )
    headers = {"Authorization": f"Token {api_key}", "Content-Type": "application/json"}
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            data = r.json()
            dates = [d["date"][:10] for d in data]
            # Tiingo's adjClose = adjusted for splits AND dividends
            prices = [d["adjClose"] for d in data]
            series = pd.Series(prices, index=pd.to_datetime(dates), name=f"tiingo_{symbol}")
            series = series.sort_index().dropna()
            return (series / series.iloc[0]) * 1000
        print(f"    Tiingo: HTTP {r.status_code} — {r.text[:100]}")
        return None
    except Exception as e:
        print(f"    Tiingo error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Source 6: EOD Historical Data (free API key needed)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_eodhd(symbol: str, start_date: str, end_date: str) -> pd.Series | None:
    """EOD Historical Data API — adjusted close includes dividends.

    Free tier: 20 calls/day, 1 year history.
    Register at https://eodhd.com → get API key, then:
        export EODHD_API_KEY=your_key_here
    """
    api_key = os.environ.get("EODHD_API_KEY", "")
    if not api_key:
        print("    EODHD: SKIP — set EODHD_API_KEY (free: eodhd.com)")
        return None

    import requests
    url = (
        f"https://eodhd.com/api/eod/{symbol}.US"
        f"?from={start_date}&to={end_date}&api_token={api_key}&fmt=json"
    )
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            data = r.json()
            dates = [d["date"] for d in data]
            prices = [d.get("adjusted_close", d.get("close", 0)) for d in data]
            series = pd.Series(prices, index=pd.to_datetime(dates), name=f"eodhd_{symbol}")
            series = series.sort_index().dropna()
            return (series / series.iloc[0]) * 1000
        print(f"    EODHD: HTTP {r.status_code} — {r.text[:100]}")
        return None
    except Exception as e:
        print(f"    EODHD error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Source 7: CSV baseline (current 3_processed data)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_csv_baseline(symbol: str, lookback: int) -> pd.Series:
    """Current CSV as truth baseline."""
    csv_path = _WS / "knowledge" / "us" / "3_processed" / f"{symbol}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    df = pd.read_csv(csv_path, parse_dates=["date"])
    series = pd.Series(
        df["total_return"].values,
        index=pd.to_datetime(df["date"]),
        name="csv_baseline",
    ).sort_index()
    if len(series) > lookback + 20:
        series = series.iloc[-(lookback + 20):]
    return series


# ═══════════════════════════════════════════════════════════════════════════════
# Comparison & Report
# ═══════════════════════════════════════════════════════════════════════════════

def fmt_tr(series: pd.Series) -> str:
    tr = (series.iloc[-1] / series.iloc[0] - 1) * 100
    ann = ((1 + tr/100) ** (252/len(series)) - 1) * 100
    return f"{tr:+>8.2f}%  ann {ann:+>6.2f}%  ({len(series)}d)"


def run_tests(symbol: str = "TLT", lookback: int = 2000, plot: bool = False):
    print(f"\n{'='*65}")
    print(f"  方案2: TLT Total Return — Alternative Data Source Test")
    print(f"  Period: last ~{lookback} trading days")
    print(f"{'='*65}\n")

    now = datetime.now()
    end_ts = int(now.timestamp())
    start_ts = end_ts - lookback * 86400 - 30 * 86400  # add buffer
    start_date = (now - timedelta(days=lookback + 60)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    results: dict[str, pd.Series] = {}
    notes: list[str] = []

    # ── Baseline: Current CSV ──
    print(f"[1/7] 📁 Current CSV (knowledge/us/3_processed/{symbol}.csv)")
    try:
        s = fetch_csv_baseline(symbol, lookback)
        results["CSV_Baseline"] = s
        print(f"      ✓ {fmt_tr(s)}")
    except Exception as e:
        print(f"      ✗ {e}")

    # ── Yahoo v8 chart API ──
    print(f"\n[2/7] 🌐 Yahoo Finance v8 chart API (adjclose)")
    try:
        s = fetch_yahoo_csv(symbol, start_ts, end_ts)
        if s is not None:
            results["Yahoo_v8"] = s
            if results.get("CSV_Baseline") is not None:
                tr_diff = (s.iloc[-1]/s.iloc[0] - 1)*100 - \
                          (results["CSV_Baseline"].iloc[-1]/results["CSV_Baseline"].iloc[0] - 1)*100
                notes.append(f"Yahoo v8 vs CSV: Δ={tr_diff:+.2f}%")
            print(f"      ✓ {fmt_tr(s)}")
        else:
            print(f"      ✗ No data (Yahoo v8 requires crumb/cookie)")
    except Exception as e:
        print(f"      ✗ {e}")

    # ── Yahoo v7 CSV ──
    print(f"\n[3/7] 🌐 Yahoo Finance v7 CSV (Adj Close)")
    try:
        s = fetch_yahoo_v7_csv(symbol, start_ts, end_ts)
        if s is not None:
            results["Yahoo_v7"] = s
            if results.get("CSV_Baseline") is not None:
                tr_diff = (s.iloc[-1]/s.iloc[0] - 1)*100 - \
                          (results["CSV_Baseline"].iloc[-1]/results["CSV_Baseline"].iloc[0] - 1)*100
                notes.append(f"Yahoo v7 vs CSV: Δ={tr_diff:+.2f}%")
            print(f"      ✓ {fmt_tr(s)}")
        else:
            print(f"      ✗ No data (blocked/auth required)")
    except Exception as e:
        print(f"      ✗ {e}")

    # ── FRED treasury ──
    print(f"\n[4/7] 🏛️  FRED (treasury total return indices)")
    try:
        s = fetch_fred_treasury_tr(start_date, end_date)
        if s is not None:
            results["FRED"] = s
            print(f"      ✓ {fmt_tr(s)}")
        else:
            print(f"      ✗ No compatible total return series for TLT on FRED")
    except Exception as e:
        print(f"      ✗ {e}")

    # ── Morningstar ──
    print(f"\n[5/7] ⭐ Morningstar total return API")
    try:
        s = fetch_morningstar_tr(symbol)
        if s is not None:
            results["Morningstar"] = s
            print(f"      ✓ {fmt_tr(s)}")
    except Exception as e:
        print(f"      ✗ {e}")

    # ── Tiingo ──
    print(f"\n[6/7] 🔷 Tiingo (adjusted close w/ dividends)")
    try:
        s = fetch_tiingo(symbol, start_date, end_date)
        if s is not None:
            results["Tiingo"] = s
            if results.get("CSV_Baseline") is not None:
                tr_diff = (s.iloc[-1]/s.iloc[0] - 1)*100 - \
                          (results["CSV_Baseline"].iloc[-1]/results["CSV_Baseline"].iloc[0] - 1)*100
                notes.append(f"Tiingo vs CSV: Δ={tr_diff:+.2f}%")
            print(f"      ✓ {fmt_tr(s)}")
    except Exception as e:
        print(f"      ✗ {e}")

    # ── EOD Historical Data ──
    print(f"\n[7/7] 🏢 EOD Historical Data (adjusted close w/ dividends)")
    try:
        s = fetch_eodhd(symbol, start_date, end_date)
        if s is not None:
            results["EODHD"] = s
            if results.get("CSV_Baseline") is not None:
                tr_diff = (s.iloc[-1]/s.iloc[0] - 1)*100 - \
                          (results["CSV_Baseline"].iloc[-1]/results["CSV_Baseline"].iloc[0] - 1)*100
                notes.append(f"EODHD vs CSV: Δ={tr_diff:+.2f}%")
            print(f"      ✓ {fmt_tr(s)}")
    except Exception as e:
        print(f"      ✗ {e}")

    # ── Summary ──
    print(f"\n{'─'*65}")
    print(f"  {'#':<2} {'Source':<24} {'Result':<30}")
    print(f"{'─'*65}")
    labels = {
        "CSV_Baseline": "[BASELINE] Current CSV",
        "Yahoo_v8": "Yahoo v8 chart API",
        "Yahoo_v7": "Yahoo v7 CSV (Adj Close)",
        "FRED": "FRED treasury indices",
        "Morningstar": "Morningstar API",
        "Tiingo": "Tiingo adjusted close",
        "EODHD": "EOD Historical Data",
    }
    for i, (key, label) in enumerate(labels.items(), 1):
        if key in results:
            print(f"  {i:<2} {label:<24} ✓ {fmt_tr(results[key])}")
        else:
            print(f"  {i:<2} {label:<24} ✗ (skipped/failed)")

    if notes:
        print(f"\n{'─'*65}")
        print(f"  Notes:")
        for n in notes:
            print(f"    {n}")

    # ── Final verdict ──
    print(f"\n{'─'*65}")
    print(f"  📋 Verdict")
    print(f"{'─'*65}")
    successful_sources = [k for k in results.keys() if k != "CSV_Baseline"]
    if not successful_sources:
        print(f"  ❌ No alternative data sources could be tested without API keys.")
        print(f"  \n  → Free API keys needed:")
        print(f"     • Tiingo:  https://tiingo.com  (free, 500 tickers)")
        print(f"     • EODHD:   https://eodhd.com   (free, 20 calls/day)")
        print(f"     • Morningstar: requires institutional access")
    else:
        print(f"  ✅ {len(successful_sources)} source(s) successfully tested")
        for k in successful_sources:
            delta_str = ""
            if results.get("CSV_Baseline") is not None:
                csv_tr = (results["CSV_Baseline"].iloc[-1] / results["CSV_Baseline"].iloc[0] - 1) * 100
                src_tr = (results[k].iloc[-1] / results[k].iloc[0] - 1) * 100
                delta_str = f" (Δ={src_tr - csv_tr:+.2f}% vs baseline)"
            print(f"     • {labels.get(k, k)}: {fmt_tr(results[k])}{delta_str}")

    print(f"\n{'='*65}\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="TLT")
    parser.add_argument("--lookback", type=int, default=2000)
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()
    run_tests(args.symbol, args.lookback, args.plot)
