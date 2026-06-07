"""Refresh prices from market data (knowledge CSVs)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]  # workspace-7s/
SKILL_ROOT = WORKSPACE_ROOT / "skills" / "decide"

# Add paths to sys.path BEFORE imports - use TokyoClaw root so 'skills' is importable
sys.path.insert(0, str(WORKSPACE_ROOT.parent))  # TokyoClaw root
sys.path.insert(0, str(WORKSPACE_ROOT))
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from dao.models import Plan, Position
from config import POSITIONS_DIR
from config import PLANS_DIR

KNOWLEDGE_ROOT = WORKSPACE_ROOT / "knowledge"

SKIP_SYMBOLS = {"CASH", "CASH_CN", "CASH_US"}
STALE_THRESHOLD_DAYS = 1


def _knowledge_csv_path(symbol: str, region: str) -> Path:
    slug = region.lower()
    return KNOWLEDGE_ROOT / slug / "3_processed" / f"{symbol}.csv"


def _region_from_plan_id(plan_id: str) -> str:
    if plan_id.startswith("cn_"):
        return "cn"
    elif plan_id.startswith("us_"):
        return "us"
    return "cn"


def get_latest_price(symbol: str, region: str) -> Optional[float]:
    """Read close column (3rd col) from knowledge CSV. Returns None if unavailable."""
    csv_path = _knowledge_csv_path(symbol, region)
    if not csv_path.exists():
        return None
    try:
        with open(csv_path) as f:
            lines = f.readlines()
        if len(lines) < 2:
            return None
        last_row = lines[-1].strip().split(",")
        if len(last_row) >= 3 and last_row[2].strip():
            return float(last_row[2].strip())
        return None
    except (ValueError, IndexError, FileNotFoundError):
        return None


def get_total_return_fallback(symbol: str, region: str) -> Optional[float]:
    """Read total_return (2nd col) as fallback when close unavailable.

    Only usable for asset types where total_return magnitude ≈ close magnitude:
      - CN_OTC (cum NAV 1-4 → unit NAV ≈ same range)
      - US ETFs (normalized to ~1 base, same magnitude as close)
    Guard against CSIndex-calibrated CN ETFs where total_return is an index
    value (1000-35000) that would be catastrophic as a price.
    """
    csv_path = _knowledge_csv_path(symbol, region)
    if not csv_path.exists():
        return None
    try:
        with open(csv_path) as f:
            lines = f.readlines()
        if len(lines) < 2:
            return None
        last_row = lines[-1].strip().split(",")
        if len(last_row) >= 2:
            val = float(last_row[1].strip())
            # Guard: CN assets with total_return > 500 are index-level values
            # (CSIndex index points or OTC cumulative NAV), not prices.
            if region == "cn" and val > 500:
                return None
            return val
        return None
    except (ValueError, IndexError):
        return None


def get_csv_date(symbol: str, region: str) -> Optional[str]:
    csv_path = _knowledge_csv_path(symbol, region)
    if not csv_path.exists():
        return None
    try:
        with open(csv_path) as f:
            return f.readlines()[-1].strip().split(",")[0]
    except:
        return None


def is_stale(symbol: str, region: str) -> bool:
    csv_date_str = get_csv_date(symbol, region)
    if not csv_date_str:
        return True
    try:
        csv_date = datetime.strptime(csv_date_str, "%Y-%m-%d").date()
        return (date.today() - csv_date).days > STALE_THRESHOLD_DAYS
    except:
        return True


def run_daily_update(region: str) -> dict:
    cmd = [
        sys.executable,
        str(WORKSPACE_ROOT / "skills" / "data-daily-update" / "scripts" / "run_daily_update.py"),
        "--region", region
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def refresh_prices(plan_id: str) -> dict:
    """Refresh prices for all plan assets from knowledge CSVs.

    Returns dict with updated/failed counts and price details.
    """
    today = date.today()
    region = _region_from_plan_id(plan_id)

    # Load Plan for all planned assets
    try:
        plan = Plan.load(plan_id, 1, PLANS_DIR)
    except FileNotFoundError:
        return {"error": f"Plan '{plan_id}' not found", "plan_id": plan_id}

    # Load existing position (to preserve existing shares)
    position = Position.load(plan_id, None, POSITIONS_DIR)
    position_shares = {p.symbol: p.shares for p in position.positions}
    position_names = {p.symbol: p.name for p in position.positions}

    plan_symbols = [a.symbol for a in plan.all_assets if a.symbol.upper() not in SKIP_SYMBOLS]
    plan_names = {a.symbol: a.name for a in plan.all_assets}

    # Check for stale data
    stale_symbols = [s for s in plan_symbols if is_stale(s, region)]
    if stale_symbols:
        print(f"  → Data stale for {stale_symbols}, triggering data-daily-update...", file=sys.stderr)
        run_daily_update(region)

    updated = {}
    failed = {}
    daily_update_retried = False

    positions_data = {
        "plan_id": plan_id,
        "plan_version": 1,
        "snapshot_date": today.isoformat(),
        "total_market_value": 0.0,
        "positions": []
    }

    for sym in plan_symbols:
        shares = position_shares.get(sym, 0.0)
        name = position_names.get(sym, plan_names.get(sym, sym))

        price = get_latest_price(sym, region)

        if price is None:
            price = get_total_return_fallback(sym, region)
            if price is not None:
                print(f"  → {sym}: using total_return fallback", file=sys.stderr)

        if price is None and not daily_update_retried:
            print(f"  → Price unavailable for {sym}, re-running daily_update...", file=sys.stderr)
            update_result = run_daily_update(region)
            if update_result["returncode"] == 0:
                price = get_latest_price(sym, region) or get_total_return_fallback(sym, region)
            daily_update_retried = True

        market_value = shares * price if price else 0.0
        positions_data["positions"].append({
            "symbol": sym,
            "name": name,
            "shares": shares,
            "current_price": price or 0.0,
            "market_value": market_value,
        })

        if price:
            updated[sym] = price
        else:
            failed[sym] = "price unavailable after retries"

    positions_data["total_market_value"] = sum(p["market_value"] for p in positions_data["positions"])

    return {
        "plan_id": plan_id,
        "region": region,
        "date": today.isoformat(),
        "updated": len(updated),
        "prices": updated,
        "failed": failed if failed else None,
        "stale_triggered": len(stale_symbols) > 0,
        "daily_update_retried": daily_update_retried,
        "positions_data": positions_data,
    }
