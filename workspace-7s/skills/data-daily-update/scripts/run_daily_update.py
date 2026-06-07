#!/usr/bin/env python3
"""
run_daily_update.py — OpenClaw entry point for the 7S data-daily-update skill.

Reads active assets from AssetManifest, fetches incremental price data via API,
and appends new rows to each CSV in knowledge/{region}/3_processed/.

Usage (from workspace root):
    python3 skills/data-daily-update/scripts/run_daily_update.py --region cn
    python3 skills/data-daily-update/scripts/run_daily_update.py --region us
    python3 skills/data-daily-update/scripts/run_daily_update.py --region all
"""

import argparse
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WORKSPACE_ROOT))

# Import via sys.path (data-daily-update has dash, not a Python package)
sys.path.insert(0, str(WORKSPACE_ROOT / "skills" / "data-daily-update" / "scripts"))
from cn_daily import DailyUpdateCN
from us_daily import DailyUpdateUS

from dao.asset_dao import AssetManifest
from utils.constants import Region


def _resolve_tr_code(tracks: str, tr_map: dict, csi_patterns: dict) -> str:
    """Resolve tracks to TR code: hard mapping → direct TR check → pattern → tracks as-is."""
    # 1. Hard mapping
    if tracks in tr_map:
        return tr_map[tracks]["tr_code"]

    # 2. Direct TR code — no suffix needed
    if tracks.startswith(("H", "92")) or "CNY" in tracks:
        return tracks

    # 3. Pattern matching (csi_patterns.json)
    for prefix, info in csi_patterns.items():
        if tracks.startswith(prefix):
            candidate = f"{tracks}{info['suffix']}"
            return candidate

    # 4. Use tracks directly as last resort
    return tracks


EXEMPT_TRACKS = {"UNKNOWN", "SOYBEAN_FUTURES", "COMMODITY_INDEX", "NONFERROUS_INDEX",
                  "GOLD_SPOT", "ST_BOND_NAV", "GOV_BOND_NAV"}


def _is_valid_tracks(tracks: str) -> bool:
    """True if tracks is a real CSI index code, not UNKNOWN or an exempt keyword."""
    return bool(tracks) and tracks not in EXEMPT_TRACKS


def _build_asset_db(region: str, symbol: str | None = None, state: str | None = None) -> dict:
    import json
    from pathlib import Path

    assets = AssetManifest().get_by_region(Region[region.upper()])
    root = Path(__file__).resolve().parents[3]
    res_dir = root / "config" / "symbol_resolution"

    tr_map = {}
    tr_map_path = res_dir / "tr_mapping.json"
    if tr_map_path.exists():
        with open(tr_map_path) as f:
            tr_map = json.load(f)

    csi_patterns = {}
    patterns_path = res_dir / "csi_patterns.json"
    if patterns_path.exists():
        with open(patterns_path) as f:
            csi_patterns = json.load(f)

    # Scope to state (active/watchlist/void)
    if state and state != "all":
        state_path = root / "config" / "states" / f"{state}.json"
        if state_path.exists():
            with open(state_path) as f:
                state_data = json.load(f)
            state_symbols = {a["symbol"] for a in state_data.get("assets", [])}
            assets = [a for a in assets if a.symbol in state_symbols]
        else:
            print(f"  ⚠️ State file not found: config/states/{state}.json, using all assets")

    db = {}
    for a in assets:
        tracks = a.tracks or ""
        if _is_valid_tracks(tracks):
            tr_code = _resolve_tr_code(tracks, tr_map, csi_patterns)
            entry = {
                "name": a.name,
                "type": a.asset_type.value,
                "cal_source": {"provider": "中证指数"},
                "tr_index": tr_code,
            }
        else:
            entry = {
                "name": a.name,
                "type": a.asset_type.value,
                "cal_source": {"provider": ""},
                "tr_index": "",
            }
        db[a.symbol] = entry

    if symbol:
        return {k: v for k, v in db.items() if k == symbol}
    return db


def _run_daily_update(region: str, symbol: str | None = None, state: str | None = None) -> int:
    if region == "cn":
        DailyUpdate = DailyUpdateCN
    else:
        DailyUpdate = DailyUpdateUS

    asset_db = _build_asset_db(region, symbol, state)
    result   = DailyUpdate().execute(asset_db)

    status = result["status"].upper()
    print(
        f"  [{region.upper()}] {status} — "
        f"updated: {result['updated_count']}, "
        f"skipped: {result['skipped_count']}, "
        f"failed: {result['failed_count']}"
    )
    for item in result.get("assets_updated", []):
        print(f"  [{region.upper()}] ✅ {item['symbol']} (+{item['new_rows']} rows)")
    for err in result.get("errors", []):
        print(f"  [{region.upper()}] ❌ {err}")

    return 1 if result["failed_count"] > 0 else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="7S daily update pipeline")
    parser.add_argument("--region", choices=["cn", "us", "all"], default="all",
                        help="Region to update (default: all)")
    parser.add_argument("--state", type=str, default="active",
                        choices=["active", "watchlist", "void", "all"],
                        help="State scope: active (default), watchlist, void, or all")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Single symbol to update (skip full region scan)")
    args = parser.parse_args()

    if args.symbol:
        # Single-symbol mode (adhoc): detect region from symbol, update that one
        region = "cn" if args.symbol[:1].isdigit() else "us"
        print(f"\n{'═' * 60}")
        print(f"  Daily Update — {region.upper()} [{args.symbol}]")
        print(f"{'═' * 60}")
        sys.exit(_run_daily_update(region, args.symbol, args.state))
    else:
        regions = ["cn", "us"] if args.region == "all" else [args.region]
        total_failed = 0
        for region in regions:
            print(f"\n{'═' * 60}")
            print(f"  Daily Update — {region.upper()} (state={args.state})")
            print(f"{'═' * 60}")
            total_failed += _run_daily_update(region, state=args.state)
        sys.exit(1 if total_failed else 0)


if __name__ == "__main__":
    main()
