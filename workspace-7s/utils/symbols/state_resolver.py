"""Symbol resolution — resolve selectors to symbol lists.

Single source of truth for:
   - Loading active/watchlist/void state files
   - Resolving selectors (--symbol, --active, --watchlist, --void, --region)
   - Auto-detecting region from symbol pattern

Usage:
   from utils.symbols.state_resolver import resolve_symbols, detect_region

   symbols = resolve_symbols(workspace_root, region="cn", use_active_state=True)
   symbols = resolve_symbols(workspace_root, symbol="159207")
   symbols = resolve_symbols(workspace_root, region="all", use_watchlist_state=True)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


VALID_REGIONS = {"CN", "US", "ALL"}


# ---------------------------------------------------------------------------
# State file helpers
# ---------------------------------------------------------------------------

def load_state_symbols(workspace_root: Path, state: str) -> set[str]:
    """Load symbol set from a state file (active, watchlist, void)."""
    path = workspace_root / "config" / "states" / f"{state}.json"
    if not path.exists():
        return set()
    try:
        with open(path) as f:
            data = json.load(f)
        return {str(a.get("symbol", "")).strip() for a in data.get("assets", []) if a.get("symbol")}
    except Exception:
        return set()


def load_state_asset_records(workspace_root: Path, state: str, region: str = "ALL") -> list[dict[str, Any]]:
    """Load full asset records from a state file, optionally filtered by region."""
    from dao.state_dao import load_state_records
    try:
        records = load_state_records(workspace_root, state, region if region != "ALL" else None)
        return [dict(r) for r in records]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Region detection
# ---------------------------------------------------------------------------

def detect_region(symbol: str, explicit_region: str | None = None) -> str:
    """Auto-detect region from symbol pattern if not explicitly provided."""
    if explicit_region:
        return explicit_region.strip().lower()
    if re.match(r"^\d{6}$", symbol):
        return "cn"
    if symbol.endswith(".HK"):
        return "us"
    return "us"


def normalize_region(region: str) -> str:
    """Normalize region string to uppercase standard form."""
    value = region.strip().upper()
    if value not in VALID_REGIONS:
        raise ValueError(f"Unsupported region: {region}")
    return value


# ---------------------------------------------------------------------------
# Asset master catalog
# ---------------------------------------------------------------------------

def load_asset_catalog(workspace_root: Path) -> dict[str, dict[str, Any]]:
    """Load the master asset catalog from AssetManifest."""
    import os
    from dao.asset_dao import AssetManifest

    prev_mode = os.environ.get("SEVENS_TEMP_ASSET_MODE")
    prev_manifest = os.environ.get("SEVENS_TEMP_ASSET_MANIFEST")
    os.environ["SEVENS_TEMP_ASSET_MODE"] = "disabled"
    os.environ.pop("SEVENS_TEMP_ASSET_MANIFEST", None)

    def _infer_region(asset_type: str | None) -> str:
        value = str(asset_type or "").upper()
        return "CN" if value.startswith("CN_") else "US"

    try:
        manifest = AssetManifest()
        catalog: dict[str, dict[str, Any]] = {}
        for asset in manifest.get_all():
            asset_type = getattr(asset.asset_type, "value", str(asset.asset_type))
            catalog[asset.symbol] = {
                "symbol": asset.symbol,
                "name": asset.name,
                "region": _infer_region(asset_type),
                "asset_type": asset_type,
                "strategy_type": getattr(asset, "strategy_type", "STEADY"),
                "sector": getattr(asset, "sleeve", "equity"),
                "description": getattr(asset, "description", ""),
                "data_file": getattr(asset, "data_file", f"{asset.symbol}.csv"),
                "tags": list(getattr(asset, "tags", []) or []),
                "notes": getattr(asset, "notes", ""),
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


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------

def resolve_symbols(
    workspace_root: Path,
    region: str = "ALL",
    symbol: str | None = None,
    symbols: str | None = None,
    use_active_state: bool = False,
    use_watchlist_state: bool = False,
    use_void_state: bool = False,
) -> list[str]:
    """Resolve any selector combination to a deduplicated symbol list.

    Exactly one selector must be provided. Returns list of symbol strings.
    """
    region = normalize_region(region)
    selectors = []
    if symbol:
        selectors.append(("symbol", symbol.strip()))
    if symbols:
        selectors.append(("symbols", symbols))
    if use_active_state:
        selectors.append(("state", "active"))
    if use_watchlist_state:
        selectors.append(("state", "watchlist"))
    if use_void_state:
        selectors.append(("state", "void"))

    if len(selectors) != 1:
        raise ValueError(
            "Provide exactly one selector: symbol, symbols, active, watchlist, or void"
        )

    sel_type, sel_value = selectors[0]

    if sel_type == "symbol":
        return [sel_value]

    if sel_type == "symbols":
        return [s.strip() for s in sel_value.split(",") if s.strip()]

    # State-based selectors
    from dao.state_dao import load_state_records
    region_param = region if region != "ALL" else None
    records = load_state_records(workspace_root, sel_value, region_param)
    return [str(r.get("symbol", "")).strip() for r in records if r.get("symbol")]


def resolve_symbols_from_args(
    workspace_root: Path,
    region: str = "ALL",
    symbol: str | None = None,
    symbols: str | None = None,
    use_active_state: bool = False,
    use_watchlist_state: bool = False,
    use_void_state: bool = False,
) -> list[str]:
    """Like resolve_symbols() but tolerates zero selectors — returns region assets."""
    region = normalize_region(region)

    count = sum(1 for v in [symbol, symbols, use_active_state, use_watchlist_state, use_void_state] if v)
    if count == 0:
        # Default: all assets in region
        from dao.asset_dao import AssetManifest
        manifest = AssetManifest()
        all_assets = []
        if region == "ALL":
            all_assets = manifest.get_all()
        else:
            all_assets = manifest.get_by_region(region)
        return [a.symbol for a in all_assets]

    return resolve_symbols(
        workspace_root, region, symbol, symbols,
        use_active_state, use_watchlist_state, use_void_state,
    )
