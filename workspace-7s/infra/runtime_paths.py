from __future__ import annotations

from datetime import datetime
from pathlib import Path


DISABLED_MODES = {"", "0", "false", "off", "disabled", "none"}


def slugify(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value).strip("_") or "run"


def resolve_runtime_root(
    workspace_root: str | Path,
    run_date: str,
    region: str,
    manifest_path: str | None,
    manifest_mode: str,
) -> Path | None:
    """Resolve runtime root for cron (manifest) mode. Returns None for disabled mode."""
    mode = (manifest_mode or "disabled").strip().lower()
    if mode in DISABLED_MODES:
        return None

    workspace_root = Path(workspace_root)
    source = Path(manifest_path).stem if manifest_path else f"{region}_temp"

    prefix = f"{region.lower()}_"
    while source.lower().startswith(prefix):
        source = source[len(prefix):]

    source = source or "temp"
    slug = slugify(f"{run_date}_{region}_{source}")
    return workspace_root / "adhoc" / slug


# ---------------------------------------------------------------------------
# Cron Universe paths (permanent)
# ---------------------------------------------------------------------------

def cron_knowledge_base(workspace_root: str | Path) -> Path:
    """Permanent knowledge base: knowledge/{region}/"""
    return Path(workspace_root) / "knowledge"


def cron_logs_base(workspace_root: str | Path) -> Path:
    """Permanent logs base: logs/{region}/"""
    return Path(workspace_root) / "logs"


# ---------------------------------------------------------------------------
# Adhoc Universe paths (temporary per-run)
# ---------------------------------------------------------------------------

def adhoc_knowledge_base(workspace_root: str | Path, run_id: str) -> Path:
    """Adhoc knowledge base: adhoc/{run_id}/knowledge/{region}/"""
    return Path(workspace_root) / "adhoc" / run_id / "knowledge"


def adhoc_logs_base(workspace_root: str | Path, run_id: str) -> Path:
    """Adhoc logs base: adhoc/{run_id}/logs/{region}/"""
    return Path(workspace_root) / "adhoc" / run_id / "logs"


def resolve_adhoc_run_id(universe: str, region: str) -> str:
    """Generate a unique run ID for adhoc execution."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return slugify(f"{timestamp}_{region}_{universe}")
