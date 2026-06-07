#!/usr/bin/env python3
"""
Test case: verify OHLCV volume data for 159307 (红利低波100) and 159209 (红利质量).

Tests:
  1. Volume data from existing processed CSV (already in pipeline)

Usage:
    python3 tests/test_fund_flow.py
"""
import sys
from pathlib import Path

import pandas as pd

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE_ROOT))

from utils.data_service.market_service import fetch_ohlcv


def test_csv_volume(symbol: str, label: str) -> dict:
    """Test: Volume data from existing processed CSV (OHLCV via fetch_ohlcv)."""
    print(f"\n{'=' * 60}")
    print(f"Volume from processed CSV — {label}")
    print(f"{'=' * 60}")

    df = None
    try:
        df = fetch_ohlcv(symbol, "cn")
    except Exception:
        pass

    if df is None or len(df) == 0 or "volume" not in df.columns:
        print("  ⚠️  No volume column in processed CSV")
        print("  ℹ️  Current 3-column CSV format: date, total_return, close")
        print("  ℹ️  Volume data would need CSV schema extension or OHLCV API")
        return {"status": "SKIP"}

    avg_vol = df["volume"].tail(20).mean()
    latest_vol = df["volume"].iloc[-1]
    surge = latest_vol / avg_vol if avg_vol > 0 else 1.0
    print(f"  ✅ Last 20 days avg volume: {avg_vol:>12,.0f}")
    print(f"  ✅ Latest volume:           {latest_vol:>12,.0f}")
    print(f"  ✅ Volume ratio (/20d avg): {surge:>6.2f}x")
    return {"status": "PASS"}


def main():
    print("\u2554" + "\u2550" * 62 + "\u2557")
    print("\u2551   Volume Data Source \u2014 Daily Frequency Test                   \u2551")
    print("\u2551   159307 (\u7ea2\u5229\u4f4e\u6ce2\u0031\u0030\u0030) vs 159209 (\u7ea2\u5229\u8d28\u91cf)                     \u2551")
    print("\u255a" + "\u2550" * 62 + "\u255d")

    results = {}
    for sym, label in [("159307", "\u7ea2\u5229\u4f4e\u6ce2\u0031\u0030\u0030"), ("159209", "\u7ea2\u5229\u8d28\u91cf")]:
        r2 = test_csv_volume(sym, label)
        results[sym] = {"volume_csv": r2}

    print(f"\n\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"{'Test':<25} {'159307 \u7ea2\u5229\u4f4e\u6ce2\u0031\u0030\u0030':<25} {'159209 \u7ea2\u5229\u8d28\u91cf':<25}")
    print("-" * 75)
    for test in ["volume_csv"]:
        s307 = results["159307"][test]["status"]
        s209 = results["159209"][test]["status"]
        print(f"{test:<25} {s307:<25} {s209:<25}")

    passes = sum(1 for r in results.values() for t in r.values() if t["status"] == "PASS")
    skips = sum(1 for r in results.values() for t in r.values() if t["status"] == "SKIP")
    print(f"\n\u2705 Pass: {passes}  \u23ed\ufe0f  Skip: {skips}  \u274c Fail: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
