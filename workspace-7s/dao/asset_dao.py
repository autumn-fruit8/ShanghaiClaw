"""Asset DAO — tradeable asset catalog.

Single-responsibility: manages Asset model + AssetManifest singleton.
Reads from: config/assets/asset-master.json
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from utils.constants import AssetType


# ─── Asset Source ─────────────────────────────────────────────────────────


@dataclass
class AssetSource:
    """Data source metadata for the asset's index (CSI URL etc.)."""
    provider: str = ""
    url: str = ""


# ─── Asset ────────────────────────────────────────────────────────────────


@dataclass
class Asset:
    """A single tradeable asset tracked by workspace-7s."""
    symbol: str
    name: str
    asset_type: AssetType
    region: str = ""
    tracks: Optional[str] = None    # CSI index code this asset tracks
    description: str = ""
    strategy_type: str = "STEADY"
    sleeve: str = "equity"
    tags: list[str] = field(default_factory=list)
    cal_source: Optional[AssetSource] = None
    fee: Optional[dict] = None

    def __post_init__(self):
        if isinstance(self.asset_type, str):
            self.asset_type = AssetType(self.asset_type)


# ─── AssetManifest ───────────────────────────────────────────────────────


ASSET_MASTER_PATH = Path(__file__).resolve().parents[1] / "config" / "assets" / "asset-master.json"


class AssetManifest:
    """Singleton registry of all tracked assets."""

    _instance: Optional["AssetManifest"] = None
    _assets: dict[str, Asset] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._assets = {}
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        path = os.getenv("SEVENS_ASSET_MASTER_PATH", "")
        if not path:
            path = str(ASSET_MASTER_PATH)
        if not Path(path).exists():
            raise FileNotFoundError(f"Asset master not found: {path}")

        with open(path) as f:
            payload = json.load(f)

        for item in payload.get("assets", []):
            asset = self._from_payload(item)
            self._assets[asset.symbol] = asset

    def _from_payload(self, item: dict) -> Asset:
        symbol = str(item.get("symbol", "")).strip()
        if not symbol:
            raise ValueError(f"Asset entry missing symbol: {item!r}")

        region = str(item.get("region", "")).strip().upper()
        asset_type = item.get("asset_type") or item.get("type")
        if not asset_type:
            asset_type = "CN_ETF" if region == "CN" else "US_ETF"

        cal_source = None
        raw = item.get("cal_source")
        if isinstance(raw, dict):
            cal_source = AssetSource(
                provider=raw.get("provider", ""),
                url=raw.get("url", ""),
            )

        return Asset(
            symbol=symbol,
            name=item.get("name", symbol),
            asset_type=asset_type,
            region=region,
            tracks=item.get("tracks"),
            description=item.get("description", ""),
            strategy_type=item.get("strategy_type", "STEADY"),
            sleeve=item.get("sleeve", "equity"),
            tags=item.get("tags", []),
            cal_source=cal_source,
            fee=item.get("fee"),
        )

    def get(self, symbol: str) -> Optional[Asset]:
        return self._assets.get(symbol)

    def get_all(self) -> list[Asset]:
        return list(self._assets.values())

    def get_by_region(self, region: str) -> list[Asset]:
        cn_types = {AssetType.CN_ETF, AssetType.CN_OTC}
        us_types = {AssetType.US_ETF, AssetType.HK_ETF}
        target = cn_types if region.upper() == "CN" else us_types
        return [a for a in self._assets.values() if a.asset_type in target]

    def get_by_type(self, asset_type: AssetType) -> list[Asset]:
        return [a for a in self._assets.values() if a.asset_type == asset_type]

    def register(self, asset: Asset) -> None:
        self._assets[asset.symbol] = asset

    def unregister(self, symbol: str) -> bool:
        return bool(self._assets.pop(symbol, None))
