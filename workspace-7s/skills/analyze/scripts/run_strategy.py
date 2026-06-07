"""
run_strategy.py — CLI entry point for the 7S S4 strategy module.

Signal-only by default. Use --backtest to also run the 10-year simulation.

Usage:
    # Signals only (daily cron / adhoc default)
    python run_strategy.py --region cn
    python run_strategy.py --region us --date 2026-03-08

    # Signals + backtest (adhoc deep-dive)
    python run_strategy.py --region cn --backtest

    # Full portfolio
    python run_strategy.py --region all
"""

import argparse
import json
import math
import os
import sys
from datetime import date
from pathlib import Path

# Allow import from workspace root
_THIS = Path(__file__).resolve()
_WORKSPACE_ROOT = _THIS.parents[3]             # workspace-7s root
_RUNTIME_ROOT = Path(os.getenv("SEVENS_RUNTIME_ROOT", "")).expanduser() if os.getenv("SEVENS_RUNTIME_ROOT") else None
sys.path.insert(0, str(_WORKSPACE_ROOT))

from config import SNAPSHOTS_DIR, BACKTEST_DIR
from dao.asset_dao import AssetManifest
from dao.config_dao import ConfigLoader
from utils.constants import Strategy

import pandas as pd


# ---------------------------------------------------------------------------
# New pipeline: analyze with Species→Strategy routing
# ---------------------------------------------------------------------------

_SPECIES_STRATEGY_CACHE = {}


def _ensure_strategy_registry():
    """Lazy-load registry singleton for strategy resolution."""
    if "registry" not in _SPECIES_STRATEGY_CACHE:
        from skills.analyze.scripts.s4_strategy.registry import StrategyRegistry
        _SPECIES_STRATEGY_CACHE["registry"] = StrategyRegistry()
    return _SPECIES_STRATEGY_CACHE["registry"]


def _resolve_strategy_name(species: str, symbol: str = "") -> str:
    """Resolve species→strategy label, with optional asset-aware routing.

    Returns a human-readable descriptor like 'momentum+trend' or '7s-base+dca'.
    """
    try:
        registry = _ensure_strategy_registry()
        if symbol:
            s = registry.resolve_strategy_for_asset(symbol, species)
        else:
            s = registry.resolve_strategy_for(species)
        pn = s.profile.get("name", "?")
        tn = s.tactic.get("name", "?")
        return f"{pn}+{tn}"
    except Exception:
        return "7s-base+dca"


def _print_advice_table(results: list[dict]) -> None:
    """Print signal table with scenario-based advice (no position data).
    'Advice' column shows conditional text: "若持仓: ...；若空仓: ...".
    Pure Layer 2 stateless output — never reads position files."""
    header = "| Symbol | Name | Pulse | Signal | Alignment | Advice | Key Indicators |"
    sep = "|" + "|".join(["-" * max(8, len(h)) for h in header.split("|")[1:-1]]) + "|"
    print(); print(header); print(sep)
    for r in results:
        m = r["meta"]
        species = m.get("Type", "")
        advice = m.get("advice", "")
        ind = m.get("indicators", {})
        sleeve_val = r.get("sleeve", "")

        pulse = m.get("Pulse", r.get("pulse_type", "N/A"))
        alignment = m.get("Alignment", "NEUTRAL")
        sig_short = m["Signal"].split("]")[0].lstrip("[") if "]" in m["Signal"] else m["Signal"]

        # Alignment emoji
        align_emoji = {"CONFIRMED": "✅", "DIVERGENT": "⚠️", "NEUTRAL": "—"}
        align_display = f"{align_emoji.get(alignment, '')} {alignment}"

        # Build indicator string (use actual strategy profile, not species)
        has_momentum = "adx" in ind
        if has_momentum:
            ind_str = f"ROC={ind.get('roc', 0):+.1f}%  ADX={ind.get('adx', 0):.0f}"
            mac = ind.get("ma_cross", 0)
            if mac != 0:
                ind_str += f"  cross={'golden' if mac > 0 else 'death'}"
            p200 = ind.get("price_above_ma_200", None)
            if p200 is not None:
                ind_str += f"  >MA200={'Y' if p200 else 'N'}"
            # Volume signal (if computed by momentum + volume tactic)
            vsig = ind.get("vol_signal", "")
            if vsig and vsig not in ("", "N/A"):
                ind_str += f"  vol={vsig}"
        else:
            ind_str = f"LDev={ind.get('ldev', 0):+.2f}σ  RSI={ind.get('rsi', 0):.0f}"

        row = [r["symbol"], r.get("name", ""), pulse, sig_short, align_display, advice, ind_str]
        print("| " + " | ".join(str(v) for v in row) + " |")

def _load_price_data(symbol: str, region: str) -> pd.DataFrame | None:
    """
    Load processed price CSV for a symbol — cache-first resolution.
    Searches: adhoc/cache/{symbol}.csv (SSOT)
              knowledge/{region}/3_processed/{symbol}.csv
              knowledge/{region}/prices/{symbol}.csv (legacy)
    """
    candidates = [
        _WORKSPACE_ROOT / "adhoc" / "cache" / f"{symbol}.csv",
        (_RUNTIME_ROOT or _WORKSPACE_ROOT) / "knowledge" / region / "3_processed" / f"{symbol}.csv",
        (_RUNTIME_ROOT or _WORKSPACE_ROOT) / "knowledge" / region / "prices" / f"{symbol}.csv",
    ]
    for path in candidates:
        if path.exists():
            try:
                df = pd.read_csv(path)
                if "date" not in df.columns:
                    df.columns = df.columns.str.lower()
                return df
            except Exception as e:
                print(f"  [WARN] Failed to read {path}: {e}")
    return None


def _print_signal_table(results: list[dict]) -> None:
    """Print a Markdown signal summary table to stdout (no backtest metrics)."""
    header = (
        "| Symbol | Name | Type | Pulse | Signal | LDev | RSI | PE%ile | Div%ile |"
    )
    sep = "|" + "|".join(["-" * 8] * 9) + "|"
    print()
    print(header)
    print(sep)
    for r in results:
        m = r["meta"]
        sig_short = m["Signal"].split("]")[0].lstrip("[") if "]" in m["Signal"] else m["Signal"]
        last_row = r["data"].iloc[-1] if r.get("data") is not None and not r["data"].empty else {}

        pulse = m.get("Pulse", r.get("pulse_type", "NEUTRAL"))

        pe_pctile = ""
        div_pctile = ""
        try:
            from skills.analyze.scripts.s4_strategy.valuation import get_valuation
            val = get_valuation(r["symbol"])
            if val:
                pp = val.get("pe_pctile")
                if pp is not None:
                    pe_pctile = f"{pp:.0f}%"
                dp = val.get("div_pctile")
                if dp is not None:
                    div_pctile = f"{dp:.0f}%"
        except Exception:
            pass
        ldev = last_row.get("log_dev", 0)
        rsi  = last_row.get("rsi", 0)
        row = [
            r["symbol"],
            r.get("name", ""),
            m["Type"],
            pulse,
            sig_short,
            f"{ldev:.2f}" if isinstance(ldev, float) else str(ldev),
            f"{rsi:.0f}" if isinstance(rsi, float) else str(rsi),
            pe_pctile if pe_pctile else "—",
            div_pctile if div_pctile else "—",
        ]
        print("| " + " | ".join(str(v) for v in row) + " |")


def _print_backtest_table(results: list[dict]) -> None:
    """Print a Markdown backtest summary table."""
    # Use first result's backtest period (should be same for all)
    first_meta = results[0]["meta"]
    bt_period = ""
    if "Backtest_Years" in first_meta:
        bt_start = first_meta.get("Backtest_Period_Start", "?")
        bt_end   = first_meta.get("Backtest_Period_End", "?")
        bt_years = first_meta.get("Backtest_Years", 0)
        bt_period = f" ({bt_years:.1f}yr: {bt_start} to {bt_end})"

    header = (
        "| Symbol | Name | Type | Signal | LDev | RSI | "
        f"Strat Ret{bt_period} | Strat DD | BH Ret | BH DD | Sharpe |"
    )
    sep = "|" + "|".join(["-" * max(3, len(h)) for h in header.split("|")[1:-1]]) + "|"
    print()
    print(header)
    print(sep)
    for r in results:
        m = r["meta"]
        sig_short = m["Signal"].split("]")[0].lstrip("[") if "]" in m["Signal"] else m["Signal"]
        last_row  = r["data"].iloc[-1] if r.get("data") is not None and not r["data"].empty else {}
        ldev = last_row.get("log_dev", 0)
        rsi  = last_row.get("rsi", 0)
        row = [
            r["symbol"],
            r.get("name", ""),
            m["Type"],
            sig_short,
            f"{ldev:.2f}" if isinstance(ldev, float) else str(ldev),
            f"{rsi:.0f}" if isinstance(rsi, float) else str(rsi),
            f"{m.get('Strategy_Ret', 0):.1%}",
            f"{m.get('Strategy_DD', 0):.1%}",
            f"{m.get('BuyHold_Ret', 0):.1%}",
            f"{m.get('BuyHold_DD', 0):.1%}",
            f"{m.get('Strat_Sharpe', 0):.2f}",
        ]
        print("| " + " | ".join(str(v) for v in row) + " |")


def _save_snapshot(results: list[dict], region: str, run_date: str) -> None:
    """Write snapshot JSON for downstream consumers (e.g. view_report).
    When backtest was NOT run, backtest fields are omitted.
    When backtest WAS run, all fields are included.
    """
    out_dir = (_RUNTIME_ROOT / "logs" / "snapshots") if _RUNTIME_ROOT else SNAPSHOTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_date}_{region}.json"

    compact = []
    for r in results:
        m = r["meta"]
        last = r["data"].iloc[-1] if r.get("data") is not None and not r["data"].empty else {}

        entry = {
            "symbol": r["symbol"],
            "name":   r.get("name", ""),
            "description": r.get("description", ""),
            "region": r.get("region", region),
            "signal": m.get("Signal", ""),
            "signal_action": m.get("Signal_Action", ""),
            "signal_type": last.get("signal_type", ""),
            "signal_desc": last.get("signal_desc", ""),
            "pulse": m.get("Pulse", r.get("pulse_type", "NEUTRAL")),
            "type": m.get("Type", ""),
            "strategy": m.get("strategy", r.get("strategy_name", "")),
            "tactic": r.get("tactic_name", ""),
            "advice": m.get("advice", ""),
            "alignment": m.get("Alignment", "NEUTRAL"),
            # Snapshot indicators (latest row)
            "ldev": float(last.get("log_dev", last.get("ldev", 0))) if not math.isnan(float(last.get("log_dev", last.get("ldev", 0)))) else None,
            "rsi":  float(last.get("rsi", 0))  if not math.isnan(float(last.get("rsi", 0))) else None,
            "z":    float(last.get("z_score", last.get("zscore", 0))) if not math.isnan(float(last.get("z_score", last.get("zscore", 0)))) else None,
            "ma60_pct": float(last.get("ma60_pct", 0)) if not math.isnan(float(last.get("ma60_pct", 0))) else None,
            # Volume signal (structured)
            "vol_ratio": float(last.get("vol_ratio", 0)) if not math.isnan(float(last.get("vol_ratio", 0))) else None,
            "vol_signal": last.get("vol_signal", ""),
            "vol_memo": last.get("vol_memo", ""),
        }

        # Species-specific indicators from new pipeline
        indicators = m.get("indicators", {})
        if indicators:
            entry["indicators"] = indicators

        # Backtest fields (only present when --backtest was run)
        if "Strategy_Ret" in m:
            entry.update({
                "strategy_ret": m.get("Strategy_Ret"),
                "strategy_dd": m.get("Strategy_DD"),
                "strat_vol": m.get("Strat_Vol"),
                "strat_sharpe": m.get("Strat_Sharpe"),
                "buyhold_ret": m.get("BuyHold_Ret"),
                "buyhold_dd": m.get("BuyHold_DD"),
                "buyhold_vol": m.get("BuyHold_Vol"),
                "buyhold_sharpe": m.get("BuyHold_Sharpe"),
                "stats_info": m.get("Stats_Info", ""),
                "trades_count": len(r.get("trades", [])),
                "backtest_period_start": m.get("Backtest_Period_Start", "?"),
                "backtest_period_end": m.get("Backtest_Period_End", "?"),
                "backtest_years": m.get("Backtest_Years", 0),
            })
            if "backtest_date" in m:
                entry["backtest_date"] = m["backtest_date"]

        # Valuation: PE + dividend yield percentile (CN STEADY/BOND only)
        try:
            from skills.analyze.scripts.s4_strategy.valuation import get_valuation
            val = get_valuation(r["symbol"])
            if val:
                if val.get("pe") is not None:
                    entry["pe"] = val["pe"]
                    entry["pe_pctile"] = val.get("pe_pctile")
                if val.get("div_yield") is not None:
                    entry["div_yield"] = val["div_yield"]
                    entry["div_pctile"] = val.get("div_pctile")
        except Exception:
            pass

        compact.append(entry)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(compact, f, ensure_ascii=False, indent=2)
    print(f"\nSnapshot saved -> {path}")




# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(region: str, run_date: str, with_backtest: bool = False, sleeve: str | None = None) -> None:
    ConfigLoader()   # ensure singleton is initialised

    manifest_path = os.getenv("SEVENS_TEMP_ASSET_MANIFEST") or os.getenv("SEVENS_MANIFEST_PATH")
    if manifest_path:
        # Adhoc mode: read the manifest JSON to get only the resolved symbols
        import json as _json
        import pathlib
        m_path = pathlib.Path(manifest_path)
        if m_path.exists():
            with open(m_path) as f:
                manifest_data = _json.load(f)
            manifest_symbols = {a["symbol"] for a in manifest_data.get("assets", [])}
            manifest = AssetManifest()
            all_assets = manifest.get_all()
            assets = [a for a in all_assets if a.symbol in manifest_symbols]
            region_total = len(manifest_symbols)
            print(f"  [Adhoc] Resolved {len(assets)}/{region_total} assets from manifest")
        else:
            print(f"  [WARN] Manifest not found: {manifest_path}, falling back to all")
            manifest = AssetManifest()
            assets = manifest.get_all()
    else:
        # Cron mode: always active state only
        # State files are symbol-only (SSOT = asset master); look up region from master.
        import json
        from pathlib import Path
        _workspace = Path(__file__).resolve().parents[3]
        active_path = _workspace / "config" / "states" / "active.json"
        active_symbols: set[str] = set()
        if active_path.exists():
            with open(active_path) as f:
                active_data = json.load(f)
            for a in active_data.get("assets", []):
                sym = str(a.get("symbol", "")).strip()
                if sym:
                    active_symbols.add(sym)

        manifest = AssetManifest()
        all_assets = manifest.get_all()
        # Build symbol→region lookup from asset master (single source of truth)
        region_map = {a.symbol: a.region.upper() for a in all_assets}
        target_region = region.upper()
        assets = [
            a for a in all_assets
            if a.symbol in active_symbols and region_map.get(a.symbol) == target_region
        ]
        region_total = sum(1 for a in all_assets if region_map.get(a.symbol) == target_region)
        active_count = len(assets)
        if active_count < region_total:
            print(f"  [Cron] Filtered to {active_count}/{region_total} active assets")

    # Filter by category (sleeve from asset master)
    if sleeve:
        filtered = [a for a in assets if getattr(a, "sleeve", "").lower() == sleeve.lower()]
        if not filtered:
            print(f"  [WARN] No {sleeve} assets found in active state")
        else:
            print(f"  [Filter] Showing {len(filtered)}/{len(assets)} {sleeve} assets")
        assets = filtered

    if not assets:
        print(f"[WARN] No assets found for region={region!r}")
        return

    mode_str = "signals + backtest" if with_backtest else "signals only"
    print(f"\nRunning strategy engine — region={region}, date={run_date} ({mode_str})")
    print(f"Assets: {len(assets)}\n")

    results = []

    for asset in assets:
        sym   = asset.symbol
        name  = asset.name
        desc  = asset.description
        species = getattr(asset, "strategy_type", "STEADY")
        rgn = "us" if region.upper() == "US" else "cn"
        asset_sleeve = getattr(asset, "sleeve", "")
        asset_tags = getattr(asset, "tags", [])
        asset_meta = {"symbol": sym, "name": name, "description": desc,
                      "strategy_class": species, "sleeve": asset_sleeve,
                      "tags": list(asset_tags), "region": rgn}

        df = _load_price_data(sym, rgn)
        if df is None:
            print(f"  [SKIP] {sym}: no price data found")
            continue

        symbol = asset_meta.get("symbol", "")
        strategy_name = _resolve_strategy_name(species, symbol)

        if with_backtest:
            from skills.analyze.scripts.s4_strategy.pipeline import run_strategy_pipeline
            result = run_strategy_pipeline(
                df, {**asset_meta},
                strategy_name=strategy_name,
                backtest_years=10,
            )
        else:
            from skills.analyze.scripts.s4_strategy.pipeline import run_analyze_pipeline
            result = run_analyze_pipeline(
                df, {**asset_meta},
                strategy_name=strategy_name,
            )

        if result is None:
            print(f"  [SKIP] {sym}: insufficient history or data error")
            continue

        result["region"] = rgn
        result["description"] = desc
        result["sleeve"] = getattr(asset, "sleeve", "")
        results.append(result)
        sig = result["meta"]["Signal"]
        print(f"  [{sym:>8}] {sig}")
        print(f"            Data: {df['date'].min()} → {df['date'].max()}")

    if not results:
        print("\nNo results produced.")
        return

    if with_backtest:
        _print_backtest_table(results)
    else:
        # BOND species uses old engine → old table; others use new table
        has_advice = any("advice" in r.get("meta", {}) for r in results)
        if has_advice:
            _print_advice_table(results)
        else:
            _print_signal_table(results)

    _save_snapshot(results, region.lower(), run_date)


def main():
    parser = argparse.ArgumentParser(description="7S S4 strategy engine runner")
    parser.add_argument("--region", default="all", choices=["cn", "us", "all"],
                        help="Region to run: cn, us, or all")
    parser.add_argument("--date", default=str(date.today()),
                        help="Date marker for snapshot (default: today)")
    parser.add_argument("--backtest", action="store_true",
                        help="Also run 10-year backtest simulation (default: signals only)")
    parser.add_argument("--sleeve", type=str, default=None,
                        choices=["equity", "bond", "commodity", "other"],
                        help="Filter by asset sleeve category (equity, bond, commodity)")
    args = parser.parse_args()

    regions = ["cn", "us"] if args.region == "all" else [args.region]
    for r in regions:
        run(r, args.date, with_backtest=args.backtest, sleeve=args.sleeve)


if __name__ == "__main__":
    main()
