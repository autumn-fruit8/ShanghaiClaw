"""SSOT domain models for Plan and Position.

Domain model (2026-05-06):
- Plan: investor constraints & target allocations
- Position: daily snapshot of actual holdings

Weight systems:
  - relative_weight (per asset): asset_mv / position.total_market_value
  - building_progress (per asset): asset_mv / (plan.target_market_value × target_weight)
  - position_progress (whole position): position.total_market_value / plan.target_market_value
  - drift: relative_weight - target_weight
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional


# ─── Plan Entities ────────────────────────────────────────────────────────────


@dataclass
class PlanAsset:
    symbol: str
    target_weight: float
    role: Optional[str] = None
    preferred: bool = False
    name: str = ""
    risk_volatility: Optional[float] = None


@dataclass
class PlanConstraints:
    drift_threshold: float = 0.05
    max_weight: float = 1.0
    min_weight: float = 0.0


@dataclass
class Plan:
    plan_id: str
    version: int
    region: str
    currency: str
    target_market_value: Optional[float] = None
    all_assets: list[PlanAsset] = field(default_factory=list)
    constraints: Optional[PlanConstraints] = None
    created: Optional[str] = None

    # Legacy fields (for backward compat with existing JSON)
    name: str = ""
    sleeves: list = field(default_factory=list)  # Deprecated, use all_assets

    @staticmethod
    def load(plan_id: str, version: int, plans_root: Path) -> Plan:
        path = plans_root / plan_id / f"v{version}.json"
        with open(path) as f:
            data = json.load(f)
        return Plan.from_dict(plan_id, version, data)

    def save(self, plans_root: Path):
        path = plans_root / self.plan_id / f"v{self.version}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @staticmethod
    def from_dict(plan_id: str, version: int, data: dict) -> Plan:
        all_assets = [
            PlanAsset(
                symbol=a["symbol"],
                target_weight=a.get("target_weight", 0.0),
                role=a.get("role"),
                preferred=a.get("preferred", False),
                name=a.get("name", a["symbol"]),
                risk_volatility=a.get("risk_volatility"),
            )
            for a in data.get("all_assets", [])
        ]

        constraints_data = data.get("constraints", {})
        constraints = PlanConstraints(
            drift_threshold=constraints_data.get("drift_threshold", 0.05),
            max_weight=constraints_data.get("max_weight", 1.0),
            min_weight=constraints_data.get("min_weight", 0.0),
        ) if constraints_data else None

        return Plan(
            plan_id=plan_id,
            version=version,
            region=data.get("region", "CN"),
            currency=data.get("currency", "CNY"),
            target_market_value=data.get("target_market_value") or data.get("target_total_market_value"),
            all_assets=all_assets,
            constraints=constraints,
            created=data.get("created"),
            name=data.get("name", plan_id),
        )

    def to_dict(self) -> dict:
        data = {
            "region": self.region,
            "currency": self.currency,
            "name": self.name,
        }
        if self.target_market_value is not None:
            data["target_market_value"] = self.target_market_value
        if self.constraints:
            data["constraints"] = {
                "drift_threshold": self.constraints.drift_threshold,
                "max_weight": self.constraints.max_weight,
                "min_weight": self.constraints.min_weight,
            }
        if self.created:
            data["created"] = self.created
        data["all_assets"] = [
            {
                "symbol": a.symbol,
                "name": a.name,
                "target_weight": a.target_weight,
                "role": a.role,
                "preferred": a.preferred,
                "risk_volatility": a.risk_volatility,
            }
            for a in self.all_assets
        ]
        return data

    def get_asset(self, symbol: str) -> Optional[PlanAsset]:
        for a in self.all_assets:
            if a.symbol == symbol:
                return a
        return None


# ─── Position Entities ────────────────────────────────────────────────────────


@dataclass
class PositionSnapshot:
    symbol: str
    shares: float
    current_price: float = 0.0
    market_value: float = 0.0  # computed: shares × current_price
    name: str = ""  # asset display name

    @property
    def weight(self, total_market_value: float) -> float:
        if total_market_value <= 0:
            return 0.0
        return self.market_value / total_market_value


@dataclass
class Position:
    """Daily snapshot of a plan's holdings."""
    plan_id: str
    plan_version: int
    snapshot_date: date
    positions: list[PositionSnapshot] = field(default_factory=list)

    @property
    def total_market_value(self) -> float:
        return sum(p.market_value for p in self.positions)

    @staticmethod
    def load(plan_id: str, snapshot_date: Optional[date], positions_root: Path) -> Position:
        """Load position snapshot.

        Resolution order:
          1. If snapshot_date is None → use the latest available snapshot.
          2. If snapshot_date is provided and file exists → use it.
          3. If snapshot_date is provided but file doesn't exist → fall back to
             the latest available snapshot (same as None), then log a warning.
        """
        if snapshot_date is not None:
            path = positions_root / plan_id / f"{snapshot_date.isoformat()}.json"
            if path.exists():
                with open(path) as f:
                    data = json.load(f)
                return Position.from_dict(plan_id, snapshot_date, data)
            # Fall through: requested date not found, try latest
            import logging
            logging.warning(
                "Position.load: %s snapshot for %s not found, falling back to latest",
                plan_id, snapshot_date,
            )

        # Fallback: load latest available snapshot
        pos_dir = positions_root / plan_id
        files = sorted(pos_dir.glob("*.json"), reverse=True)
        if not files:
            return Position(plan_id=plan_id, plan_version=0, snapshot_date=snapshot_date or date.today())
        fallback_date = date.fromisoformat(files[0].stem)
        path = files[0]
        with open(path) as f:
            data = json.load(f)
        return Position.from_dict(plan_id, fallback_date, data)
        with open(path) as f:
            data = json.load(f)
        return Position.from_dict(plan_id, snapshot_date, data)

    def save(self, positions_root: Path):
        path = positions_root / self.plan_id / f"{self.snapshot_date.isoformat()}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @staticmethod
    def from_dict(plan_id: str, snapshot_date: date, data: dict) -> Position:
        snapshots = [
            PositionSnapshot(
                symbol=p["symbol"],
                shares=p.get("shares", 0.0),
                current_price=p.get("current_price", 0.0),
                market_value=p.get("market_value", 0.0),
                name=p.get("name", ""),
            )
            for p in data.get("positions", [])
        ]
        return Position(
            plan_id=plan_id,
            plan_version=data.get("plan_version", 0),
            snapshot_date=snapshot_date,
            positions=snapshots,
        )

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "snapshot_date": self.snapshot_date.isoformat(),
            "total_market_value": self.total_market_value,
            "positions": [
                {
                    "symbol": p.symbol,
                    "name": p.name,
                    "shares": p.shares,
                    "current_price": p.current_price,
                    "market_value": p.market_value,
                }
                for p in self.positions
            ],
        }

    def get_snapshot(self, symbol: str) -> Optional[PositionSnapshot]:
        for p in self.positions:
            if p.symbol == symbol:
                return p
        return None


# ─── Holdings Entities ─────────────────────────────────────────────────────────


@dataclass
class Holding:
    symbol: str
    name: str = ""
    weight: float = 0.0
    sector: str = ""


@dataclass
class HoldingsData:
    etf_symbol: str
    fetched_date: date
    holdings: list[Holding]
    source: str = ""

    def top_holdings(self, n: int = 10) -> list[Holding]:
        sorted_h = sorted(self.holdings, key=lambda h: h.weight, reverse=True)
        return sorted_h[:n]


# ─── Asset Catalog ────────────────────────────────────────────────────────────


@dataclass
class AssetCatalog:
    active_symbols: set[str] = field(default_factory=set)
    catalog: dict = field(default_factory=dict)

    @staticmethod
    def load(config_root: Path) -> AssetCatalog:
        active_symbols, catalog = set(), {}

        active_path = config_root / "data" / "state_db" / "active.json"
        if active_path.exists():
            with open(active_path) as f:
                data = json.load(f)
            for asset in data.get("assets", []):
                if "symbol" in asset:
                    active_symbols.add(asset["symbol"])

        # Try both paths for asset-master.json
        for rel in ("data/asset-master.json", "asset-master.json"):
            catalog_path = config_root / rel
            if catalog_path.exists():
                with open(catalog_path) as f:
                    data = json.load(f)
                catalog = {a["symbol"]: a for a in data.get("assets", [])}
                break

        return AssetCatalog(active_symbols=active_symbols, catalog=catalog)

    def is_active(self, symbol: str) -> bool:
        return symbol in self.active_symbols


# ─── Plan + Position Combined View ────────────────────────────────────────────


@dataclass
class PlanPositionView:
    """Combines Plan and Position to compute dual weight metrics.

    Weight System 1 — Relative Allocation:
        relative_weight = asset_mv / position.total_market_value
        drift           = relative_weight - target_weight

    Weight System 2 — Building Progress:
        position_progress  = position.total_market_value / plan.target_market_value
        building_progress  = asset_mv / (plan.target_market_value × target_weight)
    """
    plan: Plan
    position: Position

    # ── Weight System 1: Relative Allocation ────────────────────────────────

    @property
    def total_market_value(self) -> float:
        """Total market value of the position (sum of all assets)."""
        return self.position.total_market_value

    def relative_weight(self, symbol: str) -> float:
        """Current weight of asset within the position (proportion of position)."""
        total = self.total_market_value
        if total <= 0:
            return 0.0
        snapshot = self.position.get_snapshot(symbol)
        return snapshot.market_value / total if snapshot else 0.0

    def drift(self, symbol: str) -> float:
        """Deviation from target weight (relative_weight - target_weight)."""
        plan_asset = self.plan.get_asset(symbol)
        target = plan_asset.target_weight if plan_asset else 0.0
        return self.relative_weight(symbol) - target

    # ── Weight System 2: Building Progress ─────────────────────────────────

    @property
    def position_progress(self) -> float:
        """How built is the position relative to plan target (0-1+)."""
        if not self.plan.target_market_value or self.plan.target_market_value <= 0:
            return 0.0
        return self.total_market_value / self.plan.target_market_value

    def building_progress(self, symbol: str) -> float:
        """How built is the asset relative to its target allocation (0-1+)."""
        plan_asset = self.plan.get_asset(symbol)
        if not plan_asset or not self.plan.target_market_value:
            return 0.0
        target_asset_mv = self.plan.target_market_value * plan_asset.target_weight
        if target_asset_mv <= 0:
            return 0.0
        snapshot = self.position.get_snapshot(symbol)
        return snapshot.market_value / target_asset_mv if snapshot else 0.0

    def funding_gap(self, symbol: str) -> float:
        """Dollar amount needed to reach target allocation."""
        plan_asset = self.plan.get_asset(symbol)
        if not plan_asset or not self.plan.target_market_value:
            return 0.0
        target_asset_mv = self.plan.target_market_value * plan_asset.target_weight
        snapshot = self.position.get_snapshot(symbol)
        current_mv = snapshot.market_value if snapshot else 0.0
        return target_asset_mv - current_mv

    # ── Asset-level Summary ─────────────────────────────────────────────────

    def asset_summary(self, symbol: str) -> dict:
        """Full metrics for a single asset."""
        plan_asset = self.plan.get_asset(symbol)
        snapshot = self.position.get_snapshot(symbol)
        target_weight = plan_asset.target_weight if plan_asset else 0.0
        target_asset_mv = (self.plan.target_market_value or 0) * target_weight

        return {
            "symbol": symbol,
            "name": plan_asset.name if plan_asset else symbol,
            "shares": snapshot.shares if snapshot else 0.0,
            "current_price": snapshot.current_price if snapshot else 0.0,
            "market_value": snapshot.market_value if snapshot else 0.0,
            # Weight System 1
            "relative_weight": self.relative_weight(symbol),
            "target_weight": target_weight,
            "drift": self.drift(symbol),
            # Weight System 2
            "target_asset_mv": target_asset_mv,
            "building_progress": self.building_progress(symbol),
            "funding_gap": self.funding_gap(symbol),
        }

    def position_summary(self) -> dict:
        """Portfolio-level summary metrics."""
        return {
            "plan_id": self.plan.plan_id,
            "plan_version": self.plan.version,
            "target_market_value": self.plan.target_market_value,
            "current_market_value": self.total_market_value,
            "position_progress": self.position_progress,
            "asset_count": len(self.position.positions),
        }
