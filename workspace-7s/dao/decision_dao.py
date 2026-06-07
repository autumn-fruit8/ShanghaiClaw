"""Decision DAO - CRUD for logs/decisions/."""
from __future__ import annotations

from datetime import date
from pathlib import Path


def load_decision(plan_id: str, decide_date: date, decisions_dir: Path) -> Optional[str]:
    """Load decision markdown for a plan on a specific date."""
    path = decisions_dir / f"{decide_date.isoformat()}_{plan_id}_decide.md"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def save_decision(plan_id: str, decide_date: date, decisions_dir: Path, content: str) -> Path:
    """Save decision markdown."""
    decisions_dir.mkdir(parents=True, exist_ok=True)
    path = decisions_dir / f"{decide_date.isoformat()}_{plan_id}_decide.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def list_decisions(plan_id: str, decisions_dir: Path) -> list[date]:
    """List all decision dates for a plan."""
    if not decisions_dir.exists():
        return []
    dates = []
    for f in decisions_dir.glob(f"*_{plan_id}_decide.md"):
        try:
            # Format: 2026-05-09_cn_hb_decide.md
            dates.append(date.fromisoformat(f.stem.split("_")[0]))
        except ValueError:
            pass
    return sorted(dates, reverse=True)
