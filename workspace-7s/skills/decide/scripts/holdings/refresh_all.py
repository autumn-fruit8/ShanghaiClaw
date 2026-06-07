"""Refresh holdings cache for all ETFs across all plans.

Run this on the server periodically (weekly cron) or after deployment.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure workspace root is on path
_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_WORKSPACE_ROOT))

from config import PLANS_DIR, HOLDINGS_DIR
from holdings.fetcher import refresh_holdings
from dao.holdings_dao import list_cached_etfs, load_meta


def get_all_plan_etfs() -> set[str]:
    """Collect all ETF symbols from all plan configs."""
    etfs = set()
    for plan_dir in PLANS_DIR.iterdir():
        if not plan_dir.is_dir():
            continue
        for vfile in plan_dir.glob("v*.json"):
            import json
            with open(vfile) as f:
                data = json.load(f)
            for asset in data.get("all_assets", []):
                etfs.add(asset["symbol"])
    return etfs


def main():
    print("=== Holdings Cache Refresh ===")
    print(f"Cache dir: {HOLDINGS_DIR}")

    etfs = get_all_plan_etfs()
    print(f"Found {len(etfs)} ETFs across all plans: {', '.join(sorted(etfs))}")
    print()

    success = 0
    failed = 0
    for etf in sorted(etfs):
        print(f"  {etf}...", end=" ", flush=True)
        result = refresh_holdings(etf, top_n=10)
        if result is not None and result.holdings:
            print(f"✅ {len(result.holdings)} holdings from {result.source}")
            success += 1
        else:
            print("⚠️  no data")
            failed += 1

    print()
    print(f"Done: {success} refreshed, {failed} skipped")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
