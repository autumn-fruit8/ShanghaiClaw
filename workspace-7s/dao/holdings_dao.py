"""Holdings DAO — CRUD for cached ETF holdings data.

Storage: CSV files in HOLDINGS_DIR/{etf_symbol}.csv
Metadata: HOLDINGS_DIR/_meta.json for staleness tracking.
"""
from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from dao.models import Holding, HoldingsData


def _meta_path(holdings_root: Path) -> Path:
    return holdings_root / "_meta.json"


def load_meta(holdings_root: Path) -> dict:
    """Load metadata dict. Returns empty dict if file doesn't exist."""
    path = _meta_path(holdings_root)
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_meta(holdings_root: Path, meta: dict):
    path = _meta_path(holdings_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(meta, f, indent=2)


def load_holdings(etf_symbol: str, holdings_root: Path) -> Optional[list[Holding]]:
    """Load cached holdings CSV for an ETF. Returns None if no cache exists."""
    path = holdings_root / f"{etf_symbol.upper()}.csv"
    if not path.exists():
        return None
    holdings = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                weight = float(row.get("weight", 0) or 0)
            except ValueError:
                weight = 0.0
            holdings.append(Holding(
                symbol=row.get("symbol", ""),
                name=row.get("name", ""),
                weight=weight,
                sector=row.get("sector", ""),
            ))
    return holdings


def save_holdings(etf_symbol: str, holdings: list[Holding], holdings_root: Path):
    """Save holdings as CSV and update metadata."""
    path = holdings_root / f"{etf_symbol.upper()}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["symbol", "name", "weight", "sector"])
        for h in holdings:
            writer.writerow([h.symbol, h.name, h.weight, h.sector])
    meta = load_meta(holdings_root)
    meta[etf_symbol.upper()] = {
        "fetched": date.today().isoformat(),
    }
    save_meta(holdings_root, meta)


def is_cache_stale(etf_symbol: str, holdings_root: Path, max_age_days: int = 30) -> bool:
    """Check if cached data is older than max_age_days."""
    meta = load_meta(holdings_root)
    entry = meta.get(etf_symbol.upper())
    if entry is None:
        return True
    fetched = entry.get("fetched")
    if not fetched:
        return True
    try:
        fetched_date = date.fromisoformat(fetched)
    except (ValueError, TypeError):
        return True
    return (date.today() - fetched_date).days > max_age_days


def save_holdings_with_diff(
    etf_symbol: str,
    holdings: list[Holding],
    holdings_root: Path,
) -> dict:
    """Save holdings + compute diff vs previous snapshot.

    Returns diff dict with fields:
      - new_stocks: list of stock names added
      - removed_stocks: list of stock names removed
      - turnover_pct: percentage of holdings changed
      - prev_count: previous holdings count
      - curr_count: current holdings count
    All fields empty/false for first-time saves.
    """
    old = load_holdings(etf_symbol, holdings_root)
    save_holdings(etf_symbol, holdings, holdings_root)

    diff = {
        "new_stocks": [],
        "removed_stocks": [],
        "turnover_pct": 0,
        "prev_count": 0,
        "curr_count": len(holdings),
    }

    if old:
        old_names = {h.name for h in old if h.name}
        new_names = {h.name for h in holdings if h.name}

        added = new_names - old_names
        removed = old_names - new_names
        total = len(old_names | new_names)

        diff["new_stocks"] = sorted(added)
        diff["removed_stocks"] = sorted(removed)
        diff["prev_count"] = len(old)
        diff["turnover_pct"] = round((len(added) + len(removed)) / total * 100, 1) if total > 0 else 0

    # Update _meta.json with diff
    meta = load_meta(holdings_root)
    meta[etf_symbol.upper()] = {
        "fetched": date.today().isoformat(),
        "prev_fetched": meta.get(etf_symbol.upper(), {}).get("fetched"),
        "holdings_count": len(holdings),
    }
    if diff["turnover_pct"] > 0:
        meta[etf_symbol.upper()].update({
            "turnover_pct": diff["turnover_pct"],
            "new_stocks": diff["new_stocks"],
            "removed_stocks": diff["removed_stocks"],
        })
    save_meta(holdings_root, meta)

    return diff


def list_cached_etfs(holdings_root: Path) -> list[str]:
    """List all ETF symbols with cached holdings."""
    meta = load_meta(holdings_root)
    return sorted(meta.keys())
