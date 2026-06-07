"""State DAO — read/write state files (active/watchlist/void).

Split from config_dao.py for single responsibility.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

VALID_STATES = {"void", "watchlist", "active"}
STATE_DB_SCHEMA_VERSION = "state-db-v1"


def _root(workspace_root: str | Path) -> Path:
    return Path(workspace_root) / "config" / "states"


def _path(workspace_root: str | Path, state: str) -> Path:
    s = str(state or "").strip().lower()
    if s not in VALID_STATES:
        raise ValueError(f"Unsupported state: {state}")
    return _root(workspace_root) / f"{s}.json"


def load_state_file(workspace_root: str | Path, state: str) -> dict[str, Any]:
    path = _path(workspace_root, state)
    if not path.exists():
        raise FileNotFoundError(f"Missing state file: {path}")
    with open(path) as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"State file must be an object: {path}")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError(f"State file assets must be a list: {path}")
    return {
        "schema_version": payload.get("schema_version", STATE_DB_SCHEMA_VERSION),
        "state": str(payload.get("state", state)).strip().lower(),
        "assets": assets,
        "path": str(path),
    }


def load_all_state_files(workspace_root: str | Path) -> dict[str, dict[str, Any]]:
    return {s: load_state_file(workspace_root, s) for s in sorted(VALID_STATES)}


def load_state_records(
    workspace_root: str | Path,
    state: str,
    region: str | None = None,
) -> list[dict[str, Any]]:
    payload = load_state_file(workspace_root, state)
    target = str(region or "").strip().upper()
    results: list[dict[str, Any]] = []
    for raw in payload["assets"]:
        item = dict(raw)
        item["state"] = payload["state"]
        item.setdefault("source", "manual")
        # Resolve region from asset-master if not in state record
        if target and target != "ALL":
            rec_region = str(item.get("region") or "").strip().upper()
            if not rec_region:
                try:
                    from dao.asset_dao import AssetManifest
                    asset = AssetManifest().get(item["symbol"])
                    rec_region = asset.region if asset else ""
                except Exception:
                    rec_region = ""
            if rec_region != target:
                continue
        results.append(item)
    return results


def build_state_index(workspace_root: str | Path) -> dict[str, dict[str, Any]]:
    """Build a symbol→state index from all state files."""
    all_states: dict[str, list[str]] = {}
    for state, payload in load_all_state_files(workspace_root).items():
        symbols: list[str] = []
        for raw in payload["assets"]:
            if not isinstance(raw, dict):
                raise ValueError(f"State entry in {state} must be an object: {raw!r}")
            sym = str(raw.get("symbol", "")).strip()
            if not sym:
                raise ValueError(f"State entry in {state} is missing symbol: {raw!r}")
            symbols.append(sym)
        all_states[state] = symbols

    # Validate: void must not overlap with other states
    void_symbols = set(all_states.get("void", []))
    non_void = {s for state, syms in all_states.items() if state != "void" for s in syms}
    conflicts = sorted(void_symbols & non_void)
    if conflicts:
        raise ValueError(
            f"void symbols must not appear in other state files. "
            f"Conflicts: {', '.join(conflicts)}"
        )

    priority = ["active", "watchlist", "void"]
    index: dict[str, dict[str, Any]] = {}
    for state in priority:
        payload = load_all_state_files(workspace_root).get(state, {"assets": []})
        for raw in payload.get("assets", []):
            sym = str(raw.get("symbol", "")).strip()
            if not sym or sym in index:
                continue
            index[sym] = {
                "state": state,
                "symbol": sym,
                "name": raw.get("name"),
                "region": raw.get("region"),
                "note": raw.get("note"),
                "updated_at": raw.get("updated_at"),
                "source": raw.get("source", "manual"),
            }
    return index
