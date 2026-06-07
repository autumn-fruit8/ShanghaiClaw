"""7S workspace validation utilities.

Checks consistency between state files and asset-master.
"""
from __future__ import annotations

import json
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = WORKSPACE_ROOT / "config" / "states"
ASSET_MASTER = WORKSPACE_ROOT / "config" / "assets" / "asset-master.json"


def load_master_symbols() -> set[str]:
    with open(ASSET_MASTER) as f:
        return {a["symbol"] for a in json.load(f)["assets"]}


def load_state_symbols(state_file: str) -> list[dict]:
    path = STATE_DIR / state_file
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f).get("assets", [])


def validate_states() -> list[str]:
    """Check all state files against asset-master.

    Returns a list of warning/error messages (empty = all clean).
    """
    master = load_master_symbols()
    messages: list[str] = []
    total_missing = 0

    for fname in ["active.json", "watchlist.json", "void.json"]:
        assets = load_state_symbols(fname)
        missing = [a for a in assets if a["symbol"] not in master]
        if missing:
            syms = ", ".join(a["symbol"] for a in missing)
            messages.append(
                f"❌ {fname}: {len(missing)} symbols not in asset-master: {syms}"
            )
            total_missing += len(missing)
        else:
            messages.append(f"✅ {fname}: {len(assets)} assets, all consistent")

    if total_missing == 0:
        messages.append("\nAll states consistent ✅")

    return messages


def validate_tracks() -> list[str]:
    """Check asset-master for missing or unknown tracks (CN assets must have tracks)."""
    import json

    with open(ASSET_MASTER) as f:
        assets = json.load(f).get("assets", [])

    messages: list[str] = []
    missing = []
    unknown = []

    for a in assets:
        if a.get("region") != "CN":
            continue
        tracks = a.get("tracks", "")
        if not tracks:
            missing.append(f"{a['symbol']} ({a.get('name','')})")
        elif tracks == "UNKNOWN":
            unknown.append(f"{a['symbol']} ({a.get('name','')})")

    if missing:
        messages.append(f"❌ {len(missing)} CN assets MISSING tracks:")
        for m in missing:
            messages.append(f"     {m}")
    if unknown:
        messages.append(f"⚠️ {len(unknown)} CN assets have UNKNOWN tracks (needs investigation):")
        for u in unknown:
            messages.append(f"     {u}")
    if not missing and not unknown:
        messages.append(f"✅ All {sum(1 for a in assets if a.get('region')=='CN')} CN assets have tracks")

    return messages


if __name__ == "__main__":
    print("=== State Consistency ===")
    for msg in validate_states():
        print(msg)
    print()
    print("=== Asset-master Tracks ===")
    for msg in validate_tracks():
        print(msg)
