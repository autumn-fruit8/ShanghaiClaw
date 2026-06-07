#!/usr/bin/env python3
"""
run_report.py — 7S report layer.

Pure view layer over strategy output files. Never re-computes signals.

Usage:
    python3 skills/view_report/scripts/run_report.py --region us --type daily
    python3 skills/view_report/scripts/run_report.py --region cn --type weekly
    python3 skills/view_report/scripts/run_report.py --region all --type daily
    python3 skills/view_report/scripts/run_report.py --region us --date 2026-03-08
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ── paths ────────────────────────────────────────────────────────────────────

_SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = _SCRIPT_DIR.parents[2]  # workspace-7s root
sys.path.insert(0, str(WORKSPACE_ROOT))

from config import SNAPSHOTS_DIR, REPORTS_DIR, BACKTEST_DIR, HOLDINGS_DIR

# ── signal helpers ────────────────────────────────────────────────────────────

# Severity order (higher = more urgent)
_SIGNAL_ORDER = {"NEUTRAL": 0, "WARNING": 1, "BEARISH": 1, "OPPORTUNITY": 1, "DANGER": 2}
_ALERT_CLASSES = {"WARNING", "DANGER", "BEARISH", "OPPORTUNITY"}


def _signal_class(signal: str) -> str:
    """Extract the bracketed class from a Signal string.
    '[WARNING] MELT-UP (LDev > 2.5)' → 'WARNING'
    """
    m = re.match(r"\[([A-Z ]+)\]", signal or "")
    return m.group(1).strip() if m else "UNKNOWN"


def _parse_ldev_rsi(signal_action: str) -> tuple[float | None, float | None]:
    """Extract LDev and RSI from Signal_Action string.
    e.g. '[WARNING] MELT-UP (LDev > 2.5) | LDev:2.73σ | RSI:54.2'
    """
    ldev = rsi = None
    m = re.search(r"LDev:([-\d.]+)", signal_action or "")
    if m:
        ldev = float(m.group(1))
    m = re.search(r"RSI:([\d.]+)", signal_action or "")
    if m:
        rsi = float(m.group(1))
    return ldev, rsi


# ── snapshot loading ──────────────────────────────────────────────────────────


def _load_snapshot(region: str, run_date: date) -> list[dict] | None:
    path = SNAPSHOTS_DIR / f"{run_date}_{region}.json"
    if not path.exists():
        return None
    with path.open() as f:
        return json.load(f)


def _find_prev_snapshot(region: str, run_date: date) -> list[dict] | None:
    """Try run_date-1, -2, -3 to handle weekends and holidays."""
    for offset in (1, 2, 3):
        snap = _load_snapshot(region, run_date - timedelta(days=offset))
        if snap is not None:
            return snap
    return None


def _load_monthly_backtest(region: str) -> list[dict] | None:
    """Load the latest backtest snapshot for the weekly report."""
    path = BACKTEST_DIR / f"latest_{region}.json"
    if not path.exists():
        return None
    try:
        with path.open() as f:
            return json.load(f)
    except Exception:
        return None


def _load_holdings_changes(region: str) -> list[dict] | None:
    """Load holdings diff data from _meta.json for assets in a region.

    Returns list of assets with turnover > 0, sorted by turnover descending.
    """
    meta_path = HOLDINGS_DIR / "_meta.json"
    if not meta_path.exists():
        return None
    try:
        with meta_path.open() as f:
            meta = json.load(f)
    except Exception:
        return None

    changes = []
    for etf_symbol, entry in meta.items():
        turnover = entry.get("turnover_pct", 0)
        if turnover > 0:
            new_stocks = entry.get("new_stocks", [])
            removed = entry.get("removed_stocks", [])
            fetched = entry.get("fetched", "?")
            changes.append({
                "symbol": etf_symbol,
                "turnover": turnover,
                "new_count": len(new_stocks),
                "removed_count": len(removed),
                "new_stocks": ", ".join(new_stocks[:5]),
                "removed_stocks": ", ".join(removed[:5]),
                "date": fetched,
            })
    return sorted(changes, key=lambda x: x["turnover"], reverse=True) if changes else None


# ── formatting helpers ────────────────────────────────────────────────────────


def _pct(v) -> str:
    if v is None:
        return "   N/A"
    return f"{v * 100:+6.1f}%"


def _fmt(v, decimals=2) -> str:
    if v is None:
        return "  N/A"
    return f"{v:.{decimals}f}"


# ── daily briefing (REQ-JAR-RPT-01) ──────────────────────────────────────────


def _daily(region: str, run_date: date) -> None:
    today = _load_snapshot(region, run_date)
    if today is None:
        print(
            f"⚠️  No snapshot for {run_date} ({region.upper()}). "
            f"Run `run_strategy.py --region {region}` first."
        )
        return

    yesterday = _find_prev_snapshot(region, run_date)
    prev_by_sym: dict[str, dict] = {a["symbol"]: a for a in yesterday} if yesterday else {}
    prev_date = yesterday[0].get("date", "?") if yesterday else None

    n = len(today)
    sep = "━" * 50

    print(f"\n📊 DAILY BRIEFING — {run_date} ({region.upper()})")
    print(sep)
    print(f"   {n} assets tracked")
    if prev_date:
        print(f"   Comparing with: {prev_date}")
    print()

    # ── signal changes ────────────────────────────────────────────────────────
    changes: list[tuple] = []
    for asset in today:
        sym = asset["symbol"]
        prev = prev_by_sym.get(sym)
        if prev is None:
            continue
        old_cls = _signal_class(prev.get("signal", ""))
        new_cls = _signal_class(asset.get("signal", ""))
        if old_cls != new_cls:
            changes.append((sym, old_cls, new_cls, asset.get("signal_action", "")))

    if changes:
        print(f"🔔 Signal changes ({len(changes)} asset{'s' if len(changes) != 1 else ''}):")
        for sym, old, new, action in changes:
            ldev, rsi = _parse_ldev_rsi(action)
            extras = []
            if ldev is not None:
                extras.append(f"LDev:{ldev:.2f}σ")
            if rsi is not None:
                extras.append(f"RSI:{rsi:.1f}")
            detail = "  " + ", ".join(extras) if extras else ""
            print(f"  {sym:<8}  {old} → [{new}]{detail}")
    elif yesterday:
        print("✅ No signal changes since last snapshot.")
    else:
        print("ℹ️  No previous snapshot found — first run, no diff available.")
    print()

    # ── active alerts ─────────────────────────────────────────────────────────
    alerts = [
        (a, _signal_class(a["signal"]))
        for a in today
        if _signal_class(a["signal"]) in _ALERT_CLASSES
    ]
    if alerts:
        alerts_sorted = sorted(
            alerts,
            key=lambda x: (_SIGNAL_ORDER.get(x[1], 0), x[0].get("symbol", "")),
            reverse=True,
        )
        print(f"⚠️  Active alerts ({len(alerts)} assets):")
        for asset, cls in alerts_sorted:
            ldev, rsi = _parse_ldev_rsi(asset.get("signal_action", ""))
            ldev_str = f"LDev:{ldev:.2f}σ" if ldev is not None else ""
            rsi_str = f"RSI:{rsi:.1f}" if rsi is not None else ""
            detail = " | ".join(x for x in [ldev_str, rsi_str] if x)
            name_trunc = asset.get("name", "")[:34]
            print(f"  {asset['symbol']:<8}  [{cls:<11}]  {name_trunc:<34}  {detail}")
    else:
        print("✅ No active alerts.")
    print()

    # ── full signal table ─────────────────────────────────────────────────────
    print("| Symbol   | Type       | Signal                              | LDev   | RSI  |")
    print("|----------|------------|-------------------------------------|--------|------|")
    for asset in today:
        sym = f"{asset['symbol']:<8}"
        typ = f"{asset.get('Type', '')[:10]:<10}"
        sig = asset.get("signal", "")
        # trim signal text to fit column
        sig_trunc = sig[:35]
        ldev, rsi = _parse_ldev_rsi(asset.get("signal_action", ""))
        ldev_col = f"{ldev:.2f}σ" if ldev is not None else "  N/A"
        rsi_col = f"{rsi:.1f}" if rsi is not None else " N/A"
        print(f"| {sym} | {typ} | {sig_trunc:<35} | {ldev_col:>6} | {rsi_col:>4} |")
    print()

    # ── footer ────────────────────────────────────────────────────────────────
    stale = [a["symbol"] for a in today if a.get("date") != str(run_date)]
    if stale:
        print(f"⚠️  Stale price data detected: {', '.join(stale)}")
    print(f"   Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")


# ── weekly review (REQ-JAR-RPT-02) ───────────────────────────────────────────


def _weekly(region: str, run_date: date) -> None:
    today = _load_snapshot(region, run_date)
    if today is None:
        print(
            f"⚠️  No snapshot for {run_date} ({region.upper()}). "
            f"Run `run_strategy.py --region {region}` first."
        )
        return

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    lines.append(f"# 📅 Weekly Review — Week ending {run_date} ({region.upper()})")
    lines.append(f"*Generated: {generated}*")
    lines.append("")

    # ── 10-year backtest performance table (from monthly backtest snapshot) ──
    monthly = _load_monthly_backtest(region.lower())
    if monthly:
        # Get actual backtest period from first asset
        bt = monthly[0].get("backtest", monthly[0])
        bt_start = bt.get("period_start", "?")
        bt_end   = bt.get("period_end", "?")
        bt_years = bt.get("period_years", "?")
        if isinstance(bt_years, (int, float)):
            lines.append(f"## Backtest: Strategy vs Buy & Hold ({bt_years:.1f}yr: {bt_start} to {bt_end})")
        else:
            lines.append(f"## Backtest: Strategy vs Buy & Hold")
        lines.append("")
        lines.append("| Symbol | Name | Type | S.Ret | S.DD | S.Sharpe | B.Ret | B.DD | B.Sharpe |")
        lines.append("|--------|------|------|------:|-----:|---------:|------:|-----:|---------:|")
        for asset in monthly:
            bt  = asset.get("backtest", asset)
            sym = bt.get("symbol", "?")
            name = bt.get("name", "")[:12]
            typ  = bt.get("type", "")
            sr   = _pct(bt.get("strategy_ret")).strip()
            sd   = _pct(bt.get("strategy_dd")).strip()
            ss   = _fmt(bt.get("strat_sharpe")).strip()
            br   = _pct(bt.get("buyhold_ret")).strip()
            bd   = _pct(bt.get("buyhold_dd")).strip()
            bs   = _fmt(bt.get("buyhold_sharpe")).strip()
            lines.append(f"| {sym} | {name} | {typ} | {sr} | {sd} | {ss} | {br} | {bd} | {bs} |")

        # Trend warnings from monthly data
        warnings = [a for a in monthly if a.get("delta", {}).get("deltas", {}).get("trend_warning")]
        if warnings:
            lines.append("")
            lines.append("**⚠️ Degradation Signals:**")
            for w in warnings:
                warn = w["delta"]["deltas"]["trend_warning"]
                sym = w.get("symbol", "?")
                lines.append(f"- **{sym}**: {warn}")
        lines.append("")
    else:
        lines.append(f"## 10-Year Backtest: Strategy vs Buy & Hold")
        lines.append("")
        lines.append("*No backtest data available. Run `run_backtest.py --region {region}` to generate.*")
        lines.append("")

    # ── holdings changes (from holdings cache _meta.json) ──────────────────
    holdings_changes = _load_holdings_changes(region.lower())
    if holdings_changes:
        lines.append("## 📦 持仓变动")
        lines.append("")
        lines.append("| ETF | 换手率 | 新增 | 剔除 |")
        lines.append("|-----|:-----:|:----|:----|")
        for c in holdings_changes:
            sym = c["symbol"]
            t = f"{c['turnover']:.0f}%"
            new = c.get("new_stocks", "-")[:40]
            rem = c.get("removed_stocks", "-")[:40]
            lines.append(f"| {sym} | {t} | {new} | {rem} |")
        lines.append("")

        # News hits for ETFs with changes
        meta_path = HOLDINGS_DIR / "_meta.json"
        if meta_path.exists():
            try:
                with meta_path.open() as f:
                    meta = json.load(f)
                for c in holdings_changes:
                    sym = c["symbol"]
                    news_hits = meta.get(sym, {}).get("news_hits", [])
                    if news_hits:
                        lines.append(f"**📡 {sym} 相关新闻**")
                        for h in news_hits[:3]:
                            kw = ", ".join(h.get("keywords", []))
                            title = h.get("title", "")[:80]
                            source = h.get("source", "")
                            date_str = h.get("date", "")[:10]
                            lines.append(f"- [{kw}] *{source}* {date_str}: {title}")
                        lines.append("")
            except Exception:
                pass

    # ── current signals ───────────────────────────────────────────────────────
    lines.append("## Current Signals")
    lines.append("")
    lines.append("| Symbol | Name | Type | Signal | LDev | RSI |")
    lines.append("|--------|------|------|--------|-----:|----:|")
    for asset in today:
        sym  = asset["symbol"]
        name = asset.get("name", "")
        typ  = asset.get("type", "")
        sig  = asset.get("signal", "")
        ldev, rsi = _parse_ldev_rsi(asset.get("signal_action", ""))
        ldev_col = f"{ldev:.2f}σ" if ldev is not None else "N/A"
        rsi_col  = f"{rsi:.1f}"   if rsi  is not None else "N/A"
        lines.append(f"| {sym} | {name} | {typ} | {sig} | {ldev_col} | {rsi_col} |")
    lines.append("")

    # ── regime overview ───────────────────────────────────────────────────────
    lines.append("## Regime Overview")
    lines.append("")
    lines.append("| Class | Count | Bar |")
    lines.append("|-------|------:|-----|")
    regime_counts = Counter(_signal_class(a["signal"]) for a in today)
    regime_order = ["NEUTRAL", "BULLISH", "OPPORTUNITY", "WARNING", "BEARISH", "DANGER", "UNKNOWN"]
    for cls in regime_order:
        count = regime_counts.get(cls, 0)
        if count:
            bar = "█" * count
            lines.append(f"| {cls} | {count} | {bar} |")
    lines.append("")

    # ── write file ────────────────────────────────────────────────────────────
    out_dir = REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_date}_weekly_{region}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ Weekly report saved → {out_path}")


# ── entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="7S report — daily briefing or weekly review"
    )
    parser.add_argument("--region", default="us", help="cn | us | all")
    parser.add_argument(
        "--type",
        dest="report_type",
        default="daily",
        choices=["daily", "weekly"],
        help="Report type (default: daily)",
    )
    parser.add_argument(
        "--date",
        default=str(date.today()),
        help="Reference date YYYY-MM-DD (default: today)",
    )
    args = parser.parse_args()

    run_date = date.fromisoformat(args.date)
    regions = ["cn", "us"] if args.region == "all" else [args.region.lower()]
    fn = _daily if args.report_type == "daily" else _weekly

    for i, region in enumerate(regions):
        fn(region, run_date)
        if i < len(regions) - 1:
            print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    main()
