"""
strategy_registry.py — Load, validate, resolve Strategy configs from YAML.

The single entry point for loading any named strategy.
Validates all YAML against the concept definitions in docs/STRATEGY_ARCHITECTURE.md.

Usage:
    from skills.analyze.scripts.s4_strategy.registry import StrategyRegistry

    registry = StrategyRegistry()
    strategy = registry.load("dca-7s")
    # → Strategy(name="dca-7s", profile={...}, tactic={...}, params={...})
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_THIS = Path(__file__).resolve()
_WORKSPACE_ROOT = _THIS.parents[4]
_STRATEGIES_DIR = _WORKSPACE_ROOT / "config" / "strategies"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Strategy:
    name: str
    description: str
    profile: dict
    tactic: dict
    params: dict

    def __post_init__(self):
        # name is also stored inside params for convenience
        if "name" not in self.params:
            self.params["name"] = self.name


@dataclass
class StrategyRegistry:
    strategies_dir: Path = field(default_factory=lambda: _STRATEGIES_DIR)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_strategies(self) -> list[str]:
        """Return list of available strategy configurations from routing.
        Includes species defaults + per-symbol overrides (unique combos)."""
        routing = self._load_strategy_routing()
        seen = set()
        names = []
        def _add(p, t):
            key = f"{p}+{t}"
            if key not in seen:
                seen.add(key)
                names.append(key)
        for entry in routing.get("species_defaults", {}).values():
            if isinstance(entry, dict):
                _add(entry.get('profile'), entry.get('tactic'))
        for entry in routing.get("symbols", []):
            if isinstance(entry, dict):
                p = entry.get('profile')
                t = entry.get('tactic')
                if p and t:
                    _add(p, t)
        return names

    def load(self, name: str) -> Strategy:
        """Load a strategy by name. Reads strategy YAML, or resolves from routing.

        Supports "profile+tactic" format (e.g. "momentum+trend") to create
        ad-hoc strategy from any profile/tactic combo without a YAML file.

        Strategy YAML is the legacy format {profile, tactic, params}.
        When no YAML exists, falls back to routing's species_defaults or fallback.
        """
        path = self.strategies_dir / f"{name}.yaml"
        if path.exists():
            with open(path) as f:
                raw = yaml.safe_load(f)
            self._validate_composition(raw, name)
            profile = self._load_profile(raw.get("profile", "7s-base"))
            tactic = self._load_tactic(raw.get("tactic", "dca"))
            self._validate_profile_tactic_match(profile, tactic,
                raw.get("profile", "7s-base"), raw.get("tactic", "dca"))
            params = raw.get("params", {})
            params.setdefault("initial_cash", 100000.0)
            return Strategy(
                name=name,
                description=raw.get("description", ""),
                profile=profile,
                tactic=tactic,
                params=params,
            )

        # Try "profile+tactic" ad-hoc format (no YAML needed)
        if "+" in name:
            parts = name.split("+", 1)
            profile = self._load_profile(parts[0].strip())
            tactic = self._load_tactic(parts[1].strip())
            self._validate_profile_tactic_match(profile, tactic, parts[0], parts[1])
            return Strategy(
                name=name,
                description=f"Ad-hoc profile+tactic: {name}",
                profile=profile,
                tactic=tactic,
                params={"initial_cash": 100000.0},
            )

        routing = self._load_strategy_routing()
        defaults = routing.get("species_defaults", {})
        # Try as species key
        entry = defaults.get(name.upper(), routing.get("fallback", {}))
        if entry and isinstance(entry, dict):
            return self._build_strategy(entry, name)
        # Last resort
        entry = routing.get("fallback", {})
        return self._build_strategy(entry, name)

    # ── Strategy routing file ────────────────────────────────────────

    _ROUTING_FILE = "routing.yaml"

    def _load_strategy_routing(self) -> dict:
        """Load strategy_routing.yaml from config/strategies/."""
        path = self.strategies_dir / self._ROUTING_FILE
        if not path.exists():
            return {"symbols": [], "species_defaults": {}, "fallback": {"profile": "7s-base", "tactic": "dca"}}
        try:
            with open(path) as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {"symbols": [], "species_defaults": {}, "fallback": {"profile": "7s-base", "tactic": "dca"}}

    def resolve_strategy_for(self, species: str) -> Strategy:
        """Pick the canonical strategy for an asset species from routing."""
        routing = self._load_strategy_routing()
        defaults = routing.get("species_defaults", {})
        entry = defaults.get(species.upper(), routing.get("fallback", {}))
        return self._build_strategy(entry, f"species:{species}")

    def resolve_strategy_for_asset(self, symbol: str, species: str) -> Strategy:
        """Pick strategy for a specific asset. Priority: symbol > species > fallback."""
        routing = self._load_strategy_routing()

        for entry in routing.get("symbols", []):
            if str(entry.get("symbol")) == str(symbol):
                strategy = self._build_strategy(entry, f"symbol:{symbol}")
                return strategy

        defaults = routing.get("species_defaults", {})
        entry = defaults.get(species.upper(), routing.get("fallback", {}))
        return self._build_strategy(entry, f"species:{species}")

    def _build_strategy(self, entry: dict, source: str) -> Strategy:
        """Build a Strategy from a routing entry {profile, tactic, params?}."""
        if not entry or not isinstance(entry, dict):
            routing = self._load_strategy_routing()
            entry = routing.get("fallback", {})
        profile_name = entry.get("profile", "7s-base")
        tactic_name = entry.get("tactic", "dca")
        profile = self._load_profile(profile_name)
        tactic = self._load_tactic(tactic_name)
        self._validate_profile_tactic_match(profile, tactic, profile_name, tactic_name)
        params = entry.get("params", {})
        if not params:
            routing = self._load_strategy_routing()
            params = dict(routing.get("params", {}))
        params.setdefault("initial_cash", 100000.0)
        return Strategy(
            name=source,
            description="",
            profile=profile,
            tactic=tactic,
            params=params,
        )

    def load_profile(self, name: str) -> dict:
        """Load a signal profile by name."""
        return self._load_profile(name)

    def load_tactic(self, name: str) -> dict:
        """Load a tactic by name."""
        return self._load_tactic(name)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_profile(self, name: str) -> dict:
        path = self.strategies_dir / "profiles" / f"{name}.yaml"
        if not path.exists():
            raise ValueError(f"Profile {name!r} not found at {path}")
        with open(path) as f:
            data = yaml.safe_load(f)
        self._validate_profile(data, name)
        return data

    def _load_tactic(self, name: str) -> dict:
        path = self.strategies_dir / "tactics" / f"{name}.yaml"
        if not path.exists():
            raise ValueError(f"Tactic {name!r} not found at {path}")
        with open(path) as f:
            data = yaml.safe_load(f)
        self._validate_tactic(data, name)
        return data

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_composition(self, raw: dict, name: str) -> None:
        required = {"profile", "tactic"}
        missing = required - set(raw.keys())
        if missing:
            raise ValueError(f"Strategy {name!r} missing fields: {missing}")
        ok = {"name", "description", "profile", "tactic", "params"}
        extra = set(raw.keys()) - ok - required
        # Allow name key even if stem differs
        if "name" in extra:
            extra.discard("name")
        if extra:
            raise ValueError(f"Strategy {name!r} unknown fields: {extra}")

    def _validate_profile(self, data: dict, name: str) -> None:
        if "indicators" not in data:
            raise ValueError(f"Profile {name!r} missing 'indicators'")

        valid_indicators = {
            "ldev", "rsi", "zscore", "sma", "price_above_ma",
            "ma_cross", "roc", "slope", "adx", "vol_ratio",
        }
        for ind in data["indicators"]:
            if ind not in valid_indicators:
                raise ValueError(
                    f"Profile {name!r}: unknown indicator {ind!r}. "
                    f"Valid: {valid_indicators}"
                )

    def _validate_tactic(self, data: dict, name: str) -> None:
        if "rules" not in data:
            raise ValueError(f"Tactic {name!r} missing 'rules'")

        for i, rule in enumerate(data["rules"]):
            if "id" not in rule:
                raise ValueError(f"Tactic {name!r} rule[{i}] missing 'id'")
            if "when" not in rule:
                raise ValueError(f"Tactic {name!r} rule[{i}] ({rule['id']}) missing 'when'")
            if "do" not in rule:
                raise ValueError(f"Tactic {name!r} rule[{i}] ({rule['id']}) missing 'do'")

            do = rule["do"]
            valid_verbs = {"BUY", "SELL", "CLOSE", "HOLD"}
            if do.get("verb") not in valid_verbs:
                raise ValueError(
                    f"Tactic {name!r} rule[{i}] ({rule['id']}): "
                    f"do.verb must be one of {valid_verbs}"
                )

            frac = do.get("fraction", 0.0)
            verb_f = do.get("verb", "HOLD")
            if verb_f in ("SELL", "CLOSE", "HOLD"):
                if not (0.0 <= frac <= 1.0):
                    raise ValueError(
                        f"Tactic {name!r} rule[{i}] ({rule['id']}): "
                        f"{verb_f} fraction must be [0.0, 1.0], got {frac}"
                    )
            elif verb_f == "BUY":
                if frac < 0.0:
                    raise ValueError(
                        f"Tactic {name!r} rule[{i}] ({rule['id']}): "
                        f"BUY fraction must be >= 0.0, got {frac}"
                    )

            # Validate when clause
            self._validate_when(rule["when"], name, rule["id"])

    def _validate_when(self, when: list, tactic_name: str, rule_id: str) -> None:
        if not isinstance(when, list):
            raise ValueError(
                f"Tactic {tactic_name!r} rule {rule_id!r}: "
                f"'when' must be a list of conditions"
            )
        for j, cond in enumerate(when):
            if not isinstance(cond, dict):
                raise ValueError(
                    f"Tactic {tactic_name!r} rule {rule_id!r}: "
                    f"when[{j}] must be a dict"
                )
            for indicator, op_spec in cond.items():
                if not isinstance(op_spec, dict):
                    raise ValueError(
                        f"Tactic {tactic_name!r} rule {rule_id!r}: "
                        f"when[{j}] operator for {indicator!r} must be a dict"
                    )
                    valid_ops = {"lt", "lte", "gt", "gte", "eq", "between"}
                    for op in op_spec:
                        if op not in valid_ops:
                            raise ValueError(
                                f"Tactic {tactic_name!r} rule {rule_id!r}: "
                                f"when[{j}] operator {op!r} not in {valid_ops}"
                            )

    # ── Profile ↔ Tactic cross-validation ───────────────────────────

    def _validate_profile_tactic_match(
        self,
        profile: dict,
        tactic: dict,
        profile_name: str,
        tactic_name: str,
    ) -> None:
        """Verify tactic rule conditions reference indicators available in profile."""
        available = self._profile_available_columns(profile)

        for i, rule in enumerate(tactic.get("rules", [])):
            rule_id = rule.get("id", f"rule[{i}]")
            for cond in rule.get("when", []):
                if not isinstance(cond, dict):
                    continue
                for indicator in cond:
                    if indicator not in available:
                        raise ValueError(
                            f"Strategy validation failed: tactic {tactic_name!r} "
                            f"rule {rule_id!r} references indicator {indicator!r}, "
                            f"but profile {profile_name!r} only provides columns: "
                            f"{sorted(available)}. "
                            f"Either add {indicator!r} to profile {profile_name!r}.yaml "
                            f"or pair this tactic with a different profile."
                        )

    def _profile_available_columns(self, profile: dict) -> set[str]:
        """Compute the actual DataFrame columns a profile produces."""
        indicators = profile.get("indicators", {})
        available = set(indicators.keys())
        builtin = {"yield_pctile", "vix_pctile", "ldev", "zscore", "rsi"}
        available |= builtin

        # Expand composite indicators to actual column names
        # price_above_ma: [60] → price_above_ma_60
        pa = indicators.get("price_above_ma", [])
        for w in (pa if isinstance(pa, list) else [pa]):
            available.add(f"price_above_ma_{w}")

        # sma: [200] → sma_200
        sma = indicators.get("sma", [])
        for w in (sma if isinstance(sma, list) else [sma]):
            available.add(f"sma_{w}")

        return available
