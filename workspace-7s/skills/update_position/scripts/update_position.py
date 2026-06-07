"""Update Position — Orchestrator for refreshing prices and applying broker trades.

Usage:
    update_position.py                 # Do all: refresh prices + apply trades
    update_position.py refresh         # Refresh prices only
    update_position.py apply           # Apply broker trades from CSV
    update_position.py export          # Export position to CSV for editing
    update_position.py check           # Check Plan vs Position integrity
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]  # workspace-7s/
SKILL_ROOT = WORKSPACE_ROOT / "skills" / "decide"

# Add paths to sys.path BEFORE imports - use TokyoClaw root so 'skills' is importable
sys.path.insert(0, str(WORKSPACE_ROOT.parent))  # TokyoClaw root
sys.path.insert(0, str(WORKSPACE_ROOT))
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from dao.models import Plan, Position
from config import PLANS_DIR, POSITIONS_DIR

DEFAULT_CSV = POSITIONS_DIR / "apply_trade.csv"


def cmd_refresh(plan_ids: list[str], dry_run: bool = False) -> int:
    """Refresh prices from market data for all plan assets."""
    try:
        from .refresh_prices import refresh_prices
    except ImportError:
        from refresh_prices import refresh_prices  # noqa: F401

    results = []
    for plan_id in plan_ids:
        print(f"\n=== Refreshing prices for {plan_id} ===")
        result = refresh_prices(plan_id)
        results.append(result)

        if "error" in result:
            print(f"  ERROR: {result['error']}")
            continue

        print(f"  Updated {result['updated']} assets")
        for sym, price in result.get("prices", {}).items():
            print(f"    {sym}: {price}")

        # Write snapshot if not dry run
        if not dry_run:
            positions_data = result.get("positions_data", {})
            if positions_data.get("positions"):
                new_file = POSITIONS_DIR / plan_id / f"{result['date']}.json"
                new_file.parent.mkdir(parents=True, exist_ok=True)
                with open(new_file, "w") as f:
                    json.dump(positions_data, f, indent=2, ensure_ascii=False)
                print(f"  Saved to: {new_file}")

    if len(results) == 1:
        print(json.dumps(results[0], indent=2, default=str))
    else:
        print(json.dumps(results, indent=2, default=str))
    return 0


def cmd_apply(csv_path: Path, dry_run: bool = False) -> int:
    """Apply broker trades from CSV to position snapshots."""
    try:
        from .apply_trades import apply_trades, ValidationError
    except ImportError:
        from apply_trades import apply_trades, ValidationError  # noqa: F401

    if dry_run:
        print("[DRY RUN] Validating and previewing changes...")

    result = apply_trades(csv_path, dry_run=dry_run)

    if result.errors:
        for err in result.errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    if dry_run:
        print("\n[DRY RUN] Would make these changes:")
    else:
        print("\nChanges applied:")

    if result.updated:
        print("\n  Updated:")
        for sym, old, new in result.updated:
            print(f"    {sym}: {old} -> {new}")

    if result.deleted:
        print("\n  Deleted:")
        for sym in result.deleted:
            print(f"    {sym}")

    if result.skipped:
        print("\n  Skipped (no change):")
        for sym, reason in result.skipped:
            print(f"    {sym}: {reason}")

    if not result.updated and not result.deleted:
        print("\nNo changes to apply.")

    if not dry_run:
        print(f"\nNew snapshot(s) saved with today's date.")
        print(f"CSV archived to: archive/apply_trade_{date.today().isoformat()}.csv")

    return 0


def cmd_export(plan_ids: list[str]) -> int:
    """Export position(s) to CSV for manual editing."""
    try:
        from .apply_trades import export_positions
    except ImportError:
        from apply_trades import export_positions  # noqa: F401

    if not plan_ids:
        # Export all plans
        plan_ids = [d.name for d in POSITIONS_DIR.iterdir() if d.is_dir() and d.name != "archive"]

    output_path = export_positions(plan_ids, output_path=DEFAULT_CSV)
    print(f"Exported to: {output_path}")
    print(f"Open this file in Excel, edit 'new_shares' column, save, then run apply.")
    return 0


def cmd_check(plan_id: str = None, version: int = 1, all_plans: bool = False) -> int:
    """Check Plan assets vs Position assets consistency."""
    if all_plans:
        results = []
        for plan_dir in PLANS_DIR.iterdir():
            if plan_dir.is_dir():
                versions = []
                for f in plan_dir.glob("v*.json"):
                    try:
                        versions.append(int(f.stem[1:]))
                    except ValueError:
                        pass
                if versions:
                    results.append(_check_integrity(plan_dir.name, max(versions)))
    else:
        results = [_check_integrity(plan_id, version)]

    print_check_report(results)
    return 0


def _check_integrity(plan_id: str, version: int = 1) -> dict:
    """Check integrity for one plan."""
    errors, warnings = [], []

    try:
        plan = Plan.load(plan_id, version, PLANS_DIR)
    except FileNotFoundError:
        return {"plan_id": plan_id, "error": f"Plan v{version} not found", "pass": False}

    pos_dir = POSITIONS_DIR / plan_id
    if not pos_dir.exists():
        return {"plan_id": plan_id, "error": f"No positions directory", "pass": False}

    position_files = sorted(pos_dir.glob("*.json"), reverse=True)
    if not position_files:
        return {"plan_id": plan_id, "error": "No position files found", "pass": False}

    snapshot_date = position_files[0].stem
    try:
        from datetime import datetime
        snapshot_dt = datetime.strptime(snapshot_date, "%Y-%m-%d").date()
        position = Position.load(plan_id, snapshot_dt, POSITIONS_DIR)
    except FileNotFoundError:
        return {"plan_id": plan_id, "error": f"Position file not found", "pass": False}

    plan_symbols = {a.symbol for a in plan.all_assets}
    pos_symbols = {p.symbol for p in position.positions}

    missing_in_pos = plan_symbols - pos_symbols
    if missing_in_pos:
        warnings.append(f"Assets in plan but not in positions: {missing_in_pos}")

    extra_in_pos = pos_symbols - plan_symbols
    if extra_in_pos:
        warnings.append(f"Assets in positions but not in plan: {extra_in_pos}")

    if position.positions:
        total = sum(p.market_value for p in position.positions)
        if plan.target_market_value and abs(total - plan.target_market_value) / plan.target_market_value > 0.05:
            warnings.append(f"Position total {total:,.2f} differs from target {plan.target_market_value:,.2f}")

    return {
        "plan_id": plan_id,
        "version": version,
        "snapshot_date": snapshot_date,
        "plan_assets": len(plan_symbols),
        "position_assets": len(pos_symbols),
        "total_market_value": sum(p.market_value for p in position.positions),
        "errors": errors,
        "warnings": warnings,
        "pass": len(errors) == 0,
    }


def print_check_report(results: list[dict]):
    print("# Integrity Check Report\n")
    all_pass = True
    for r in results:
        status = "PASS" if r["pass"] else "FAIL"
        print(f"## {r['plan_id']} v{r.get('version','?')} — {status}")
        if r.get("snapshot_date"):
            print(f"- Snapshot: {r['snapshot_date']}")
        print(f"- Plan assets: {r['plan_assets']}, Position assets: {r['position_assets']}")
        print(f"- Total market value: {r.get('total_market_value', 0):,.2f}")
        if r.get("warnings"):
            for w in r["warnings"]:
                print(f"  ⚠ {w}")
        if r.get("errors"):
            all_pass = False
            for e in r["errors"]:
                print(f"  ✗ {e}")
        print()
    print("All passed" if all_pass else "Some checks failed.")


def cmd_all(plan_ids: list[str], dry_run: bool = False) -> int:
    """Do everything: apply trades first, then refresh prices."""
    csv_path = DEFAULT_CSV

    # Step 1: Apply trades if CSV exists
    if csv_path.exists():
        print("\n=== Step 1: Applying broker trades ===")
        cmd_apply(csv_path, dry_run=dry_run)
    else:
        print("\n=== Step 1: No apply_trade.csv found, skipping trades ===")

    # Step 2: Refresh prices
    print("\n=== Step 2: Refreshing prices ===")
    return cmd_refresh(plan_ids, dry_run=dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Update Position — refresh prices and apply broker trades",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  update_position.py                 # Do all: apply trades + refresh prices
  update_position.py refresh --plan cn_hb us_hb   # Refresh prices only
  update_position.py apply           # Apply broker trades from CSV
  update_position.py export          # Export all plans to CSV
  update_position.py export --plan cn_hb  # Export specific plans
  update_position.py check --plan cn_hb   # Check integrity
  update_position.py check --all         # Check all plans
        """
    )
    sub = parser.add_subparsers(dest="command", required=False)

    # Default: do all
    p_all = sub.add_parser("refresh", help="Refresh prices from market data")
    p_all.add_argument("--plan", nargs="+", required=True, help="Plan ID(s)")
    p_all.add_argument("--dry-run", action="store_true")

    # Apply trades
    p_apply = sub.add_parser("apply", help="Apply broker trades from CSV")
    p_apply.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    p_apply.add_argument("--dry-run", action="store_true")

    # Export
    p_export = sub.add_parser("export", help="Export position(s) to CSV")
    p_export.add_argument("--plan", nargs="*", help="Plan ID(s) (default: all)")

    # Check
    p_check = sub.add_parser("check", help="Check Plan vs Position integrity")
    p_check.add_argument("--plan", help="Plan ID")
    p_check.add_argument("--version", type=int, default=1)
    p_check.add_argument("--all", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Default command: do all
    if args.command is None:
        plan_ids = [d.name for d in POSITIONS_DIR.iterdir() if d.is_dir() and d.name != "archive"]
        if not plan_ids:
            print("No plans found.", file=sys.stderr)
            return 1
        return cmd_all(plan_ids, dry_run=False)

    if args.command == "refresh":
        return cmd_refresh(args.plan, args.dry_run)

    if args.command == "apply":
        return cmd_apply(args.csv, args.dry_run)

    if args.command == "export":
        plan_ids = args.plan or []
        return cmd_export(plan_ids)

    if args.command == "check":
        return cmd_check(args.plan, args.version, args.all)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
