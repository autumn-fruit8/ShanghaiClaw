"""Config DAO - Access to config/ directory (static/semi-static data).

Merged from:
- loader.py: ConfigLoader
- assets.py: AssetManifest
- state_db.py: state loading
- shared/loader.py: plan loading
"""
from __future__ import annotations

import json
import os
import yaml
from pathlib import Path
from typing import Any, Optional



# ─── ConfigLoader ──────────────────────────────────────────────────────────────


class ConfigLoader:
    """Configuration loader with caching support. Singleton."""

    _instance: Optional["ConfigLoader"] = None
    _config: Optional[dict[str, Any]] = None

    def __new__(cls, config_path: Optional[str] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_path: Optional[str] = None):
        if self._config is None:
            self._load(config_path)

    def _load(self, config_path: Optional[str] = None) -> None:
        """Load configuration from YAML file."""
        if config_path is None:
            config_path = Path(__file__).resolve().parents[1] / "config" / "engine.yaml"

        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f)

        # Resolve base_dir: "." → workspace-7s root (parent of config/)
        if self._config.get("paths", {}).get("base_dir") == ".":
            self._config["paths"]["base_dir"] = str(Path(__file__).parent.parent)

    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """Get configuration value by dot-notation key (e.g. 'app.name')."""
        instance = cls()
        keys = key.split(".")
        value = instance._config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    @classmethod
    def get_path(cls, key: str, default: Any = None) -> Path:
        """Get a path config value as an absolute Path."""
        base_dir = Path(cls.get("paths.base_dir"))
        relative = cls.get(f"paths.{key}")

        if relative is None:
            if default is not None:
                p = Path(default)
                return p if p.is_absolute() else base_dir / p
            raise KeyError(f"Path configuration not found: paths.{key}")

        p = Path(relative)
        return p if p.is_absolute() else base_dir / p

    @classmethod
    def get_strategy_config(cls, strategy_type: Optional[str] = None) -> dict[str, Any]:
        """Return strategy config dict, optionally scoped to a strategy type."""
        strategy_config = cls.get("strategy") or {}
        if strategy_type:
            return strategy_config.get(strategy_type, {})
        return strategy_config

    @classmethod
    def reload(cls, config_path: Optional[str] = None) -> None:
        """Force reload (useful in tests)."""
        cls._config = None
        cls(config_path)

    @property
    def config(self) -> dict[str, Any]:
        return self._config.copy()


# ─── AssetManifest ─────────────────────────────────────────────────────────────




# ─── State DB ──────────────────────────────────────────────────────────────────


VALID_STATES = {"void", "watchlist", "active"}
STATE_DB_SCHEMA_VERSION = "state-db-v1"


def state_db_root(workspace_root: str | Path) -> Path:
    return Path(workspace_root) / "config" / "states"


def state_file_path(workspace_root: str | Path, state: str) -> Path:
    normalized = normalize_state_name(state)
    return state_db_root(workspace_root) / f"{normalized}.json"


def normalize_state_name(value: str) -> str:
    state = str(value or "").strip().lower()
    if state not in VALID_STATES:
        raise ValueError(f"Unsupported state: {value}")
    return state


def load_state_file(workspace_root: str | Path, state: str) -> dict[str, Any]:
    path = state_file_path(workspace_root, state)
    if not path.exists():
        raise FileNotFoundError(f"Missing state file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"State file must be an object: {path}")
    file_state = normalize_state_name(payload.get("state", state))
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError(f"State file assets must be a list: {path}")
    return {
        "schema_version": payload.get("schema_version", STATE_DB_SCHEMA_VERSION),
        "state": file_state,
        "assets": assets,
        "path": str(path),
    }


def load_all_state_files(workspace_root: str | Path) -> dict[str, dict[str, Any]]:
    return {state: load_state_file(workspace_root, state) for state in sorted(VALID_STATES)}


def build_state_index(workspace_root: str | Path) -> dict[str, dict[str, Any]]:
    """Build a symbol index from all state files."""
    all_states: dict[str, list[str]] = {}
    for state, payload in load_all_state_files(workspace_root).items():
        symbols: list[str] = []
        for raw in payload["assets"]:
            if not isinstance(raw, dict):
                raise ValueError(f"State entry in {state} must be an object: {raw!r}")
            symbol = str(raw.get("symbol", "")).strip()
            if not symbol:
                raise ValueError(f"State entry in {state} is missing symbol: {raw!r}")
            symbols.append(symbol)
        all_states[state] = symbols

    void_symbols = set(all_states.get("void", []))
    non_void_symbols = {s for state, syms in all_states.items() if state != "void" for s in syms}
    void_conflicts = sorted(void_symbols & non_void_symbols)
    if void_conflicts:
        raise ValueError(f"void symbols must not appear in any other state file. Conflicts: {', '.join(void_conflicts)}")

    priority = ["active", "watchlist", "void"]
    index: dict[str, dict[str, Any]] = {}
    for state in priority:
        payload = load_all_state_files(workspace_root).get(state, {"assets": []})
        for raw in payload.get("assets", []):
            symbol = str(raw.get("symbol", "")).strip()
            if not symbol or symbol in index:
                continue
            index[symbol] = {
                "state": state,
                "symbol": symbol,
                "name": raw.get("name"),
                "region": raw.get("region"),
                "note": raw.get("note"),
                "updated_at": raw.get("updated_at"),
                "source": raw.get("source", "manual"),
            }
    return index


def load_state_records(
    workspace_root: str | Path,
    state: str,
    region: str | None = None,
) -> list[dict[str, Any]]:
    payload = load_state_file(workspace_root, state)
    target_region = str(region or "").strip().upper()
    results: list[dict[str, Any]] = []
    for raw in payload["assets"]:
        item = dict(raw)
        item["state"] = payload["state"]
        item.setdefault("source", "manual")
        if target_region and target_region != "ALL":
            if str(item.get("region", "")).strip().upper() != target_region:
                continue
        results.append(item)
    return results


# ─── Plan Loading ──────────────────────────────────────────────────────────────


def load_plan(plan_id: str, version: Optional[int], plans_root: Path):
    """Load a plan. If version is None, load latest."""
    from dao.models import Plan
    if version is None:
        versions = list_plan_versions(plan_id, plans_root)
        if not versions:
            raise FileNotFoundError(f"No versions found for plan '{plan_id}'")
        version = max(versions)
    return Plan.load(plan_id, version, plans_root)


def list_plan_versions(plan_id: str, plans_root: Path) -> list[int]:
    """Return sorted version numbers for a plan."""
    plan_dir = plans_root / plan_id
    if not plan_dir.exists():
        return []
    versions = []
    for f in plan_dir.glob("v*.json"):
        try:
            versions.append(int(f.stem[1:]))
        except ValueError:
            pass
    return sorted(versions)


def list_plans(plans_root: Path) -> list[str]:
    """Return all plan IDs."""
    if not plans_root.exists():
        return []
    return sorted(d.name for d in plans_root.iterdir() if d.is_dir())
