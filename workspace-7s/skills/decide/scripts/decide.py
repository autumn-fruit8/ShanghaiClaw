"""Decide — Orchestrator for S5 (self-portrait) + S6 (stake) decision layer.

Pure orchestration only:
  - Route commands to domain scripts (S5/S6)
  - Format and push outputs to Feishu
  - NO domain logic (drift computation, action recommendation)

Usage:
    decide.py decide --plan cn_hb           # Orchestrate: Plan + Stake + Output
    decide.py self-portrait show --plan-id cn_hb   # Passthrough: S5 Plan CRUD
    decide.py stake --plan cn_hb                   # Passthrough: S6 drift
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]  # workspace-7s/
SKILL_ROOT = WORKSPACE_ROOT / "skills" / "decide"

# Add workspace root to sys.path BEFORE other imports to ensure correct config module
sys.path.insert(0, str(WORKSPACE_ROOT))

# Add scripts/ to path for internal imports
SKILL_SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

# Internal modules (only for Plan loading, NO domain logic)
from dao.models import Plan, Position

# Shared config layer
from config import PLANS_DIR, POSITIONS_DIR


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestrator: Route to domain scripts
# ═══════════════════════════════════════════════════════════════════════════════

def _get_default_output_path(plan_id: str, snapshot_date: str, is_json: bool) -> Path:
    """Get default output path in outputs/ directory."""
    suffix = ".json" if is_json else ".md"
    filename = f"{snapshot_date}_{plan_id}_decide{suffix}"
    return SKILL_ROOT / "outputs" / filename


def cmd_decide(args: argparse.Namespace) -> int:
    """Orchestrate full decision workflow: Plan metadata + S6 drift + Feishu push.

    This is a pure orchestrator — delegates all domain logic to stake.py.
    """
    plan_id = args.plan
    version = args.version
    snapshot_date = args.date or date.today().isoformat()  # Use today if not provided

    # ── Step 1: S5 — Load investor constraints (Plan metadata only) ───────────
    if version is None:
        versions = _list_plan_versions(plan_id)
        if not versions:
            sys.exit(f"Error: plan '{plan_id}' not found.")
        version = max(versions)

    try:
        plan = Plan.load(plan_id, version, PLANS_DIR)
    except FileNotFoundError:
        sys.exit(f"Error: plan '{plan_id}' v{version} not found.")

    # ── Step 2: S6 — Delegate to stake.py (domain logic) ─────────────────────
    stake_script = SKILL_SCRIPTS / "stake.py"

    # Run stake.py with JSON output to capture drift + recommendations
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = [
            sys.executable, str(stake_script),
            "--plan", plan_id,
            "--version", str(version),
            "--format", "json",
            "--output", tmp_path,
        ]
        if snapshot_date is not None:
            cmd.extend(["--date", snapshot_date])
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        stake_data = json.loads(Path(tmp_path).read_text())
    except subprocess.CalledProcessError as e:
        sys.exit(f"Error running stake.py: {e.stderr}")
    except json.JSONDecodeError:
        sys.exit(f"Error: stake.py returned invalid JSON")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # ── Step 2b: Concentration overlap analysis (optional) ──────────────────
    concentration_section = ""
    overlap = {}
    if args.concentration:
        try:
            snapshot_date_obj = date.fromisoformat(snapshot_date)
            position = Position.load(plan_id, snapshot_date_obj, POSITIONS_DIR)
            # Import from holdings module (sibling directory)
            from holdings.overlap import compute_overlap
            from holdings import format_overlap_report
            overlap = compute_overlap(plan, position)
            concentration_section = format_overlap_report(overlap)
        except Exception as e:
            concentration_section = f"\n⚠️ Concentration analysis unavailable: {e}\n"

    # ── Step 3: Orchestrate output (decide-specific: region, currency, Feishu) ─
    result = {
        "plan_id": plan_id,
        "plan_version": version,
        "snapshot_date": snapshot_date,
        "region": plan.region,
        "currency": plan.currency,
        "target_market_value": plan.target_market_value,
        "current_market_value": stake_data.get("total_market_value", 0),
        "drift_threshold": stake_data.get("drift_threshold", 0.05),
        "recommendations": stake_data.get("recommendations", []),
        "generated_at": datetime.now().isoformat(),
    }

    # Build summary table for Feishu message
    currency_sym = "¥" if plan.currency == "CNY" else "$"
    summary_lines = [
        f"📋 **{plan_id} v{version}** — {snapshot_date}",
        f"市值 {currency_sym}{result['current_market_value']:,.0f} / 目标 {currency_sym}{result['target_market_value']:,.0f} "
        f"(总进度 {result['current_market_value'] / result['target_market_value']:.1%})",
        f"",
        f"| Symbol | Name | 权重 | 漂移 | 进度 |",
        f"|--------|------|-----:|-----:|-----:|",
    ]
    for r in result["recommendations"]:
        summary_lines.append(
            f"| {r['symbol']} | {r['name']} | {r['current_weight']:.1%} | {r['drift']:+.1%} | {r['building_progress']:.1%} |"
        )
    # Synthesize concentration insights for the Feishu message
    if args.concentration and overlap.get("etf_count", 0) > 0:
        flagged = [o for o in overlap["overlaps"] if o["flagged"]]
        top3 = sorted(overlap["overlaps"], key=lambda x: x["combined_weight"], reverse=True)[:3]
        summary_lines.append("")
        summary_lines.append(f"📊 持仓集中度：{overlap['holdings_count']} 只底层股票 / {overlap['etf_count']} 只 ETF")
        if flagged:
            summary_lines.append(f"⚠️ **{len(flagged)} 只超过 {overlap['threshold']:.0f}% 阈值：**")
            for f in flagged:
                srcs = ", ".join([s['etf'] for s in f['sources']])
                summary_lines.append(f"  · {f['symbol']} {f['combined_weight']:.1f}% → 来自 {srcs}")
        top3_str = "  |  ".join([f"{t['symbol']} {t['combined_weight']:.1f}%" for t in top3])
        summary_lines.append(f"📈 前 3 持仓：{top3_str}")
        summary_lines.append("")

    summary = "\n".join(summary_lines)

    if args.concentration:
        result["concentration"] = overlap
    output = json.dumps(result, indent=2, ensure_ascii=False) if args.json else _render_markdown(result, concentration_section)

    # Auto-save to outputs/ if no explicit output path, then push to Feishu
    if args.output is None:
        output_path = _get_default_output_path(plan_id, snapshot_date, args.json)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(output)
        print(f"Output written to: {output_path}")
        # Push summary text + file to Feishu
        _push_to_feishu(output_path, summary)
        print(output)  # Also print to stdout for the agent to read
    else:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Output written to: {args.output}")
        print(output)

    return 0


def _render_markdown(result: dict, concentration_section: str = "") -> str:
    target_mv = result.get('target_market_value')
    lines = [
        f"# Decide — {result['plan_id']} v{result['plan_version']}",
        f"",
        f"**Snapshot**: {result['snapshot_date']}  |  **Region**: {result['region']}  |  **Currency**: {result['currency']}",
        f"",
        f"| Metric | Value |",
        f"|---|---:|",
        f"| Current Market Value | {result['current_market_value']:,.2f} |",
        f"| Target Market Value | {target_mv:,.2f} |" if target_mv else f"| Target Market Value | N/A |",
        f"| Drift Threshold | {result['drift_threshold']:.1%} |",
        f"",
        f"## Recommendations",
        f"",
        f"| Symbol | Action | Shares | Price | Market Value | Rel. Weight | Target | Drift | Build Progress | Funding Gap | Signal | LDev |",
        f"|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in result["recommendations"]:
        action = r['action']
        drift_action = r.get('drift_action', action)
        override = r.get('signal_override', False)
        if override:
            action_display = f"~~{drift_action}~~→**{action}**"
        else:
            action_display = f"**{action}**"
        species = r.get('species', '')
        ldev = r.get('signal_ldev')
        ldev_s = f"{ldev:+.2f}σ" if ldev is not None else ""
        lines.append(
            f"| {r['symbol']} | {action_display} | {r['shares']:.0f} | {r['current_price']:.4f} | "
            f"{r['market_value']:,.2f} | {r['current_weight']:.2%} | {r['target_weight']:.2%} | {r['drift']:+.2%} | "
            f"{r['building_progress']:.2%} | {r['funding_gap']:+,.2f} | {species} | {ldev_s} |"
        )
    if concentration_section:
        lines.append(concentration_section)
    return "\n".join(lines)


def _push_to_feishu(file_path: Path, summary: str = "") -> bool:
    """Push summary text + file to Feishu group via OpenClaw."""
    try:
        import os
        import shutil
        import subprocess
        import shlex
        # Use module-level WORKSPACE_ROOT (already resolved correctly)
        _ENV_PATH = WORKSPACE_ROOT / ".env"
        if _ENV_PATH.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(_ENV_PATH)
            except ImportError:
                pass  # dotenv not installed, rely on system env vars

        chat_id = os.environ.get("SEVENS_FEISHU_CHAT_ID")
        if not chat_id:
            raise ValueError("SEVENS_FEISHU_CHAT_ID not set — Feishu push cannot proceed")

        # Add nvm node to PATH
        env = os.environ.copy()
        nvm_node = "/root/.nvm/versions/node/v22.22.0/bin"
        env["PATH"] = f"{nvm_node}:{env.get('PATH', '')}"

        # Send summary text message first (if provided)
        if summary:
            msg_cmd = (
                f'openclaw message send --channel feishu --target {chat_id} '
                f'--message {shlex.quote(summary)}'
            )
            subprocess.run(shlex.split(msg_cmd), capture_output=True, text=True, env=env)

        # Then send the file
        media_dir = Path("/root/.openclaw/media")
        media_dir.mkdir(parents=True, exist_ok=True)
        media_path = media_dir / file_path.name
        shutil.copy2(file_path, media_path)

        file_cmd = f'openclaw message send --channel feishu --target {chat_id} --media "{media_path}"'
        result = subprocess.run(shlex.split(file_cmd), capture_output=True, text=True, env=env)
        if result.returncode != 0:
            raise Exception(result.stderr)
        print(f"Pushed to Feishu: {file_path.name}")
        return True
    except Exception as e:
        print(f"Failed to push to Feishu: {e}", file=sys.stderr)
        return False


def _list_plan_versions(plan_id: str) -> list[int]:
    plan_dir = PLANS_DIR / plan_id
    if not plan_dir.exists():
        return []
    return sorted([
        int(f.stem[1:]) for f in plan_dir.glob("v*.json")
        if f.stem[1:].isdigit()
    ])


# ═══════════════════════════════════════════════════════════════════════════════
# Admin subcommands (passthrough to dedicated scripts)
# ═══════════════════════════════════════════════════════════════════════════════

def _run_script(script_name: str, extra_args: list[str] = None) -> int:
    """Run a sub-script via subprocess (for admin operations)."""
    script = SKILL_ROOT / "scripts" / script_name
    cmd = [sys.executable, str(script)] + (extra_args or [])
    return subprocess.run(cmd, cwd=str(WORKSPACE_ROOT)).returncode


def cmd_self_portrait(args) -> int:
    return _run_script("self_portrait.py", args.subcommand)


def cmd_stake(args) -> int:
    return _run_script("stake.py", args.subcommand)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Decide — S5 Self-portrait + S6 Stake decision layer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  decide.py decide --plan cn_hb                      # Full decision workflow
  decide.py decide --plan cn_hb --date 2026-05-01   # Use specific date
  decide.py decide --plan cn_hb --json               # JSON output
  decide.py self-portrait show --plan-id cn_hb       # Show plan details
  decide.py stake --plan cn_hb                       # Compute drift only
        """
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── Primary: Full decision workflow ──────────────────────────────────────
    p = sub.add_parser("decide", help="Full decision: load plan → load position → compute drift")
    p.add_argument("--plan", required=True, help="Plan ID (e.g. cn_hb, us_hb)")
    p.add_argument("--version", type=int, default=None, help="Plan version (default: latest)")
    p.add_argument("--date", default=None, help="Position snapshot date (default: today)")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument("--output", default=None, help="Write output to file")
    p.add_argument("--concentration", action="store_true",
                   help="Show holdings overlap analysis (top-10 holdings cross-referenced across ETFs)")

    # ── Admin: S5 Plan CRUD ──────────────────────────────────────────────────
    p_sp = sub.add_parser("self-portrait", help="S5 — Plan CRUD (show/create/update/delete/list)")
    p_sp.add_argument("subcommand", nargs=argparse.REMAINDER,
                      help="Pass through to self_portrait.py")

    # ── Admin: S6 drift only ────────────────────────────────────────────────
    p_st = sub.add_parser("stake", help="S6 — Compute drift only")
    p_st.add_argument("subcommand", nargs=argparse.REMAINDER,
                      help="Pass through to stake.py")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "decide": cmd_decide,
        "self-portrait": cmd_self_portrait,
        "stake": cmd_stake,
    }

    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
