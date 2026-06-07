from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WORKSPACE_ROOT))  # Enable infra imports

from infra.runtime_pipeline import run_pipeline
from infra.pipeline_io import write_selection_manifest
from skills.analyze.scripts.species import resolve_analysis_selection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Workspace-7S analyze action")
    parser.add_argument("--region", choices=["cn", "us", "all"], default="us")
    parser.add_argument("--date", default=str(date.today()))

    # ── Mode ──────────────────────────────────────────────────────────────────
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--cron",
        action="store_true",
        help="Production run: permanent paths, pushes report to Feishu.",
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="Weekly report variant (use with --cron). Default: daily.",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        default=None,
        help="Push report to Feishu (default: --cron enables it, adhoc disables it).",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Suppress Feishu push (useful in --cron to override the default).",
    )

    # ── Selectors (mutually exclusive, invalid in --cron) ─────────────────────
    selector_group = parser.add_mutually_exclusive_group()
    selector_group.add_argument(
        "--active",
        action="store_true",
        help="Analyze the active-holdings slice (adhoc only).",
    )
    selector_group.add_argument(
        "--watchlist",
        action="store_true",
        help="Analyze the watchlist slice (adhoc only).",
    )
    selector_group.add_argument(
        "--void",
        action="store_true",
        help="Analyze the void slice (adhoc only).",
    )
    selector_group.add_argument(
        "--symbol",
        metavar="XYZ",
        help="Analyze a single symbol (adhoc only).",
    )
    selector_group.add_argument(
        "--symbols",
        metavar="A,B,C",
        help="Analyze a comma-separated symbol basket (adhoc only).",
    )

    # ── Debug / inspection ────────────────────────────────────────────────────
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="Also run 10-year backtest simulation (signals only by default).",
    )
    parser.add_argument(
        "--show-selection",
        action="store_true",
        help="Print the resolved selection payload and exit.",
    )
    parser.add_argument(
        "--skip-map",
        action="store_true",
        help="Skip market map PNG generation.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Halt immediately if a pipeline step fails.",
    )
    return parser


def _resolve_push(args: argparse.Namespace) -> bool:
    """Determine whether to push based on mode and explicit flags."""
    if args.no_push:
        return False
    if args.push is not None:
        return args.push
    return bool(args.cron)


def _resolve_manifest_and_mode(
    args: argparse.Namespace,
) -> tuple[str | None, str]:
    """
    Cron Universe: no manifest written, mode="disabled", permanent paths.
    Adhoc Universe: manifest written to adhoc/, mode="replace", temp paths.
    """
    is_cron = bool(getattr(args, "cron", False))
    has_selector = bool(
        getattr(args, "symbol", None)
        or getattr(args, "symbols", None)
        or getattr(args, "active", False)
        or getattr(args, "watchlist", False)
        or getattr(args, "void", False)
    )

    if is_cron:
        if has_selector and (args.watchlist or args.void or args.symbol or args.symbols or args.active):
            sys.exit(
                "Error: selectors are invalid in cron mode. "
                "Cron always runs active-state assets."
            )
        return None, "disabled"

    manifest_mode = "replace"
    if args.symbol or args.symbols:
        payload = resolve_analysis_selection(
            workspace_root=WORKSPACE_ROOT,
            region=args.region,
            symbol=args.symbol,
            symbols=args.symbols,
        )
    elif args.watchlist:
        payload = resolve_analysis_selection(
            workspace_root=WORKSPACE_ROOT,
            region=args.region,
            use_default_watchlist=True,
        )
    elif args.active:
        payload = resolve_analysis_selection(
            workspace_root=WORKSPACE_ROOT,
            region=args.region,
            use_active_state=True,
        )
    elif args.void:
        payload = resolve_analysis_selection(
            workspace_root=WORKSPACE_ROOT,
            region=args.region,
            use_void_state=True,
        )
    else:
        sys.exit(
            "Error: adhoc mode requires a selector: --active, --watchlist, --void, "
            "--symbol, or --symbols."
        )

    manifest_path = write_selection_manifest(WORKSPACE_ROOT, payload)
    return str(manifest_path), manifest_mode


def resolve_analyze_manifest(
    args: argparse.Namespace,
) -> tuple[str | None, str]:
    """Public alias so test imports work without underscore."""
    return _resolve_manifest_and_mode(args)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    manifest_path, manifest_mode = _resolve_manifest_and_mode(args)
    dry_run = not _resolve_push(args)

    if args.show_selection:
        if not manifest_path:
            sys.exit("Error: --show-selection requires an adhoc selector (--active, --watchlist, --void, --symbol, or --symbols).")
        with Path(manifest_path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    rc = run_pipeline(
        workspace_root=WORKSPACE_ROOT,
        region=args.region,
        manifest_path=manifest_path,
        manifest_mode=manifest_mode,
        dry_run=dry_run,
        run_date=args.date,
        continue_on_error=not args.stop_on_error,
        skip_map=args.skip_map,
        report_type="weekly" if args.weekly else "daily",
        with_backtest=args.backtest,
        with_volume=True,          # volume enrichment always ON
    )
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
