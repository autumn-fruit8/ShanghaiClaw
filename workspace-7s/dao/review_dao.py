"""Review DAO - CRUD for logs/review/."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional


def load_review(plan_id: str, region: str, review_dir: Path) -> Optional[dict]:
    """Load latest review for a plan. Returns dict with chart_path and review_section."""
    pattern = f"*_{plan_id}_{region.lower()}_review*"
    files = sorted((review_dir / plan_id).glob(pattern), reverse=True) if (review_dir / plan_id).exists() else []
    if not files:
        return None
    
    # Find the most recent review directory
    return {"path": str(files[0])}  # Placeholder - actual implementation would parse files


def save_review(
    plan_id: str,
    region: str,
    review_dir: Path,
    chart_path: str,
    review_section: str,
    run_date: date,
) -> Path:
    """Save review output."""
    out_dir = review_dir / plan_id
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Save markdown
    md_path = out_dir / f"{run_date.isoformat()}_{plan_id}_{region.lower()}_review.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(review_section)
    
    return md_path


def list_reviews(plan_id: str, review_dir: Path) -> list[date]:
    """List all review dates for a plan."""
    review_plan_dir = review_dir / plan_id
    if not review_plan_dir.exists():
        return []
    dates = []
    for f in review_plan_dir.glob("*_review.md"):
        try:
            # Format: 2026-05-09_cn_hb_CN_review.md
            dates.append(date.fromisoformat(f.stem.split("_")[0]))
        except ValueError:
            pass
    return sorted(dates, reverse=True)
