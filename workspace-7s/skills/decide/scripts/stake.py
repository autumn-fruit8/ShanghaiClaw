"""S6 Stake — compute drift and output buy/hold/sell recommendations."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]  # workspace-7s/
SKILL_ROOT = WORKSPACE_ROOT / "skills" / "decide"

# Add paths to sys.path BEFORE imports
sys.path.insert(0, str(WORKSPACE_ROOT))
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from dao.models import Plan, Position, PlanPositionView
from config import PLANS_DIR

# Resolve positions root (same pattern as PLANS_DIR)
POSITIONS_DIR = WORKSPACE_ROOT / "logs" / "positions"

def compute_drift(plan: Plan, position: Position) -> list[dict]:
    """Compute weight drift and building progress for each asset."""
    view = PlanPositionView(plan=plan, position=position)
    results = []
    for item in position.positions:
        summary = view.asset_summary(item.symbol)
        results.append({
            "symbol": summary["symbol"],
            "name": summary["name"],
            "shares": summary["shares"],
            "current_price": summary["current_price"],
            "market_value": summary["market_value"],
            "current_weight": summary["relative_weight"],
            "target_weight": summary["target_weight"],
            "drift": summary["drift"],
            "building_progress": summary["building_progress"],
            "funding_gap": summary["funding_gap"],
        })
    return results


def _load_snapshot_signals(snapshot_dir: Path, region: str, snapshot_date: str) -> dict[str, dict]:
    """Load latest signal data from S4 snapshot JSON.

    Returns {symbol: {"type": str, "ldev": float, "indicators": dict}}.
    """
    # Try exact date match, then latest available
    candidates = sorted(snapshot_dir.glob(f"*{region}*.json"), reverse=True)
    if not candidates:
        return {}

    path = candidates[0]
    try:
        with open(path) as f:
            records = json.load(f)
    except Exception:
        return {}

    signals = {}
    for r in records:
        sym = r.get("symbol", "")
        if sym:
            signals[sym] = {
                "type": r.get("type", ""),
                "ldev": r.get("ldev"),
                "rsi": r.get("rsi"),
                "z": r.get("z"),
                "advice": r.get("advice", ""),
                "indicators": r.get("indicators", {}),
            }
    return signals


_VOLATILE_BUY_LDEV = -1.5    # LDev below this → allow buy
_VOLATILE_SELL_LDEV = 2.5    # LDev above this → allow sell
_MOMENTUM_BUY_ROC = 0.0     # ROC above this + price>MA200 → allow buy


def _signal_filtered_action(
    drift: float,
    drift_threshold: float,
    species: str,
    signal: dict | None,
) -> str:
    """Recommend action considering both drift and S4 signals.

    Returns action string: "buy" | "sell" | "hold" | "buy(信号等待)" | "sell(信号等待)"
    """
    base_action = "sell" if drift > drift_threshold else ("buy" if drift < -drift_threshold else "hold")

    if base_action == "hold":
        return "hold"

    if species in ("STEADY", "BOND") or not signal:
        # STEADY/BOND: drift-driven, no signal filtering
        return base_action

    if species == "VOLATILE":
        ldev = signal.get("ldev")
        if ldev is None:
            return base_action
        if base_action == "buy":
            if ldev < _VOLATILE_BUY_LDEV:
                return "buy"
            else:
                return "hold"  # wait for deeper discount
        if base_action == "sell":
            if ldev > _VOLATILE_SELL_LDEV:
                return "sell"
            else:
                return "hold"  # wait for overheat

    if species == "MOMENTUM":
        ind = signal.get("indicators", {})
        roc = ind.get("roc", 0)
        p200 = ind.get("price_above_ma_200", False)
        if base_action == "buy":
            if roc > _MOMENTUM_BUY_ROC and p200:
                return "buy"
            else:
                return "hold"  # wait for trend confirmation
        if base_action == "sell":
            if not (roc > _MOMENTUM_BUY_ROC and p200):
                return "sell"
            else:
                return "hold"  # trend still intact

    return base_action


def parse_args():
    parser = argparse.ArgumentParser(description="S6 Stake — allocation recommendations")
    parser.add_argument("--plan", required=True, help="Plan ID (e.g. cn_hb)")
    parser.add_argument("--version", type=int, default=None, help="Plan version (default: latest)")
    parser.add_argument("--date", default=None, help="Position snapshot date (default: today)")
    parser.add_argument("--output", default=None, help="Write output to file")
    parser.add_argument("--format", default="markdown", choices=["markdown", "json"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    plan_id = args.plan
    if args.date is None:
        snapshot_date = date.today()
    else:
        snapshot_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    snapshot_date_str = snapshot_date.isoformat()

    # Load plan
    versions = _list_versions(plan_id)
    if not versions:
        sys.exit(f"Error: plan '{plan_id}' not found.")
    version = args.version or max(versions)
    if version not in versions:
        sys.exit(f"Error: plan '{plan_id}' has no v{version}.")
    plan = Plan.load(plan_id, version, PLANS_DIR)

    # Load position
    position = Position.load(plan_id, snapshot_date, POSITIONS_DIR)

    # Load S4 snapshot signals (decoupled via JSON file)
    region = plan.region.lower()
    snapshots_dir = WORKSPACE_ROOT / "logs" / "snapshots"
    snapshot_signals = _load_snapshot_signals(snapshots_dir, region, snapshot_date_str)
    if snapshot_signals:
        print(f"  [S4] Loaded signals for {len(snapshot_signals)} assets from snapshot")
    else:
        print(f"  [S4] No snapshot signals found (pure drift mode)")

    # Build species lookup from asset master (PlanAsset has role, not strategy_type)
    try:
        from dao.asset_dao import AssetManifest
        _manifest = AssetManifest()
        _species_map = {a.symbol: a.strategy_type for a in _manifest.get_all()}
    except Exception:
        _species_map = {}

    # Compute drift on-the-fly
    drift_threshold = plan.constraints.drift_threshold if plan.constraints else 0.05
    drifts = compute_drift(plan, position)

    # Build recommendations with signal-aware filtering
    recommendations = []
    for d in drifts:
        strategy_type = _species_map.get(d["symbol"], "")
        signal = snapshot_signals.get(d["symbol"])
        drift_action = "sell" if d["drift"] > drift_threshold else ("buy" if d["drift"] < -drift_threshold else "hold")
        signal_action = _signal_filtered_action(d["drift"], drift_threshold, strategy_type, signal)
        override = signal_action != drift_action

        recommendations.append({
            "symbol": d["symbol"],
            "name": d["name"],
            "action": signal_action,  # signal-filtered action
            "drift_action": drift_action,  # pure drift action (for comparison)
            "signal_override": override,
            "shares": d["shares"],
            "current_price": d["current_price"],
            "market_value": d["market_value"],
            "current_weight": round(d["current_weight"], 4),
            "target_weight": d["target_weight"],
            "drift": round(d["drift"], 4),
            "building_progress": round(d["building_progress"], 4),
            "funding_gap": d["funding_gap"],
            "role": (plan.get_asset(d["symbol"]).role or "ungrouped") if plan.get_asset(d["symbol"]) else "ungrouped",
            "species": strategy_type,
            "signal_ldev": signal.get("ldev") if signal else None,
        })

    result = {
        "plan_id": plan_id,
        "plan_version": version,
        "snapshot_date": snapshot_date_str,
        "total_market_value": position.total_market_value,
        "drift_threshold": drift_threshold,
        "has_signal_filter": bool(snapshot_signals),
        "recommendations": recommendations,
        "generated_at": datetime.now().isoformat(),
    }

    output = json.dumps(result, indent=2, ensure_ascii=False) if args.format == "json" else _render_markdown(result)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Output written to: {args.output}")
    else:
        print(output)
    return 0


def _render_markdown(result: dict) -> str:
    has_filter = result.get("has_signal_filter", False)
    filter_note = " (with S4 signal filter)" if has_filter else " (pure drift, no signal data)"
    lines = [
        f"# Stake — {result['plan_id']} v{result['plan_version']}{filter_note}",
        f"Snapshot: {result['snapshot_date']}  |  Total: {result['total_market_value']:,.2f}  |  Drift threshold: {result['drift_threshold']:.1%}",
        "",
    ]
    if has_filter:
        lines.append("> S4 signal filter active: STEADY/BOND → pure drift | VOLATILE → LDev-gated | MOMENTUM → trend-gated")
        lines.append("")

    lines.append("| Symbol | Action | vs Drift | Signal | LDev | Shares | Price | Market Value | Rel. Weight | Target | Drift | Build Progress | Funding Gap |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in result["recommendations"]:
        act = r["action"]
        drift_act = r.get("drift_action", act)
        override = r.get("signal_override", False)
        act_display = f"**{act}**" if not override else f"~~{drift_act}~~→**{act}**"
        signal_s = r.get("species", "")
        ldev_s = f"{r['signal_ldev']:+.2f}σ" if r.get("signal_ldev") is not None else "—"
        lines.append(
            f"| {r['symbol']} | {act_display} | {drift_act} | {signal_s} | {ldev_s} | {r['shares']:.0f} | {r['current_price']:.4f} | "
            f"{r['market_value']:,.2f} | {r['current_weight']:.2%} | {r['target_weight']:.2%} | {r['drift']:+.2%} | "
            f"{r['building_progress']:.2%} | {r['funding_gap']:+,.2f} |"
        )
    
    # Role-level summary
    role_groups = {}
    for r in result["recommendations"]:
        ro = r.get("role", "ungrouped")
        if ro not in role_groups:
            role_groups[ro] = {"current": 0.0, "target": 0.0, "market_value": 0.0}
        role_groups[ro]["current"] += r["current_weight"]
        role_groups[ro]["target"] += r["target_weight"]
        role_groups[ro]["market_value"] += r["market_value"]
    
    if len(role_groups) > 1:
        lines.append("")
        lines.append("### Role-Level Summary")
        lines.append("")
        lines.append("| Role | Market Value | Current Weight | Target Weight | Drift |")
        lines.append("|---|---:|---:|---:|---:|")
        for ro, data in sorted(role_groups.items()):
            drift = data["current"] - data["target"]
            lines.append(
                f"| {ro} | {data['market_value']:,.2f} | {data['current']:.2%} | {data['target']:.2%} | {drift:+.2%} |"
            )
    
    return "\n".join(lines)


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


if __name__ == "__main__":
    raise SystemExit(main())


# ── Exported API for orchestrator ────────────────────────────────────────────
__all__ = ["compute_drift", "recommend_action", "_signal_filtered_action", "_load_snapshot_signals"]
# recommend_action kept for backward compatibility
def recommend_action(drift: float, drift_threshold: float = 0.05) -> str:
    if drift > drift_threshold:
        return "sell"
    elif drift < -drift_threshold:
        return "buy"
    return "hold"
