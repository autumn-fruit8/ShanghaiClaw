"""
S1 Species — Asset classification and selection.

Resolves analysis selections from CLI selectors (--symbol, --active, --watchlist, etc.)
by loading the asset catalog and state-DB records.

────────────────────────────────────────────────────────────
  SPECIES TAXONOMY (v2 — Data-Grounded, 2026-05-21)
────────────────────────────────────────────────────────────

Every tracked asset is assigned a behavioral species based on its
total-return time-series signature. The two primary discriminants
are R² (linear-trend coefficient of determination in log space)
and C/V (CAGR ÷ annualized volatility).

  STEADY  (Compound Escalator)  — R² > ~0.85,  C/V > ~0.5
    Secular drift dominates noise. The asset has a reliable upward
    trajectory — volatility exists but is subordinate to the trend.
    Strategy: buy-hold + dip-buying (reversion within uptrend).
    Examples: dividend/quality/value ETFs, US broad-market indices,
    utilities, REITs, infrastructure, cash equivalents.

  VOLATILE (Cyclical Trend)     — R² ~0.3–0.85, C/V ~0.2–0.5
    Weak drift with large cyclical oscillations. Mean-reversion
    bands hold around a slowly evolving trend. The mean is
    usable — you can buy statistical extremes and sell rips.
    Strategy: buy deep value at  LDev << -1σ,  sell at Z > 1.
    Examples: energy equities, CN broad-market indices, growth/tech
    in oscillating markets, sector equity ETFs.

  MOMENTUM (Pure Oscillation)   — R² < ~0.3,  C/V < ~0.2
    No stable equilibrium — no secular drift, no reliable mean
    to revert to. The asset oscillates without a gravitational
    center. The "mean" in mean-reversion is just a recalculating
    historical average, not a physical attractor.
    Strategy: ride the impulse; parachute (price < MA60) is
    mandatory because there is no gravity.
    Examples: gold, broad commodity indices.

  BOND (Fixed-Income Sub-Species)
    Sleeve-based classification. Uses yield percentile as an
    additional signal dimension alongside LDev.

  Note: these thresholds are empirical starting points derived
  from 27 assets with >252 trading days of history. They will be
  refined as more data accumulates. Borderline assets (e.g. gold
  during a super-cycle producing STEADY-like metrics) require
  domain knowledge of the asset's underlying physics.
────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dao.state_dao import load_state_records


VALID_REGIONS = {"CN", "US", "ALL"}


def normalize_region(region: str | None) -> str:
    value = (region or "ALL").strip().upper()
    if value not in VALID_REGIONS:
        raise ValueError(f"Unsupported region: {region}")
    return value


def infer_region(asset_type: str | None, fallback: str = "US") -> str:
    value = str(asset_type or "").upper()
    if value.startswith("CN_"):
        return "CN"
    return fallback


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_asset_catalog(workspace_root: Path) -> dict[str, dict[str, Any]]:
    """Load the master asset catalog from AssetManifest."""
    from dao.asset_dao import AssetManifest

    prev_mode = os.environ.get("SEVENS_TEMP_ASSET_MODE")
    prev_manifest = os.environ.get("SEVENS_TEMP_ASSET_MANIFEST")

    os.environ["SEVENS_TEMP_ASSET_MODE"] = "disabled"
    os.environ.pop("SEVENS_TEMP_ASSET_MANIFEST", None)

    try:
        manifest = AssetManifest()
        catalog: dict[str, dict[str, Any]] = {}
        for asset in manifest.get_all():
            asset_type = getattr(asset.asset_type, "value", str(asset.asset_type))
            strategy_type = getattr(asset, "strategy_type", "STEADY")
            region = infer_region(asset_type)
            catalog[asset.symbol] = {
                "symbol": asset.symbol,
                "name": asset.name,
                "region": region,
                "asset_type": asset_type,
                "strategy_type": strategy_type,
                "sector": getattr(asset, "sleeve", "equity"),
                "description": getattr(asset, "description", ""),
                "data_file": getattr(asset, "data_file", f"{asset.symbol}.csv"),
                "tags": list(getattr(asset, "tags", []) or []),
                "notes": getattr(asset, "notes", ""),
                "scope": "master",
            }
        return catalog
    finally:
        if prev_mode is None:
            os.environ.pop("SEVENS_TEMP_ASSET_MODE", None)
        else:
            os.environ["SEVENS_TEMP_ASSET_MODE"] = prev_mode
        if prev_manifest is None:
            os.environ.pop("SEVENS_TEMP_ASSET_MANIFEST", None)
        else:
            os.environ["SEVENS_TEMP_ASSET_MANIFEST"] = prev_manifest


def parse_symbol_list(symbols: str) -> list[str]:
    parsed = [part.strip() for part in symbols.split(",") if part.strip()]
    if not parsed:
        raise ValueError("No symbols were provided")
    return parsed


def pick_asset(catalog: dict[str, dict[str, Any]], symbol: str, region: str) -> dict[str, Any]:
    """Pick a single asset from the catalog."""
    try:
        asset = dict(catalog[symbol])
    except KeyError as exc:
        raise ValueError(f"Unknown symbol in selection: {symbol}") from exc

    asset_region = normalize_region(asset.get("region") or infer_region(asset.get("asset_type"), region))
    if region != "ALL" and asset_region != region:
        raise ValueError(f"Symbol {symbol} belongs to region {asset_region}, not {region}")
    asset["region"] = asset_region
    return asset


def load_state_assets(
    workspace_root: Path,
    catalog: dict[str, dict[str, Any]],
    state: str,
    region: str,
) -> list[dict[str, Any]]:
    """Load assets from a state-DB (active/watchlist/void).

    Skips symbols not in asset-master with a warning (won't crash).
    """
    selected: list[dict[str, Any]] = []
    for raw in load_state_records(workspace_root, state, region):
        symbol = str(raw.get("symbol", "")).strip()
        try:
            asset = pick_asset(catalog, symbol, region)
        except ValueError:
            import logging
            logging.warning(
                "State '%s' contains '%s' which is not in asset-master. Skipping.",
                state, symbol,
            )
            continue
        asset["state"] = state
        asset["state_note"] = raw.get("note")
        if state == "watchlist":
            if raw.get("note"):
                asset["notes"] = raw["note"]
        selected.append(asset)
    return selected


def resolve_analysis_selection(
    workspace_root: str | Path,
    region: str,
    symbol: str | None = None,
    symbols: str | None = None,
    use_default_watchlist: bool = False,
    use_active_state: bool = False,
    use_void_state: bool = False,
) -> dict[str, Any]:
    """Resolve an analysis selection from CLI selectors into a payload."""
    workspace_root = Path(workspace_root)
    region = normalize_region(region)
    chosen = [value for value in (symbol, symbols) if value]
    if use_default_watchlist:
        chosen.append("default-watchlist")
    if use_active_state:
        chosen.append("active-state")
    if use_void_state:
        chosen.append("void-state")
    if len(chosen) != 1:
        raise ValueError(
            "Provide exactly one of symbol, symbols, use_default_watchlist, "
            "use_active_state, or use_void_state"
        )

    catalog = load_asset_catalog(workspace_root)

    if use_default_watchlist:
        selected = load_state_assets(workspace_root, catalog, "watchlist", region)
        if not selected:
            raise ValueError(f"No watchlist assets found for region {region}")
        weights = [1.0 / len(selected)] * len(selected)
        selection = {"mode": "default-watchlist", "value": "watchlist"}
    elif use_active_state:
        selected = load_state_assets(workspace_root, catalog, "active", region)
        if not selected:
            raise ValueError(f"No active assets found for region {region}")
        weights = [1.0 / len(selected)] * len(selected)
        selection = {"mode": "active-state", "value": "active"}
    elif use_void_state:
        selected = load_state_assets(workspace_root, catalog, "void", region)
        if not selected:
            raise ValueError(f"No void assets found for region {region}")
        weights = [1.0 / len(selected)] * len(selected)
        selection = {"mode": "void-state", "value": "void"}
    elif symbol:
        selected = [pick_asset(catalog, symbol.strip(), region)]
        weights = [1.0]
        selection = {"mode": "symbol", "value": symbol.strip()}
    else:
        requested = parse_symbol_list(symbols or "")
        selected = [pick_asset(catalog, item, region) for item in requested]
        weights = [1.0 / len(selected)] * len(selected)
        selection = {"mode": "symbols", "value": requested}

    assets: list[dict[str, Any]] = []
    for asset, weight in zip(selected, weights):
        asset["weight"] = weight
        assets.append(asset)

    return {
        "manifest_id": (
            f"7s_{selection['mode']}_{selection['value'] if isinstance(selection['value'], str) else 'basket'}"
        ),
        "scope": "generated by workspace-7s analysis runner",
        "region": region,
        "selection": selection,
        "assets": assets,
    }
