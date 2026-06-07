from __future__ import annotations

import json
import shutil
import sys
from datetime import date
from pathlib import Path

from infra.pipeline_io import load_expected_symbols, snapshot_contains_expected_symbols, load_json_rows
from infra.process_runner import run_step
from infra.runtime_env import build_env
from infra.runtime_paths import DISABLED_MODES, resolve_runtime_root
from utils.data_service.data_resolver import batch_resolve_for_adhoc
from utils.formatting.view_builder import write_seven_layer_view


def _resolve_runtime_root(
    workspace_root: str | Path,
    run_date: str,
    region: str,
    manifest_path: str | None,
    manifest_mode: str,
) -> Path | None:
    """Backwards-compatible alias used by existing tests."""
    return resolve_runtime_root(workspace_root, run_date, region, manifest_path, manifest_mode)


def _resolve_csv_for_adhoc(
    workspace_root: Path,
    runtime_root: Path,
    region: str,
    expected_symbols: set[str] | None = None,
) -> int:
    """Resolve CSVs for adhoc analyze. Delegates to shared utils.data_resolver."""
    return batch_resolve_for_adhoc(workspace_root, runtime_root, region, expected_symbols)


def run_pipeline(
    workspace_root: str | Path,
    region: str = "us",
    manifest_path: str | None = None,
    manifest_mode: str = "replace",
    dry_run: bool = True,
    run_date: str | None = None,
    continue_on_error: bool = True,
    skip_map: bool = True,
    report_type: str = "daily",
    with_backtest: bool = False,
    # --- Snapshot enrichment ---
    with_volume: bool = True,   # volume enrichment ON by default (for future use)
    with_flow: bool = False,
) -> int:
    """Run daily update -> strategy -> report from the workspace-owned orchestrator.

    Cron mode (manifest_mode enabled): daily update → strategy → report
    Adhoc mode (manifest_mode disabled): resolve CSV (3-tier) → strategy → skip report
    """
    workspace_root = Path(workspace_root)
    run_date = run_date or str(date.today())
    runtime_root = resolve_runtime_root(workspace_root, run_date, region, manifest_path, manifest_mode)
    env = build_env(manifest_path, manifest_mode, runtime_root)
    expected_symbols = load_expected_symbols(manifest_path, region)
    python_cmd = sys.executable or "python3"

    is_cron = manifest_mode in DISABLED_MODES
    
    if is_cron:
        # ── Cron mode: active-state only ──
        steps: list[list[str]] = [
            [python_cmd, "skills/data-daily-update/scripts/run_daily_update.py", "--region", region, "--state", "active"],
        ]
    else:
        # ── Adhoc mode: cache-first resolution (no daily_update bootstrap) ──
        steps = []
        _resolve_csv_for_adhoc(workspace_root, runtime_root, region, expected_symbols)

    # Strategy + report (common to both modes)
    steps_cmd = [python_cmd, "skills/analyze/scripts/run_strategy.py", "--region", region, "--date", run_date]
    if with_backtest:
        steps_cmd.append("--backtest")
    steps.append(steps_cmd)
    
    # Report: skip for single-symbol adhoc (PDF/map are noise)
    single_adhoc = not is_cron and len(expected_symbols) == 1
    if not single_adhoc:
        steps.append([python_cmd, "skills/view_report/scripts/report_pusher.py", "--region", region, "--type", report_type, "--date", run_date])

    if dry_run and not single_adhoc:
        steps[-1].append("--dry-run")
    if skip_map and not single_adhoc:
        steps[-1].append("--skip-map")

    exit_codes: list[int] = []
    strat_idx = 1  # strategy is always step index 1 (daily update at 0, strategy at 1, report at 2)

    for index, cmd in enumerate(steps):
        rc = run_step(cmd, env, workspace_root)
        exit_codes.append(rc)
        if rc != 0 and not continue_on_error:
            return rc

        if index == strat_idx and manifest_mode not in DISABLED_MODES:
            # --- Print S3 system assessment summary ---
            try:
                from skills.analyze.scripts.system import assess_regime
                s3 = assess_regime({"symbol": region, "region": region.upper(), "name": f"{region} Market"})
                print("\n=== S3 System Assessment ===")
                print(f"  Regime: {s3.get('regime_summary', '?')} (confidence {s3.get('confidence', 0):.2f})")
                print(f"  Signal bias: {s3.get('signal_bias', '?')}")
                macro = s3.get("macro", {})
                if macro:
                    drivers = macro.get("drivers", {})
                    print(f"  Macro: real_rate={drivers.get('real_rate','?')}, inflation={drivers.get('inflation_regime','?')}")
                risk = s3.get("risk", {})
                if risk and risk.get("vix_current"):
                    print(f"  Risk: VIX={risk['vix_current']} ({risk.get('vix_zone','?')}, {risk.get('vix_percentile_rank',0)*100:.0f}%ile)")
                for ctx in s3.get("context", []):
                    print(f"    • {ctx}")
                print("---")
            except Exception as e:
                print(f"  [S3 assessment skipped: {e}]")

            if not snapshot_contains_expected_symbols(run_date, region, expected_symbols, runtime_root):
                print("\nNo fresh temp snapshot was generated for this basket; report step skipped to avoid stale permanent data.")
                return 1

            if manifest_path:
                snapshot_path = (runtime_root / "logs" / "snapshots" / f"{run_date}_{region}.json") if runtime_root else (workspace_root / "logs" / "snapshots" / f"{run_date}_{region}.json")
                snapshot_rows = load_json_rows(snapshot_path)
                try:
                    with Path(manifest_path).open("r", encoding="utf-8") as f:
                        selection_payload = json.load(f)
                    out_dir = snapshot_path.parent
                    context_path = write_seven_layer_view(
                        workspace_root=workspace_root,
                        out_dir=out_dir,
                        selection_payload=selection_payload,
                        snapshot_rows=snapshot_rows,
                        run_date=run_date,
                    )
                    print(f"7S context saved -> {context_path}")
                except Exception as exc:
                    print(f"[WARN] Failed to write 7S context: {exc}")

    return 0 if all(rc == 0 for rc in exit_codes) else 1
