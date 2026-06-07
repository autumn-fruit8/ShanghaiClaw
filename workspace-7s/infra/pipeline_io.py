from __future__ import annotations

import json
from pathlib import Path


def load_expected_symbols(manifest_path: str | None, region: str) -> set[str]:
    if not manifest_path:
        return set()

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return set()

    items = payload.get("assets", []) if isinstance(payload, dict) else payload
    expected = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        item_region = str(item.get("region", region)).strip().lower()
        if region == "all" or item_region == region.lower():
            symbol = str(item.get("symbol", "")).strip()
            if symbol:
                expected.add(symbol)
    return expected


def snapshot_contains_expected_symbols(
    run_date: str,
    region: str,
    expected_symbols: set[str],
    runtime_root: Path | None = None,
) -> bool:
    if not expected_symbols or region == "all":
        return True

    if runtime_root is None:
        return False

    snapshot_path = runtime_root / "logs" / "snapshots" / f"{run_date}_{region}.json"
    if not snapshot_path.exists():
        return False

    try:
        with snapshot_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return False

    seen = {str(item.get("symbol", "")).strip() for item in payload if isinstance(item, dict)}
    return bool(seen & expected_symbols)


def load_json_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []

    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []

    return payload if isinstance(payload, list) else []


def write_selection_manifest(workspace_root: str | Path, payload: dict) -> Path:
    """Write analysis selection manifest to adhoc/ directory."""
    workspace_root = Path(workspace_root)
    selection = payload.get("selection", {})
    label = selection.get("value") or selection.get("mode") or "selection"
    slug = "".join(ch if str(ch).isalnum() or ch in {"-", "_"} else "_" for ch in str(label)).strip("_")
    slug = slug or "selection"

    out_dir = workspace_root / "adhoc"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{payload.get('region', 'ALL').lower()}_{slug}.json"
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return out_path
