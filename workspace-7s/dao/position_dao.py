"""Position DAO - CRUD for logs/positions/."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from dao.models import Plan, Position


def load_position(
    plan_id: str,
    snapshot_date: Optional[date],
    positions_root: Path,
) -> Position:
    """Load a position snapshot. If snapshot_date is None, returns the latest available."""
    return Position.load(plan_id, snapshot_date, positions_root)


def save_position(position: Position, positions_root: Path):
    """Save a position snapshot."""
    position.save(positions_root)


def list_position_dates(plan_id: str, positions_root: Path) -> list[date]:
    """List all snapshot dates for a plan."""
    pos_dir = positions_root / plan_id
    if not pos_dir.exists():
        return []
    dates = []
    for f in pos_dir.glob("*.json"):
        try:
            dates.append(date.fromisoformat(f.stem))
        except ValueError:
            pass
    return sorted(dates, reverse=True)


def compute_drift(plan: Plan, position: Position) -> list[dict]:
    """Compute drift for each asset: position_weight - target_weight."""
    total = position.total_market_value
    drift_list = []

    for asset in plan.all_assets:
        snap = position.get_snapshot(asset.symbol)
        if snap:
            pos_weight = snap.weight(total)
        else:
            pos_weight = 0.0

        drift = pos_weight - asset.target_weight
        drift_list.append({
            "symbol": asset.symbol,
            "target_weight": asset.target_weight,
            "position_weight": pos_weight,
            "drift": drift,
        })

    return drift_list


def get_action(drift: float, threshold: float) -> str:
    """Return buy/hold/sell based on drift vs threshold."""
    if abs(drift) <= threshold:
        return "hold"
    return "buy" if drift < 0 else "sell"
