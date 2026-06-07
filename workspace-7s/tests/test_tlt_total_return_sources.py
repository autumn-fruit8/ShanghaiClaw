"""
Test: TLT total return from different data sources.

Compares total return series for TLT (iShares 20+ Year Treasury Bond ETF)
across multiple methods to quantify the dividend adjustment gap.

Data sources tested:
  A) yfinance adjusted close (current method — auto_adjust=True)
  B) yfinance manual DRIP (Close + Dividends, auto_adjust=False)
  C) Finnhub price data (with CSV total_return comparison)
  D) Direct Yahoo CSV via pandas-datareader (fallback)

Usage:
    python3 tests/test_tlt_total_return_sources.py [--plot] [--save]
"""

import os
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Make workspace importable
_WS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_WS))

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")


# ── helpers ──────────────────────────────────────────────────────────────────

def _annualized(series: pd.Series, rf: float = 0.04) -> float:
    """Annualized return from a total-return series (base 1000 or any base)."""
    if len(series) < 2:
        return 0.0
    total_ret = (series.iloc[-1] / series.iloc[0]) - 1.0
    n_days = len(series)
    return (1 + total_ret) ** (252 / n_days) - 1


# ── Test A: current method (yfinance auto_adjust=True) ───────────────────────

def test_a_yfinance_adj_close(symbol: str = "TLT", lookback: int = 500) -> pd.Series:
    """Method A: yfinance auto_adjust=True — what we currently use."""
    import yfinance as yf
    df = yf.download(symbol, period=f"{lookback + 100}d", auto_adjust=True, progress=False)
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    # Rebase to 1000
    result = (close / close.iloc[0]) * 1000
    result.name = "method_a_adj_close"
    return result


# ── Test B: yfinance manual DRIP ─────────────────────────────────────────────

def test_b_yfinance_drip(symbol: str = "TLT", lookback: int = 500) -> pd.Series:
    """Method B: yfinance auto_adjust=False, then manually reinvest dividends.

    1. Download raw Close + Dividends.
    2. Start from total_return[0] = close[0] (or 1000 base).
    3. Each day: apply price return, then add dividend reinvestment.
    4. Result = true total return (dividends reinvested at next day's open).
    """
    import yfinance as yf

    # Fetch raw data (unadjusted)
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=f"{lookback + 200}d", auto_adjust=False)

    if df.empty:
        raise RuntimeError(f"No data for {symbol}")

    close = df["Close"]
    dividends = df.get("Dividends", pd.Series(0.0, index=close.index))

    # Filter to lookback
    if len(close) > lookback + 10:
        close = close.iloc[-(lookback + 10):]
        dividends = dividends.reindex(close.index, fill_value=0.0)

    # Build total return series with DRIP
    n = len(close)
    tr = np.zeros(n)
    tr[0] = 1000.0  # base

    for i in range(1, n):
        price_ret = close.iloc[i] / close.iloc[i - 1] if close.iloc[i - 1] > 0 else 1.0
        shares_before_drip = tr[i - 1] / close.iloc[i - 1] if close.iloc[i - 1] > 0 else 0.0

        # Value before dividend = previous shares * previous close
        val_before_div = shares_before_drip * close.iloc[i - 1]

        # Add dividend: shares_before_drip * dividend_per_share = cash received
        div_cash = shares_before_drip * dividends.iloc[i - 1] if i - 1 < len(dividends) else 0.0

        # Reinvest dividend at today's price
        shares_after_div = div_cash / close.iloc[i] if close.iloc[i] > 0 else 0.0
        total_shares = shares_before_drip + shares_after_div

        # Apply price change to total value
        tr[i] = total_shares * close.iloc[i]

    result = pd.Series(tr, index=close.index, name="method_b_drip")
    return result


# ── Test C: Finnhub price data ───────────────────────────────────────────────

def test_c_finnhub(symbol: str = "TLT", lookback: int = 500) -> pd.Series | None:
    """Method C: Finnhub price data (limited history on free tier)."""
    if not FINNHUB_KEY:
        print("  [SKIP] No FINNHUB_API_KEY set")
        return None

    import requests

    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=lookback + 60)).strftime("%Y-%m-%d")
    url = (
        f"https://finnhub.io/api/v1/stock/candle"
        f"?symbol={symbol}&resolution=D"
        f"&from={int(datetime.strptime(from_date, '%Y-%m-%d').timestamp())}"
        f"&to={int(datetime.now().timestamp())}"
        f"&token={FINNHUB_KEY}"
    )
    resp = requests.get(url, timeout=30)
    data = resp.json()

    if data.get("s") != "ok" or "c" not in data:
        print(f"  [SKIP] Finnhub empty response: {data.get('s', 'unknown')}")
        return None

    dates = pd.to_datetime(data["t"], unit="s")
    prices = pd.Series(data["c"], index=dates, name="method_c_finnhub")
    prices = prices.sort_index()

    # Rebase to 1000
    result = (prices / prices.iloc[0]) * 1000
    result.name = "method_c_finnhub"
    return result


# ── Test D: existing CSV total_return ────────────────────────────────────────

def test_d_csv_current(symbol: str = "TLT", lookback: int = 500) -> pd.Series | None:
    """Method D: current CSV in knowledge/us/3_processed/ — for comparison."""
    csv_path = _WS / "knowledge" / "us" / "3_processed" / f"{symbol}.csv"
    if not csv_path.exists():
        print(f"  [SKIP] CSV not found: {csv_path}")
        return None

    df = pd.read_csv(csv_path, parse_dates=["date"])
    series = pd.Series(
        df["total_return"].values,
        index=pd.to_datetime(df["date"]),
        name="method_d_csv_current",
    )
    series = series.sort_index()

    # Trim to lookback from end
    if len(series) > lookback + 10:
        series = series.iloc[-(lookback + 10):]

    return series


# ── Test E: yfinance split-adjusted return (adj close only, current pipeline equiv) ──

def test_e_yfinance_batch_mode(symbol: str = "TLT", lookback: int = 500) -> pd.Series:
    """Method E: yfinance batch download (auto_adjust=True) — exactly what us_daily.py does."""
    import yfinance as yf
    df = yf.download(
        tickers=symbol,
        period=f"{lookback + 100}d",
        auto_adjust=True,
        group_by="ticker",
        progress=False,
    )
    # us_daily.py uses 'Close' column with auto_adjust=True
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    result = pd.Series(
        close.values, index=close.index, name="method_e_batch"
    )
    # Normalize to 1000
    result = (result / result.iloc[0]) * 1000
    return result


# ── Comparison runner ────────────────────────────────────────────────────────

def run_tests(symbol: str = "TLT", lookback: int = 500, plot: bool = False, save: bool = False):
    """Run all available test methods and compare results."""
    print(f"\n{'='*60}")
    print(f"  TLT Total Return Source Comparison")
    print(f"  Lookback: ~{lookback} trading days (est. {lookback//252:.0f} years)")
    print(f"{'='*60}\n")

    results: dict[str, pd.Series] = {}

    # --- Test A: yfinance auto_adjust=True ---
    print("  [A] yfinance auto_adjust=True (Close = adj close)...")
    try:
        results["A_AdjClose"] = test_a_yfinance_adj_close(symbol, lookback)
        tr = (results["A_AdjClose"].iloc[-1] / results["A_AdjClose"].iloc[0] - 1) * 100
        ann = _annualized(results["A_AdjClose"]) * 100
        n = len(results["A_AdjClose"])
        print(f"      ✓ {n} days | Total: {tr:+.2f}% | Ann: {ann:+.2f}%")
    except Exception as e:
        print(f"      ✗ FAILED: {e}")

    # --- Test B: yfinance manual DRIP ---
    print("  [B] yfinance auto_adjust=False + manual DRIP...")
    try:
        results["B_DRIP"] = test_b_yfinance_drip(symbol, lookback)
        tr = (results["B_DRIP"].iloc[-1] / results["B_DRIP"].iloc[0] - 1) * 100
        ann = _annualized(results["B_DRIP"]) * 100
        n = len(results["B_DRIP"])
        print(f"      ✓ {n} days | Total: {tr:+.2f}% | Ann: {ann:+.2f}%")
    except Exception as e:
        print(f"      ✗ FAILED: {e}")

    # --- Test C: Finnhub ---
    print("  [C] Finnhub price data...")
    try:
        series = test_c_finnhub(symbol, lookback)
        if series is not None:
            results["C_Finnhub"] = series
            tr = (series.iloc[-1] / series.iloc[0] - 1) * 100
            ann = _annualized(series) * 100
            n = len(series)
            print(f"      ✓ {n} days | Total: {tr:+.2f}% | Ann: {ann:+.2f}%")
    except Exception as e:
        print(f"      ✗ FAILED: {e}")

    # --- Test D: Current CSV ---
    print("  [D] Current CSV (knowledge/us/3_processed/TLT.csv)...")
    try:
        series = test_d_csv_current(symbol, lookback)
        if series is not None:
            results["D_CSV"] = series
            tr = (series.iloc[-1] / series.iloc[0] - 1) * 100
            ann = _annualized(series) * 100
            n = len(series)
            print(f"      ✓ {n} days | Total: {tr:+.2f}% | Ann: {ann:+.2f}%")
    except Exception as e:
        print(f"      ✗ FAILED: {e}")

    # --- Test E: yfinance batch mode (exact pipeline copy) ---
    print("  [E] yfinance batch download (auto_adjust=True, exact pipeline)...")
    try:
        results["E_Batch"] = test_e_yfinance_batch_mode(symbol, lookback)
        tr = (results["E_Batch"].iloc[-1] / results["E_Batch"].iloc[0] - 1) * 100
        ann = _annualized(results["E_Batch"]) * 100
        n = len(results["E_Batch"])
        print(f"      ✓ {n} days | Total: {tr:+.2f}% | Ann: {ann:+.2f}%")
    except Exception as e:
        print(f"      ✗ FAILED: {e}")

    # ── Summary table ────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  {'Source':<24} {'Days':>6} {'Total Ret':>10} {'Ann Ret':>10}")
    print(f"{'─'*60}")
    for key, series in results.items():
        tr = (series.iloc[-1] / series.iloc[0] - 1) * 100
        ann = _annualized(series) * 100
        label = {
            "A_AdjClose": "A) yf auto_adjust=True",
            "B_DRIP": "B) yf manual DRIP",
            "C_Finnhub": "C) Finnhub",
            "D_CSV": "D) Current CSV",
            "E_Batch": "E) yf batch (pipeline)",
        }.get(key, key)
        print(f"  {label:<24} {len(series):>6} {tr:+>9.2f}% {ann:+>9.2f}%")

    # ── DRIP vs Adj Close delta ──
    if "B_DRIP" in results and "A_AdjClose" in results:
        drip_tr = (results["B_DRIP"].iloc[-1] / results["B_DRIP"].iloc[0] - 1) * 100
        adj_tr = (results["A_AdjClose"].iloc[-1] / results["A_AdjClose"].iloc[0] - 1) * 100
        delta = drip_tr - adj_tr
        print(f"{'─'*60}")
        print(f"  🎯 DRIP - AdjClose delta: {delta:+.2f}% (dividend contribution)")
        print(f"  TLT true total return (DRIP): {drip_tr:+.2f}%")
        print(f"  TLT adjusted close return:    {adj_tr:+.2f}%")
        if delta > 0:
            print(f"  ✅ Dividends add {delta:.2f}% of hidden return")

    # ── CSV vs DRIP delta ──
    if "B_DRIP" in results and "D_CSV" in results:
        csv_tr = (results["D_CSV"].iloc[-1] / results["D_CSV"].iloc[0] - 1) * 100
        drip_tr = (results["B_DRIP"].iloc[-1] / results["B_DRIP"].iloc[0] - 1) * 100
        delta_csv = drip_tr - csv_tr
        print(f"{'─'*60}")
        print(f"  📊 Current CSV vs DRIP delta: {delta_csv:+.2f}%")
        print(f"  DRIP matches CSV? ", end="")
        if abs(delta_csv) < 1:
            print("YES ✅ (current CSV already includes dividends)")
        else:
            print("NO ❌ (current CSV missing dividend contribution)")

    # ── Plot ──
    if plot and len(results) >= 2:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(12, 6))
            colors = {"A_AdjClose": "#888", "B_DRIP": "#d32f2f", "C_Finnhub": "#2e7d32",
                      "D_CSV": "#1565c0", "E_Batch": "#f57c00"}
            labels = {"A_AdjClose": "yf AdjClose", "B_DRIP": "yf DRIP",
                      "C_Finnhub": "Finnhub", "D_CSV": "Current CSV", "E_Batch": "yf Batch"}

            for key, series in results.items():
                ax.plot(series.index, series.values,
                        color=colors.get(key, "#333"),
                        label=labels.get(key, key),
                        linewidth=2 if key == "B_DRIP" else 1,
                        alpha=0.9 if key == "B_DRIP" else 0.6)

            ax.set_title(f"TLT Total Return Comparison (base 1000) — {symbol}", fontsize=14)
            ax.set_ylabel("Total Return (base 1000)")
            ax.legend(fontsize=10)
            ax.grid(True, alpha=0.3)
            ax.axhline(1000, color="#333", linewidth=0.5, linestyle="--")

            # Annotate final values
            for key, series in results.items():
                tr = (series.iloc[-1] / series.iloc[0] - 1) * 100
                ax.annotate(f"{tr:+.1f}%",
                            xy=(series.index[-1], series.values[-1]),
                            fontsize=8, fontweight="bold",
                            color=colors.get(key, "#333"))

            fig.tight_layout()

            out_dir = _WS / "logs" / "tests"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"tlt_return_sources_comparison.png"
            fig.savefig(out_path, dpi=150, bbox_inches="tight")
            print(f"\n  📈 Chart saved: {out_path}")
            plt.close(fig)

            if save:
                # Also save CSV — align indexes (handle tz-aware vs tz-naive)
                csv_out = out_dir / "tlt_return_sources_data.csv"
                aligned = {}
                for k, s in results.items():
                    idx = s.index
                    if hasattr(idx, 'tz') and idx.tz is not None:
                        idx = idx.tz_localize(None)
                    aligned[k] = pd.Series(s.values, index=idx)
                combined = pd.DataFrame(aligned)
                combined.to_csv(csv_out)
                print(f"  📊 Data saved: {csv_out}")
        except ImportError:
            print("  ℹ️  matplotlib not available, skipping chart")

    print(f"\n{'='*60}")
    print(f"  Done. See comments above for recommendation.")
    print(f"{'='*60}\n")


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TLT total return source comparison")
    parser.add_argument("--symbol", default="TLT", help="Symbol to test (default: TLT)")
    parser.add_argument("--lookback", type=int, default=500, help="Lookback trading days")
    parser.add_argument("--plot", action="store_true", help="Generate comparison chart")
    parser.add_argument("--save", action="store_true", help="Save data CSV too")
    args = parser.parse_args()

    run_tests(
        symbol=args.symbol,
        lookback=args.lookback,
        plot=args.plot,
        save=args.save,
    )
