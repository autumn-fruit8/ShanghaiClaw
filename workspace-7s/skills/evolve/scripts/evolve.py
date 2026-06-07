"""Evolve (S7) - Human-led review process.

This module is a stub that assists the human-led monthly review.
It does NOT make automated decisions or change state.

Usage:
    python3 skills/evolve/scripts/evolve.py --remind
    python3 skills/evolve/scripts/evolve.py --summary
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="7S Evolve (S7) - Human-led review assistant")
    parser.add_argument(
        "--remind",
        action="store_true",
        help="Print reminder for monthly review",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Generate summary of recent runs (for review template)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to look back for summary (default: 30)",
    )
    return parser


def print_reminder() -> None:
    """Print the monthly review reminder."""
    today = date.today()
    print(f"# 7S Monthly Review Reminder - {today}")
    print()
    print("## Review Template")
    print(f"Please fill out: docs/evolve/review-template.md")
    print()
    print("## Steps")
    print("1. Review logs/positions/ from the past month")
    print("2. Review position evidence and decisions")
    print("3. Fill out docs/evolve/review-template.md")
    print("4. If proposals adopted, update MEMORY.md")
    print()
    print("## Non-negotiable")
    print("- Evolve proposals are NOT policy until explicitly adopted by human")
    print("- No changes to config/state_db/ without explicit human approval")
    print("- No changes to position plans without explicit human approval")


def generate_summary(days: int) -> None:
    """Generate summary of recent runs (stub)."""
    print(f"# 7S Summary - Past {days} days")
    print()
    print("Stub: This will aggregate recent snapshots and position decisions.")
    print("For now, please review logs/positions/ manually.")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.remind:
        print_reminder()
        return 0

    if args.summary:
        generate_summary(args.days)
        return 0

    # Default: print reminder
    print_reminder()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
