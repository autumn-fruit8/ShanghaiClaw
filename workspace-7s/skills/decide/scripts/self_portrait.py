"""S5 Self-portrait + Plan CRUD.

Handles:
- Creating / updating / deleting / listing Plans
- Setting target_market_value, target_weight, constraints
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]  # workspace-7s/
SKILL_ROOT = WORKSPACE_ROOT / "skills" / "decide"

# Add paths to sys.path BEFORE imports
sys.path.insert(0, str(WORKSPACE_ROOT))
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(0, str(WORKSPACE_ROOT / "skills" / "analyze" / "scripts"))

from dao.models import Plan, PlanAsset, PlanConstraints
from config import PLANS_DIR, CONFIG_ROOT
from situation import _rolling_trend  # 5Y OLS LDev, same engine as analyze


def _load_catalog() -> set[str]:
    """Return set of valid symbols from asset-master.json."""
    actual_path = CONFIG_ROOT / "assets" / "asset-master.json"
    if not actual_path.exists():
        return set()
    with open(actual_path) as f:
        data = json.load(f)
    return {a["symbol"] for a in data.get("assets", [])}


def _validate_weights(plan: Plan) -> tuple[bool, Optional[str]]:
    total = sum(a.target_weight or 0.0 for a in plan.all_assets)
    if abs(total - 1.0) < 0.01:
        if total != 0:
            for a in plan.all_assets:
                if a.target_weight is not None:
                    a.target_weight = round(a.target_weight / total, 6)
        return True, None
    elif abs(total - 1.0) < 0.001:
        return True, None
    return False, f"Weight sum {total:.4f} ≠ 1.0"


def _validate_symbols(plan: Plan, catalog: set[str]) -> tuple[bool, list[str]]:
    invalid = [a.symbol for a in plan.all_assets if a.symbol not in catalog]
    return len(invalid) == 0, invalid


def _list_versions(plan_id: str) -> list[int]:
    plan_dir = PLANS_DIR / plan_id
    if not plan_dir.exists():
        return []
    versions = []
    for f in plan_dir.glob("v*.json"):
        try:
            versions.append(int(f.stem[1:]))
        except ValueError:
            pass
    return sorted(versions)


# ── Risk-parity weight computation ─────────────────────────────────────────────


def _compute_ldev(csv_path: Path, lookback: int = 1250) -> Optional[float]:
    """Compute latest LDev from total_return CSV using the same 5Y OLS as analyze."""
    try:
        df = pd.read_csv(csv_path)
        vals = df["total_return"].values
        ldev, _, _ = _rolling_trend(vals, len(vals) - 1, lookback=lookback)
        return float(ldev)
    except Exception:
        return None


def _compute_risk_parity(
    plan: Plan,
    lookback_days: int = 1260,
    ldev_tilt: float = 0.0,
    ldev_lookback: int = 1250,
) -> dict:
    """Compute risk-parity target weights, optionally enhanced with LDev conviction.

    Pure risk parity (ldev_tilt=0.0):
        weight_i = (1/vol_i) / sum(1/vol_j)

    Enhanced risk parity (ldev_tilt > 0.0):
        conviction_i = clip(1 - LDev_i × tilt, 0.4, 1.5)
        weight_i = rp_weight_i × conviction_i → normalized

    Args:
        plan: Plan object.
        lookback_days: Trading days for vol computation.
        ldev_tilt: LDev conviction strength (0 = pure RP). Default 0.0.
        ldev_lookback: OLS lookback for LDev (default 1250 = ~5Y).

    Returns:
        {symbol: {"vol": float, "ldev": float|None, "conviction": float, "weight": float}}.
    """
    region = plan.region.lower()
    raw_vol = {}
    raw_ldev = {}

    for asset in plan.all_assets:
        csv_path = WORKSPACE_ROOT / "knowledge" / region / "3_processed" / f"{asset.symbol}.csv"
        if not csv_path.exists():
            print(f"  ⚠ No CSV for {asset.symbol}, treating as unavailable")
            raw_vol[asset.symbol] = None
            continue

        df = pd.read_csv(csv_path)
        log_ret = np.log(df["total_return"]).diff().dropna()
        lr = log_ret.iloc[-lookback_days:] if len(log_ret) >= lookback_days else log_ret
        ann_vol = float(lr.std() * np.sqrt(252))
        raw_vol[asset.symbol] = ann_vol
        print(f"  {asset.symbol}: {len(lr)} trading days, vol={ann_vol:.6f}")

        if ldev_tilt > 0:
            ldev = _compute_ldev(csv_path, ldev_lookback)
            raw_ldev[asset.symbol] = ldev
            ldev_s = f"{ldev:+.2f}σ" if ldev is not None else "N/A"
            print(f"           LDev={ldev_s}")

    # Inverse-vol weights
    valid = {s: v for s, v in raw_vol.items() if v is not None and v > 0}
    if not valid:
        print("  ⚠ No valid volatility data for any asset.")
        return {s: {"vol": None, "ldev": None, "conviction": 1.0, "weight": 0.0} for s in raw_vol}

    inv_vol = {s: 1.0 / v for s, v in valid.items()}
    total_inv = sum(inv_vol.values())
    rp_weights = {s: iv / total_inv for s, iv in inv_vol.items()}

    # Apply LDev conviction tilt
    raw = {}
    for s in valid:
        conv = 1.0
        if ldev_tilt > 0:
            ldev = raw_ldev.get(s)
            if ldev is not None:
                conv = np.clip(1.0 - ldev * ldev_tilt, 0.4, 1.5)
        raw[s] = rp_weights[s] * conv
        raw_ldev[s] = raw_ldev.get(s)

    total_raw = sum(raw.values())
    if total_raw <= 0:
        print("  ⚠ All weights zero after tilt.")
        return {s: {"vol": raw_vol.get(s), "ldev": raw_ldev.get(s), "conviction": 1.0, "weight": 0.0} for s in raw_vol}

    # Round and absorb error into largest weight
    rw = {}
    for s in raw:
        rw[s] = round(raw[s] / total_raw, 6)
    wsum = sum(rw.values())
    if abs(wsum - 1.0) > 1e-9:
        max_s = max(rw, key=rw.get)
        rw[max_s] = round(rw[max_s] + (1.0 - wsum), 6)

    result = {}
    for s in raw_vol:
        ldev = raw_ldev.get(s)
        conv = (raw[s] / rp_weights[s]) if s in valid and rp_weights.get(s, 0) > 0 else 1.0
        result[s] = {
            "vol": raw_vol.get(s),
            "ldev": ldev,
            "conviction": round(conv, 4),
            "weight": rw.get(s, 0.0),
        }

    print(f"\n  Sum: {sum(v['weight'] for v in result.values()):.6f}")
    return result


def cmd_compute_risk_parity(args: argparse.Namespace) -> int:
    """Recompute risk-parity target weights from historical total_return data."""
    plan_id = args.plan_id
    versions = _list_versions(plan_id)
    if not versions:
        sys.exit(f"Error: plan '{plan_id}' not found.")

    version = args.version or max(versions)
    plan = Plan.load(plan_id, version, PLANS_DIR)

    ldev_tilt = args.ldev_tilt or 0.0

    print(f"Computing risk-parity weights for {plan_id} v{version}")
    print(f"  Lookback: {args.lookback} trading days ({args.lookback / 252:.1f} years)")
    print(f"  LDev tilt: {ldev_tilt}" + (f" (conviction = clip(1 - LDev × {ldev_tilt}, 0.4, 1.5))" if ldev_tilt > 0 else " (pure RP)"))
    print(f"  Data source: total_return from knowledge/{plan.region}/3_processed/")
    print()

    rp = _compute_risk_parity(plan, lookback_days=args.lookback, ldev_tilt=ldev_tilt)

    # Update plan assets
    for asset in plan.all_assets:
        info = rp.get(asset.symbol, {})
        asset.target_weight = info.get("weight", 0.0)
        asset.risk_volatility = info.get("vol")

    # Validate weights
    total_w = sum(a.target_weight for a in plan.all_assets)
    if abs(total_w - 1.0) > 0.01:
        sys.exit(f"Error: weight sum {total_w:.4f} ≠ 1.0 after computation.")

    # Save as new version
    new_version = max(versions) + 1
    plan.version = new_version
    plan.save(PLANS_DIR)

    print(f"\nPlan {plan_id} v{version} → v{new_version}:")
    print(f"  {'Symbol':8s} {'Weight':>7s} {'Vol':>8s}  {'LDev':>6s} {'Conv':>5s}")
    print(f"  {'-'*8} {'-'*7} {'-'*8}  {'-'*6} {'-'*5}")
    for a in plan.all_assets:
        info = rp.get(a.symbol, {})
        vol_s = f"{info['vol']:.4f}" if info.get("vol") else "N/A"
        ldev_s = f"{info['ldev']:+.2f}" if info.get("ldev") is not None else "N/A"
        conv_s = f"{info['conviction']:.2f}" if info.get("conviction") else "1.00"
        print(f"  {a.symbol:8s} {a.target_weight:.4f}  {vol_s:>8s}  {ldev_s:>6s} {conv_s:>5s}")

    return 0


# ── CRUD commands ──────────────────────────────────────────────────────────────


def cmd_create(args: argparse.Namespace) -> int:
    catalog = _load_catalog()
    plan_id = args.plan_id
    existing = _list_versions(plan_id)

    if existing and not args.force:
        sys.exit(f"Error: plan '{plan_id}' already exists at v{max(existing)}. Use --force to overwrite.")

    if args.json_input:
        data = json.loads(args.json_input)
    elif args.file:
        with open(args.file) as f:
            data = json.load(f)
    else:
        sys.exit("Error: --create requires --json-input or --file.")

    version = max(existing) + 1 if existing else 1
    plan = Plan.from_dict(plan_id, version, data)
    plan.version = version

    ok, err = _validate_weights(plan)
    if not ok:
        sys.exit(f"Error: {err}")

    ok, invalid = _validate_symbols(plan, catalog)
    if not ok:
        sys.exit(f"Error: unknown symbols: {invalid}")

    plan.save(PLANS_DIR)
    print(f"Created plan '{plan_id}' v{version}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    plan_id = args.plan_id
    versions = _list_versions(plan_id)
    if not versions:
        sys.exit(f"Error: plan '{plan_id}' not found.")

    version = args.version or max(versions)
    if version not in versions:
        sys.exit(f"Error: plan '{plan_id}' has no v{version}. Available: {versions}")

    plan = Plan.load(plan_id, version, PLANS_DIR)
    print(f"=== {plan_id} v{plan.version} ===")
    print(f"Region: {plan.region}  Currency: {plan.currency}")
    print(f"target_market_value: {plan.target_market_value}")
    if plan.constraints:
        print(f"constraints: drift={plan.constraints.drift_threshold}, max_weight={plan.constraints.max_weight}")
    print(f"All assets ({len(plan.all_assets)}):")
    for a in plan.all_assets:
        print(f"  {a.symbol}: weight={a.target_weight}, role={a.role}")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    catalog = _load_catalog()
    plan_id = args.plan_id
    versions = _list_versions(plan_id)
    if not versions:
        sys.exit(f"Error: plan '{plan_id}' not found. Use 'create' instead.")

    current_version = max(versions)
    plan = Plan.load(plan_id, current_version, PLANS_DIR)

    if args.json_input:
        data = json.loads(args.json_input)
    elif args.file:
        with open(args.file) as f:
            data = json.load(f)
    else:
        sys.exit("Error: --update requires --json-input or --file.")

    if "target_market_value" in data:
        plan.target_market_value = data["target_market_value"]
    if "constraints" in data:
        c = data["constraints"]
        plan.constraints = PlanConstraints(
            drift_threshold=c.get("drift_threshold", 0.05),
            max_weight=c.get("max_weight", 0.40),
            min_weight=c.get("min_weight", 0.01),
        )
    if "all_assets" in data:
        plan.all_assets = [
            PlanAsset(
                symbol=a["symbol"],
                name=a.get("name", a["symbol"]),
                role=a.get("role"),
                target_weight=a.get("target_weight", 0.0),
                preferred=a.get("preferred", False),
                risk_volatility=a.get("risk_volatility"),
            )
            for a in data["all_assets"]
        ]

    new_version = current_version + 1
    plan.version = new_version

    ok, err = _validate_weights(plan)
    if not ok:
        sys.exit(f"Error: {err}")

    ok, invalid = _validate_symbols(plan, catalog)
    if not ok:
        sys.exit(f"Error: unknown symbols: {invalid}")

    plan.save(PLANS_DIR)
    print(f"Updated plan '{plan_id}' → v{new_version}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    plan_id = args.plan_id
    versions = _list_versions(plan_id)
    if not versions:
        sys.exit(f"Error: plan '{plan_id}' not found.")

    if not args.force:
        sys.exit("Error: --delete requires --force.")

    if args.version is not None:
        if args.version not in versions:
            sys.exit(f"Error: plan '{plan_id}' has no v{args.version}.")
        (PLANS_DIR / plan_id / f"v{args.version}.json").unlink()
        print(f"Deleted {plan_id} v{args.version}")
    else:
        import shutil
        shutil.rmtree(PLANS_DIR / plan_id)
        print(f"Deleted plan '{plan_id}' (all versions)")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    if not PLANS_DIR.exists():
        print("No plans found.")
        return 0
    plans = sorted([d.name for d in PLANS_DIR.iterdir() if d.is_dir()])
    if not plans:
        print("No plans found.")
        return 0
    print(f"Plans ({len(plans)}):")
    for pid in plans:
        versions = _list_versions(pid)
        latest = max(versions) if versions else "?"
        print(f"  {pid}: v{latest}")
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="S5 Self-portrait — Plan CRUD")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create a new plan")
    p_create.add_argument("--plan-id", required=True)
    g = p_create.add_mutually_exclusive_group(required=True)
    g.add_argument("--json-input", help="Plan JSON string")
    g.add_argument("--file", help="Path to plan JSON file")
    p_create.add_argument("--force", action="store_true")

    p_show = sub.add_parser("show", help="Show plan details")
    p_show.add_argument("--plan-id", required=True)
    p_show.add_argument("--version", type=int, default=None)

    p_update = sub.add_parser("update", help="Update plan (creates new version)")
    p_update.add_argument("--plan-id", required=True)
    g = p_update.add_mutually_exclusive_group(required=True)
    g.add_argument("--json-input")
    g.add_argument("--file", help="Path to plan JSON file")

    p_delete = sub.add_parser("delete", help="Delete a plan or version")
    p_delete.add_argument("--plan-id", required=True)
    p_delete.add_argument("--version", type=int, default=None)
    p_delete.add_argument("--force", action="store_true")

    sub.add_parser("list", help="List all plans")

    p_rp = sub.add_parser("compute-risk-parity", help="Recompute target weights via inverse-vol risk parity")
    p_rp.add_argument("--plan-id", required=True)
    p_rp.add_argument("--version", type=int, default=None, help="Plan version (default: latest)")
    p_rp.add_argument("--lookback", type=int, default=1260, help="Trading days for vol computation (default: 1260 = ~5Y)")
    p_rp.add_argument("--ldev-tilt", type=float, default=0.0, help="LDev conviction tilt (0=pure RP, 0.2=moderate). conviction = clip(1 - LDev × tilt, 0.4, 1.5)")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "create": cmd_create,
        "show": cmd_show,
        "update": cmd_update,
        "delete": cmd_delete,
        "list": cmd_list,
        "compute-risk-parity": cmd_compute_risk_parity,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
