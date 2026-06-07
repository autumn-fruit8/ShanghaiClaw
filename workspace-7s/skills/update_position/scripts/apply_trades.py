"""Apply broker trades from CSV to position snapshot."""

from __future__ import annotations

import csv
import shutil
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]  # workspace-7s/
SKILL_ROOT = WORKSPACE_ROOT / "skills" / "decide"

# Add paths to sys.path BEFORE imports - use TokyoClaw root so 'skills' is importable
sys.path.insert(0, str(WORKSPACE_ROOT.parent))  # TokyoClaw root
sys.path.insert(0, str(WORKSPACE_ROOT))
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from dao.models import Position, PositionSnapshot
from config import PLANS_DIR, POSITIONS_DIR

DEFAULT_CSV = POSITIONS_DIR / "apply_trade.csv"


@dataclass
class BrokerUpdateEntry:
    plan_id: str
    symbol: str
    name: str
    current_shares: float
    new_shares: Optional[float]
    action: str  # "skip" | "update" | "delete"


@dataclass
class ApplyResult:
    skipped: list[tuple[str, str]] = None
    updated: list[tuple[str, float, float]] = None
    deleted: list[str] = None
    errors: list[str] = None
    success: bool = False

    def __post_init__(self):
        if self.skipped is None:
            self.skipped = []
        if self.updated is None:
            self.updated = []
        if self.deleted is None:
            self.deleted = []
        if self.errors is None:
            self.errors = []

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "skipped": [{"symbol": s, "reason": r} for s, r in self.skipped],
            "updated": [{"symbol": s, "old": o, "new": n} for s, o, n in self.updated],
            "deleted": self.deleted,
            "errors": self.errors,
        }


class ValidationError(Exception):
    pass


def validate_file(csv_path: Path) -> list[BrokerUpdateEntry]:
    """Validate CSV and return entries."""
    if not csv_path.exists():
        raise ValidationError(f"File not found: {csv_path}")

    entries = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            plan_id = row.get("plan_id", "").strip()
            symbol = row.get("symbol", "").strip()
            name = row.get("name", "").strip()
            current_str = row.get("current_shares", "").strip()
            new_str = row.get("new_shares", "").strip()

            if not plan_id or not symbol:
                raise ValidationError(f"Missing plan_id or symbol in row: {row}")

            try:
                current_shares = float(current_str) if current_str else 0.0
            except ValueError:
                raise ValidationError(f"Invalid current_shares '{current_str}' for {symbol}")

            if new_str:
                try:
                    new_shares = float(new_str)
                    action = "update" if new_shares != current_shares else "skip"
                except ValueError:
                    raise ValidationError(f"Invalid new_shares '{new_str}' for {symbol}")
            else:
                new_shares = None
                action = "skip"

            entries.append(BrokerUpdateEntry(
                plan_id=plan_id,
                symbol=symbol,
                name=name,
                current_shares=current_shares,
                new_shares=new_shares,
                action=action,
            ))

    return entries


def apply_trades(csv_path: Path, dry_run: bool = False) -> ApplyResult:
    """Apply broker trades from CSV to position snapshots.

    Args:
        csv_path: Path to broker_updates.csv
        dry_run: If True, validate and preview but don't save

    Returns:
        ApplyResult with details of what was/would be changed
    """
    result = ApplyResult()

    try:
        entries = validate_file(csv_path)
    except ValidationError as e:
        result.errors.append(str(e))
        return result

    if not entries:
        result.errors.append("No entries found in CSV")
        return result

    # Group entries by plan_id
    entries_by_plan: dict[str, list[BrokerUpdateEntry]] = defaultdict(list)
    for entry in entries:
        entries_by_plan[entry.plan_id].append(entry)

    # Process each plan
    for plan_id, plan_entries in entries_by_plan.items():
        result = _apply_plan_updates(plan_id, plan_entries, result, dry_run)

    if not dry_run and result.updated:
        _archive_csv(csv_path)

    result.success = True
    return result


def _apply_plan_updates(
    plan_id: str,
    entries: list[BrokerUpdateEntry],
    result: ApplyResult,
    dry_run: bool,
) -> ApplyResult:
    """Apply updates for a single plan."""
    try:
        position = Position.load(plan_id, None, POSITIONS_DIR)
    except Exception as e:
        result.errors.append(f"Failed to load position for '{plan_id}': {e}")
        return result

    current_snapshots = {snap.symbol: snap for snap in position.positions}
    csv_symbols = set()

    for entry in entries:
        csv_symbols.add(entry.symbol)

        if entry.action == "skip":
            result.skipped.append((entry.symbol, "no change"))
            continue

        if entry.symbol not in current_snapshots:
            if entry.action == "update":
                new_snap = PositionSnapshot(
                    symbol=entry.symbol,
                    name=entry.name,
                    shares=entry.new_shares,
                    current_price=0.0,
                    market_value=0.0,
                )
                position.positions.append(new_snap)
                result.updated.append((entry.symbol, 0.0, entry.new_shares))
            else:
                result.skipped.append((entry.symbol, "symbol not in current position"))
            continue

        snap = current_snapshots[entry.symbol]

        if entry.action == "delete":
            position.positions = [p for p in position.positions if p.symbol != entry.symbol]
            result.deleted.append(entry.symbol)
            result.updated.append((entry.symbol, snap.shares, 0.0))
        else:
            old_shares = snap.shares
            snap.shares = entry.new_shares
            snap.market_value = snap.shares * snap.current_price
            result.updated.append((entry.symbol, old_shares, snap.shares))

    # Delete symbols in position but NOT in CSV
    for symbol, snap in current_snapshots.items():
        if symbol not in csv_symbols:
            position.positions = [p for p in position.positions if p.symbol != symbol]
            result.deleted.append(symbol)
            result.updated.append((symbol, snap.shares, 0.0))

    if not dry_run and (result.updated or result.deleted):
        position.snapshot_date = date.today()
        position.save(POSITIONS_DIR)

    return result


def _archive_csv(csv_path: Path) -> Path:
    archive_dir = POSITIONS_DIR / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"apply_trade_{date.today().isoformat()}.csv"
    archive_path = archive_dir / archive_name
    shutil.move(str(csv_path), str(archive_path))
    return archive_path


def export_positions(plan_ids: list[str], output_path: Path = None) -> Path:
    """Export position snapshots to CSV for manual editing."""
    from dao.models import Plan

    if output_path is None:
        output_path = POSITIONS_DIR / "apply_trade.csv"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    SKIP_SYMBOLS = {"CASH", "CASH_CN", "CASH_US"}

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["plan_id", "symbol", "name", "current_shares", "new_shares"])

        for plan_id in plan_ids:
            try:
                plan = Plan.load(plan_id, 1, PLANS_DIR)
            except FileNotFoundError:
                continue

            position = Position.load(plan_id, None, POSITIONS_DIR)
            position_shares = {snap.symbol: snap.shares for snap in position.positions}

            for asset in plan.all_assets:
                if asset.symbol.upper() in SKIP_SYMBOLS:
                    continue
                current_shares = position_shares.get(asset.symbol, 0.0)
                writer.writerow([
                    plan_id,
                    asset.symbol,
                    asset.name or asset.symbol,
                    current_shares,
                    "",
                ])

    return output_path
