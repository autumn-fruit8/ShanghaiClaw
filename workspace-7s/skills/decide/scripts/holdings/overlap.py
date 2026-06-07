"""Holdings overlap engine — cross-reference holdings across ETF positions.

Usage:
    from holdings.overlap import compute_overlap
    overlap = compute_overlap(plan_assets, position, top_n=10)

Returns dict with overlap data for formatting.
"""
from __future__ import annotations

import logging
from typing import Optional

from dao.models import Plan, Position, Holding, HoldingsData
from holdings.fetcher import get_top_holdings  # cache-only; never makes live API calls

logger = logging.getLogger(__name__)

_DEFAULT_CONCENTRATION_THRESHOLD = 5.0  # 5% of portfolio (percentage units)
_DEFAULT_TOP_N = 10


def compute_overlap(
    plan: Plan,
    position: Position,
    top_n: int = _DEFAULT_TOP_N,
    threshold: float = _DEFAULT_CONCENTRATION_THRESHOLD,
) -> dict:
    """Cross-reference top-N holdings across all ETF positions.

    For each ETF position in the plan, fetches its top-N holdings,
    then merges by stock symbol to compute combined portfolio weight.

    Args:
        plan: Plan with asset list
        position: Current position snapshot with market values
        top_n: How many holdings to fetch per ETF (default 10)
        threshold: Combined weight threshold for flagging (default 5%)

    Returns:
        {
            "etf_count": int,
            "holdings_count": int,
            "threshold": float,
            "overlaps": [
                {
                    "symbol": str,
                    "name": str,
                    "combined_weight": float,  # as % of portfolio
                    "flagged": bool,
                    "sources": [
                        {"etf": str, "weight_in_etf": float, "etf_allocation": float},
                    ],
                },
            ],
        }
    """
    total_mv = position.total_market_value
    if total_mv <= 0:
        return {"etf_count": 0, "holdings_count": 0, "threshold": threshold, "overlaps": []}

    # Build etf -> allocation map
    etf_allocations: dict[str, float] = {}
    for snap in position.positions:
        etf_allocations[snap.symbol] = snap.market_value / total_mv

    # Fetch holdings for each ETF
    etf_holdings: dict[str, HoldingsData] = {}
    for asset in plan.all_assets:
        symbol = asset.symbol
        if symbol not in etf_allocations:
            continue
        h_data = get_top_holdings(symbol, top_n=top_n)
        if h_data is not None and h_data.holdings:
            etf_holdings[symbol] = h_data

    # Merge by stock symbol
    stock_map: dict[str, dict] = {}
    for etf_symbol, h_data in etf_holdings.items():
        etf_pct = etf_allocations.get(etf_symbol, 0)
        for h in h_data.holdings:
            # Skip invalid/placeholder symbols (T-bills, cash equivalents, etc.)
            raw_sym = (h.symbol or "").strip()
            if not raw_sym or raw_sym.upper() in ("N/A", "NAN", "CASH", "CASH_US", "CASH_CN", ""):
                continue
            if raw_sym not in stock_map:
                stock_map[raw_sym] = {
                    "symbol": raw_sym,
                    "name": h.name,
                    "combined_weight": 0.0,
                    "sources": [],
                }
            if raw_sym.upper() == etf_symbol.upper():
                continue
            stock_map[raw_sym]["combined_weight"] += h.weight * etf_pct
            stock_map[raw_sym]["sources"].append({
                "etf": etf_symbol,
                "weight_in_etf": h.weight,
                "etf_allocation": etf_pct,
            })

    # Build result list sorted by combined weight descending
    overlaps = sorted(stock_map.values(), key=lambda x: x["combined_weight"], reverse=True)
    for o in overlaps:
        o["flagged"] = o["combined_weight"] >= threshold

    logger.info(
        "Overlap: %d ETFs → %d unique stocks, %d flagged (≥%.1f%%)",
        len(etf_holdings), len(overlaps),
        sum(1 for o in overlaps if o["flagged"]),
        threshold * 100,
    )

    return {
        "etf_count": len(etf_holdings),
        "holdings_count": len(overlaps),
        "threshold": threshold,
        "overlaps": overlaps,
    }
