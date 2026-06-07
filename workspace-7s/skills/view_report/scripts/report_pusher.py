#!/usr/bin/env python3
"""
report_pusher.py — Unified Daily/Weekly Report Push

Single entry point for:
1. Generate markdown report
2. Generate market map (PNG)
3. Push markdown + PNG to Feishu (no PDF)

Usage:
    python3 skills/view_report/scripts/report_pusher.py --type daily
    python3 skills/view_report/scripts/report_pusher.py --type weekly
    python3 skills/view_report/scripts/report_pusher.py --type daily --dry-run
    python3 skills/view_report/scripts/report_pusher.py --region cn --type daily
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Auto-load .env from workspace root
try:
    from dotenv import load_dotenv
    _SCRIPT_DIR = Path(__file__).resolve().parent
    _WORKSPACE_ROOT = _SCRIPT_DIR.parents[2]
    _ENV_PATH = _WORKSPACE_ROOT / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH)
        print(f"Loaded .env from {_ENV_PATH}")
except ImportError:
    pass  # dotenv not installed, rely on system env vars

# PDF generation
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

# Matplotlib for market map
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

# ── paths ────────────────────────────────────────────────────────────────────

_SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = _SCRIPT_DIR.parents[2]  # workspace-7s root
_RUNTIME_ROOT = Path(os.getenv("SEVENS_RUNTIME_ROOT", "")).expanduser() if os.getenv("SEVENS_RUNTIME_ROOT") else None
_BASE_ROOT = _RUNTIME_ROOT or WORKSPACE_ROOT
_SNAPSHOT_DIR = _BASE_ROOT / "logs" / "snapshots"
_REPORT_DIR = _BASE_ROOT / "logs" / "reports"
_REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ── signal helpers ───────────────────────────────────────────────────────────

_ALERT_CLASSES = {"WARNING", "DANGER", "BEARISH", "OPPORTUNITY"}


def _fmt_vol_signal(a: dict) -> str:
    """Format volume signal for report display."""
    # Skip volume for STEADY/BOND — not timing-relevant
    species = a.get("type", "")
    if species in ("STEADY", "BOND"):
        return "—"

    vol_signal = a.get("vol_signal", "")
    if not vol_signal or vol_signal == "N/A":
        return "N/A"
    vol_icon = {
        "CONFIRM_BULLISH": "🟢",
        "CONFIRM_BEARISH": "🔴",
        "EXHAUSTION": "🔄",
        "WEAKEN": "⚠️",
        "ABNORMAL": "💥",
        "NORMAL": "✅",
        "QUIET": "💤",
    }.get(vol_signal, "⚪")
    return f"{vol_icon}{vol_signal}"


def _signal_class(signal: str) -> str:
    """Extract the bracketed class from a Signal string."""
    m = re.match(r"\[([A-Z_ ]+)\]", signal or "")
    return m.group(1).strip() if m else "UNKNOWN"


def _parse_ldev_rsi(asset: dict) -> tuple[float | None, float | None]:
    """Extract LDev and RSI from asset dict (snapshot fields)."""
    ldev = asset.get("ldev") or asset.get("LDev") or asset.get("z")
    rsi = asset.get("rsi") or asset.get("RSI")
    if ldev is not None:
        try:
            ldev = float(ldev)
        except (ValueError, TypeError):
            ldev = None
    if rsi is not None:
        try:
            rsi = float(rsi)
        except (ValueError, TypeError):
            rsi = None
    return ldev, rsi


# ── snapshot loading ──────────────────────────────────────────────────────────


def _load_snapshot(region: str, run_date: date) -> list[dict] | None:
    """Load snapshot for a given date."""
    path = _SNAPSHOT_DIR / f"{run_date}_{region}.json"
    if not path.exists():
        return None
    try:
        with path.open() as f:
            return json.load(f)
    except Exception:
        return None


def _find_latest_snapshot(region: str, max_days: int = 5) -> tuple[list[dict] | None, date | None]:
    """Find the most recent valid snapshot within max_days."""
    today = date.today()
    for offset in range(max_days + 1):
        check_date = today - timedelta(days=offset)
        snap = _load_snapshot(region, check_date)
        if snap is not None:
            return snap, check_date
    return None, None


# ── market map generation ───────────────────────────────────────────────────

# Import from generate_market_map.py (Single Source of Truth)
try:
    import sys
    sys.path.insert(0, str(_SCRIPT_DIR))
    from generate_market_map import generate_from_data as _generate_from_data
    
    def generate_market_map(cn_data, us_data, output_path=None, region="all"):
        """Wrapper to maintain backward compatibility with tests."""
        if output_path is None:
            output_path = _REPORT_DIR / f"market_map_{date.today()}_{region}.png"
        return _generate_from_data(cn_data, us_data, output_path)
except ImportError as e:
    # Fallback if import fails
    print(f"Warning: could not import generate_market_map: {e}", file=sys.stderr)
    def generate_market_map(cn_data, us_data, output_path=None):
        return None


# ── report building ──────────────────────────────────────────────────────────


def _build_region_report(region: str, run_date: date) -> dict:
    """Build report data for a region."""
    result = {
        "region": region,
        "snapshot_date": None,
        "is_stale": False,
        "staleness_days": 0,
        "asset_count": 0,
        "signals": {"NEUTRAL": 0, "BULLISH": 0, "OPPORTUNITY": 0, "WARNING": 0, "BEARISH": 0, "DANGER": 0},
        "alerts": [],
        "assets": [],
        "error": None
    }
    
    snapshot = _load_snapshot(region, run_date)
    
    if snapshot is not None:
        result["snapshot_date"] = str(run_date)
    else:
        fallback_date = (run_date - timedelta(days=1))
        snapshot = _load_snapshot(region, fallback_date)
        if snapshot is not None:
            result["snapshot_date"] = str(fallback_date)
            result["is_stale"] = True
            result["staleness_days"] = 1
        else:
            snapshot, found_date = _find_latest_snapshot(region)
            if snapshot is not None:
                result["snapshot_date"] = str(found_date)
                result["is_stale"] = True
                result["staleness_days"] = (run_date - found_date).days
            else:
                result["error"] = f"No snapshot found for {region.upper()}"
                return result
    
    result["asset_count"] = len(snapshot)
    
    for asset in snapshot:
        cls = _signal_class(asset.get("signal", ""))
        result["signals"][cls] = result["signals"].get(cls, 0) + 1
        
        ldev, rsi = _parse_ldev_rsi(asset)
        asset_info = {
            "symbol": asset["symbol"],
            "name": asset.get("name", ""),
            "type": asset.get("type", ""),
            "signal": asset.get("signal", ""),
            "signal_class": cls,
            "ldev": ldev,
            "rsi": rsi,
            "vol_signal": asset.get("vol_signal", ""),
            "vol_ratio": asset.get("vol_ratio", 0),
        }
        result["assets"].append(asset_info)
        if cls in _ALERT_CLASSES:
            result["alerts"].append(asset_info)
    
    return result


def _format_daily_markdown(cn_report: dict, us_report: dict, run_date: date, region: str = "all") -> str:
    """Format daily report as markdown - compact layout for single page."""
    lines = []
    lines.append(f"# Daily Market Briefing — {run_date}")
    lines.append("")
    
    # Compact signal summary (one line per region)
    lines.append("## Signal Overview")
    lines.append("")
    
    cn = cn_report.get("signals", {})
    us = us_report.get("signals", {})
    
    if not cn_report.get("error"):
        lines.append(f"🇨🇳 CN: 🟢BULLISH:{cn.get('BULLISH',0)} | ⚪NEUTRAL:{cn.get('NEUTRAL',0)} | 🔵OPP:{cn.get('OPPORTUNITY',0)} | 🟠BEARISH:{cn.get('BEARISH',0)} | 🔴DANGER:{cn.get('DANGER',0)}")
    else:
        lines.append(f"🇨🇳 CN: 🔴 No data")
    
    if not us_report.get("error"):
        lines.append(f"🇺🇸 US: 🟢BULLISH:{us.get('BULLISH',0)} | ⚪NEUTRAL:{us.get('NEUTRAL',0)} | 🔵OPP:{us.get('OPPORTUNITY',0)} | 🟠BEARISH:{us.get('BEARISH',0)} | 🔴DANGER:{us.get('DANGER',0)}")
    else:
        lines.append(f"🇺🇸 US: 🔴 No data")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # All assets detail - combined table per region (compact)
    if not cn_report.get("error"):
        assets_cn = cn_report.get("assets", [])
        if assets_cn:
            lines.append("## 📊 CN Assets")
            lines.append("")
            lines.append("| Symbol | Name | Signal | Volume | LDev | RSI |")
            lines.append("|--------|------|--------|--------|------|-----|")
            for a in assets_cn:
                ldev_str = f"{a['ldev']:+.2f}σ" if a['ldev'] is not None else "N/A"
                rsi_str = f"{a['rsi']:.1f}" if a['rsi'] is not None else "N/A"
                vol_str = _fmt_vol_signal(a)
                signal = a.get("signal_class", "NEUTRAL")
                # Emoji prefix for quick visual
                emoji = {"BULLISH": "🟢", "NEUTRAL": "⚪", "OPPORTUNITY": "🔵", "BEARISH": "🟠", "DANGER": "🔴", "WARNING": "⚠️"}.get(signal, "⚪")
                lines.append(f"| {a['symbol']} | {a['name'][:20]} | {emoji}{signal} | {vol_str} | {ldev_str} | {rsi_str} |")
            lines.append("")
    
    if not us_report.get("error"):
        assets_us = us_report.get("assets", [])
        if assets_us:
            lines.append("## 📊 US Assets")
            lines.append("")
            lines.append("| Symbol | Name | Signal | Volume | LDev | RSI |")
            lines.append("|--------|------|--------|--------|------|------|")
            for a in assets_us:
                ldev_str = f"{a['ldev']:+.2f}σ" if a['ldev'] is not None else "N/A"
                rsi_str = f"{a['rsi']:.1f}" if a['rsi'] is not None else "N/A"
                vol_str = _fmt_vol_signal(a)
                signal = a.get("signal_class", "NEUTRAL")
                emoji = {"BULLISH": "🟢", "NEUTRAL": "⚪", "OPPORTUNITY": "🔵", "BEARISH": "🟠", "DANGER": "🔴", "WARNING": "⚠️"}.get(signal, "⚪")
                lines.append(f"| {a['symbol']} | {a['name'][:20]} | {emoji}{signal} | {vol_str} | {ldev_str} | {rsi_str} |")
            lines.append("")
    
    lines.append("---")
    lines.append("")
    lines.append(f"📁 Full Report: logs/reports/{run_date}_daily_report_{region}.md")
    lines.append(f"🗺️ Market Map: logs/reports/{run_date}_market_map_{region}.png")
    
    # Data status
    status_parts = []
    if not cn_report.get("error"):
        status_parts.append("✅ CN Current" if not cn_report.get("is_stale") else f"⚠️ CN {cn_report['staleness_days']}d stale")
    if not us_report.get("error"):
        status_parts.append("✅ US Current" if not us_report.get("is_stale") else f"⚠️ US {us_report['staleness_days']}d stale")
    if status_parts:
        lines.append(f"Data: {' | '.join(status_parts)}")
    
    return "\n".join(lines)


def _format_weekly_markdown(cn_report: dict, us_report: dict, run_date: date) -> str:
    """Legacy weekly report (stub). Kept for backward compatibility."""
    return _format_weekly_v2_markdown(cn_report, us_report, run_date)


# ── weekly v2: vector report ──────────────────────────────────────────────


def _get_nearest_snapshot_date(target_date: date, region: str, lookback: int = 4) -> date | None:
    """Find the nearest trading date <= target_date with a snapshot file."""
    check = target_date
    for _ in range(lookback + 1):
        path = _SNAPSHOT_DIR / f"{check}_{region}.json"
        if path.exists():
            return check
        check -= timedelta(days=1)
    return None


def _load_position_snapshots(plan_id: str, target_date: date, window: int = 7) -> dict | None:
    """Load position snapshot for a plan nearest to target_date (±window days)."""
    # Try exact match
    path = WORKSPACE_ROOT / "logs" / "positions" / plan_id / f"{target_date}.json"
    if path.exists():
        try:
            with path.open() as f:
                return json.load(f)
        except Exception:
            pass
    # Then expand outward (forward then backward)
    for offset in range(1, window + 1):
        for try_date in (target_date + timedelta(days=offset), target_date - timedelta(days=offset)):
            path = WORKSPACE_ROOT / "logs" / "positions" / plan_id / f"{try_date}.json"
            if path.exists():
                try:
                    with path.open() as f:
                        return json.load(f)
                except Exception:
                    pass
    return None


def _classify_signal_severity(cls: str) -> int:
    """Return numeric severity for signal class ordering."""
    return {"BULLISH": 0, "NEUTRAL": 1, "OPPORTUNITY": 2, "WARNING": 3, "BEARISH": 3, "DANGER": 4}.get(cls, 1)


def _compute_vectors(this_snap: list[dict], last_snap: list[dict], region: str = "") -> list[dict]:
    """Compute week-over-week signal vectors for each asset."""
    last_map = {a["symbol"]: a for a in last_snap}
    vectors = []
    for a in this_snap:
        last = last_map.get(a["symbol"])
        if not last:
            continue

        cls_now = _signal_class(a.get("signal", ""))
        cls_last = _signal_class(last.get("signal", ""))
        ldev_now = a.get("ldev") or 0
        ldev_last = last.get("ldev") or 0
        ldev_delta = round(ldev_now - ldev_last, 2)
        rsi_now = a.get("rsi") or 50
        rsi_last = last.get("rsi") or 50
        ma60_now = a.get("ma60_pct") or 0
        ma60_last = last.get("ma60_pct") or 0

        # direction arrow
        if ldev_delta > 0.3:
            direction = "↑"
        elif ldev_delta < -0.3:
            direction = "↓"
        else:
            direction = "→"

        # MA60 crossover
        ma60_cross = None
        if ma60_last > 0 >= ma60_now:
            ma60_cross = "breakdown"
        elif ma60_last < 0 <= ma60_now:
            ma60_cross = "breakout"

        # Continuous danger tracking
        in_danger_now = cls_now == "DANGER"
        in_danger_last = cls_last == "DANGER"
        danger_streak = 2 if (in_danger_now and in_danger_last) else (1 if in_danger_now else 0)

        # Continuous warning/bearish tracking
        in_warning_now = cls_now in ("WARNING", "BEARISH")
        in_warning_last = cls_last in ("WARNING", "BEARISH")
        warning_streak = 2 if (in_warning_now and in_warning_last) else (1 if in_warning_now else 0)

        # Detect new opportunity entry (any non-OPPORTUNITY → OPPORTUNITY)
        new_opportunity = (cls_last != "OPPORTUNITY" and cls_now == "OPPORTUNITY")
        # Detect new bullish entry
        new_bullish = (cls_last != "BULLISH" and cls_now == "BULLISH")
        # Detect new danger entry
        new_danger = (cls_last != "DANGER" and cls_now == "DANGER")
        # Deep value: ldev below -1.5 and wasn't before
        new_deep_value = (ldev_now < -1.5 and ldev_last >= -1.5)

        vectors.append({
            "symbol": a["symbol"],
            "name": a.get("name", ""),
            "region": region,
            "type": a.get("type", ""),
            "ldev_now": round(ldev_now, 2),
            "ldev_last": round(ldev_last, 2),
            "ldev_delta": ldev_delta,
            "rsi_now": round(rsi_now, 1),
            "rsi_last": round(rsi_last, 1),
            "cls_now": cls_now,
            "cls_last": cls_last,
            "direction": direction,
            "ma60_now": round(ma60_now, 2),
            "ma60_last": round(ma60_last, 2),
            "ma60_cross": ma60_cross,
            "danger_streak": danger_streak,
            "warning_streak": warning_streak,
            "new_opportunity": new_opportunity,
            "new_bullish": new_bullish,
            "new_danger": new_danger,
            "new_deep_value": new_deep_value,
        })
    return vectors


def _build_plan_progress_section(plan_id: str, plan_label: str, region_label: str,
                                  this_date: date, last_date: date) -> tuple[str, list[dict]]:
    """Compare plan positions week-over-week. Returns (section_md, raw_data)."""
    this = _load_position_snapshots(plan_id, this_date)
    last = _load_position_snapshots(plan_id, last_date)
    lines = []
    raw = []

    if not this:
        return f"### {plan_label}\n\n*No position data for {plan_id}*\n\n", []

    total_now = this.get("total_market_value", 0)
    total_last = last.get("total_market_value", 0) if last else 0
    target = 500000 if "cn" in plan_id.lower() else 25000
    currency = "CNY" if "cn" in plan_id.lower() else "USD"

    if last:
        last_map = {p["symbol"]: p for p in last["positions"]}
    else:
        last_map = {}

    lines.append(f"### {plan_label}")
    lines.append(f"**{region_label}** | Total: **{currency} {total_now:,.0f}** (target {currency} {target:,})")
    lines.append("")
    lines.append("| Asset | Last Week | This Week | ΔWeight | Target | Direction |")
    lines.append("|-------|----------|----------|---------|--------|-----------|")

    changes = []
    for p in this["positions"]:
        sym = p["symbol"]
        w_now = p["market_value"] / total_now * 100 if total_now > 0 else 0
        lp = last_map.get(sym)
        w_last = lp["market_value"] / total_last * 100 if lp and total_last > 0 else 0
        delta_w = round(w_now - w_last, 1)

        # find target weight from decide file
        tgt = "25%"
        # direction: toward target or away
        target_val = 25.0  # assume equal-weight for now
        dist_last = abs(w_last - target_val)
        dist_now = abs(w_now - target_val)
        if dist_now < dist_last:
            dir_emoji = "🟢"
        elif dist_now > dist_last:
            dir_emoji = "🔴"
        else:
            dir_emoji = "⚪"

        show_name = "cn" in plan_id.lower()
        if show_name:
            name = p.get("name", sym)
            label = f"{name} ({sym})"
        else:
            label = sym
        lines.append(f"| {label} | {w_last:.1f}% | {w_now:.1f}% | {delta_w:+.1f}% | {tgt} | {dir_emoji} |")
        changes.append({
            "symbol": sym,
            "name": name if show_name else sym,
            "last_weight": round(w_last, 1),
            "now_weight": round(w_now, 1),
            "delta_weight": delta_w,
            "direction": dir_emoji,
            "target": target_val,
        })

    lines.append("")
    # summary line
    completed = round((1 - abs(total_now - target) / target) * 100, 0) if target > 0 else 0
    lines.append(f"> 📊 Build progress: **{completed:.0f}%** of target ({currency} {total_now:,.0f} / {currency} {target:,})")
    lines.append("")
    return "\n".join(lines), changes


def _build_attention_items(vectors: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """Generate 🔴🟡🟢 attention lists from signal vectors."""
    red = []
    yellow = []
    green = []

    for v in vectors:
        sym = v["symbol"]
        show_name = v.get("region", "") == "cn"
        if show_name:
            name = v.get("name", sym)
            label = f"**{name} ({sym})**"
        else:
            label = f"**{sym}**"

        # 🔴 conditions
        parts = []
        if v["danger_streak"] >= 2:
            parts.append(f"DANGER 第2周，LDev {v['ldev_last']:+.2f}→{v['ldev_now']:+.2f}σ")
        if v["new_danger"]:
            parts.append(f"新入 DANGER，LDev {v['ldev_now']:+.2f}σ")
        if v["ldev_delta"] < -0.5 and v["cls_now"] in ("DANGER", "WARNING", "BEARISH"):
            parts.append(f"LDev 单周降 {v['ldev_delta']:.2f}σ")
        if v["ma60_cross"] == "breakdown":
            parts.append(f"MA60 支撑跌破（{v['ma60_last']:+.1f}%→{v['ma60_now']:+.1f}%）")

        if parts:
            red.append(f"- {label} — {'，'.join(parts)}")
            continue

        # 🟡 conditions
        yparts = []
        if v["new_opportunity"]:
            yparts.append(f"新 OPPORTUNITY，LDev {v['ldev_now']:+.2f}σ，RSI {v['rsi_now']:.0f}")
        if v["new_bullish"]:
            yparts.append(f"新 BULLISH Trend，LDev {v['ldev_now']:+.2f}σ")
        if abs(v["ldev_delta"]) > 0.3:
            yparts.append(f"LDev Δ{v['ldev_delta']:+.2f}σ（{v['ldev_last']:+.2f}→{v['ldev_now']:+.2f}）")
        if v["warning_streak"] >= 2:
            yparts.append(f"{v['cls_now']} 持续第二周，Z 信号延续")
        if v["ma60_cross"] == "breakout":
            yparts.append(f"MA60 向上突破（{v['ma60_last']:+.1f}%→{v['ma60_now']:+.1f}%）")
        if abs(v["ma60_now"] - v["ma60_last"]) > 1.5:
            yparts.append(f"MA60 偏离显著变化（{v['ma60_last']:+.1f}%→{v['ma60_now']:+.1f}%）")

        if yparts:
            yellow.append(f"- {label} — {'，'.join(yparts)}")
            continue

        # 🟢 conditions
        gparts = []
        if v["new_deep_value"]:
            gparts.append(f"新入 Deep Value 区（LDev {v['ldev_now']:+.2f}σ）")
        if v["rsi_now"] > 90 and v["rsi_last"] <= 90:
            gparts.append(f"RSI 进入超买区（{v['rsi_now']:.0f}）")
        if v["rsi_now"] < 30:
            gparts.append(f"RSI 进入超卖区（{v['rsi_now']:.0f}）")
        if v["danger_streak"] >= 1 and v["cls_now"] == "DANGER":
            # Only show as green if it wasn't already flagged as red
            pass

        if gparts:
            green.append(f"- {label} — {'，'.join(gparts)}")

    return red, yellow, green


def _format_weekly_v2_markdown(cn_report: dict, us_report: dict, run_date: date) -> str:
    """Format weekly report with vector comparison — the core business weekly."""
    lines = []
    lines.append(f"# 📆 Weekly Market Review — Week ending {run_date}")
    lines.append("")

    # Determine this Friday and last Friday
    run_date_dt = run_date  # date object
    # use run_date as this_friday; look for last trading day ~7 days prior
    last_friday = run_date_dt - timedelta(days=7)

    # ── Section 1: Plan on track? ──
    lines.append("---")
    lines.append("")
    lines.append("## ✅ 1. 我的计划在正轨上吗？")
    lines.append("")

    # US HB progress
    us_last_date = _get_nearest_snapshot_date(last_friday, "us")
    us_this_date = _get_nearest_snapshot_date(run_date_dt, "us")
    us_plan_md, us_plan_data = _build_plan_progress_section(
        "us_hb", "US HB", "🇺🇸 US", us_this_date or run_date_dt, us_last_date or last_friday
    )
    lines.append(us_plan_md)

    # CN HB progress
    cn_last_date = _get_nearest_snapshot_date(last_friday, "cn")
    cn_this_date = _get_nearest_snapshot_date(run_date_dt, "cn")
    cn_plan_md, cn_plan_data = _build_plan_progress_section(
        "cn_hb", "CN HB", "🇨🇳 CN", cn_this_date or run_date_dt, cn_last_date or last_friday
    )
    lines.append(cn_plan_md)

    # ── Section 2: Signal vectors ──
    lines.append("---")
    lines.append("")
    lines.append("## 📊 2. 资产信号向量：变好还是变坏？")
    lines.append("")

    for region, region_label in [("cn", "🇨🇳 CN"), ("us", "🇺🇸 US")]:
        this_date = _get_nearest_snapshot_date(run_date_dt, region)
        if not this_date:
            continue
        this_snap = _load_snapshot(region, this_date)
        last_date = _get_nearest_snapshot_date(last_friday, region)
        last_snap = _load_snapshot(region, last_date) if last_date else None

        if not this_snap or not last_snap:
            lines.append(f"### {region_label}\n*Insufficient data for vector comparison*\n")
            continue

        vectors = _compute_vectors(this_snap, last_snap, region)
        if not vectors:
            continue

        lines.append(f"### {region_label} | {last_date} → {this_date}")
        lines.append("")
        lines.append("| Asset | Type | Last LDev | LDev Now | Δ LDev | MA60% | Signal Change |")
        lines.append("|-------|------|-----------|----------|--------|-------|---------------|")

        # Sort: biggest change first (absolute delta descending)
        vectors.sort(key=lambda v: abs(v["ldev_delta"]), reverse=True)

        for v in vectors:
            dir_emoji = {"↑": "🟢", "→": "⚪", "↓": "🔴"}.get(v["direction"], "⚪")
            sig_change = f"{v['cls_last']}→{v['cls_now']}"
            # Mark notable signal transitions
            if v["new_opportunity"]:
                sig_change = f"{v['cls_last']}→**{v['cls_now']}** 🟡"
            elif v["new_bullish"]:
                sig_change = f"{v['cls_last']}→**{v['cls_now']}** 🟢"
            elif v["new_danger"]:
                sig_change = f"{v['cls_last']}→**{v['cls_now']}** 🔴"

            show_name = region == "cn"
            asset_label = f"{v.get('name', v['symbol'])} ({v['symbol']})" if show_name else v['symbol']
            lines.append(
                f"| {asset_label} | {v['type'][:8]} | {v['ldev_last']:+.2f}σ | {v['ldev_now']:+.2f}σ "
                f"| {dir_emoji}{v['ldev_delta']:+.2f}σ | {v['ma60_now']:+.1f}% | {sig_change} |"
            )
        lines.append("")

    # ── Section 3: Attention items ──
    lines.append("---")
    lines.append("")
    lines.append("## ⚡ 3. 本周关注事项")
    lines.append("")

    all_vectors = []
    for region in ["cn", "us"]:
        this_date = _get_nearest_snapshot_date(run_date_dt, region)
        last_d = _get_nearest_snapshot_date(last_friday, region)
        if this_date and last_d:
            this_snap = _load_snapshot(region, this_date)
            last_snap = _load_snapshot(region, last_d)
            if this_snap and last_snap:
                all_vectors.extend(_compute_vectors(this_snap, last_snap, region))

    red, yellow, green = _build_attention_items(all_vectors)

    if red:
        lines.append("### 🔴 需要关注")
        for item in red:
            lines.append(item)
        lines.append("")

    if yellow:
        lines.append("### 🟡 值得关注")
        for item in yellow:
            lines.append(item)
        lines.append("")

    if green:
        lines.append("### 🟢 机会观察")
        for item in green:
            lines.append(item)
        lines.append("")

    if not red and not yellow and not green:
        lines.append("*本周无显著信号变化*\n")

    # ── Section 4: Summary ──
    lines.append("---")
    lines.append("")
    lines.append("## 📋 4. 概览汇总")
    lines.append("")

    # Regional signal distribution
    for region_label, region_key in [("🇨🇳 CN", "cn"), ("🇺🇸 US", "us")]:
        this_date = _get_nearest_snapshot_date(run_date_dt, region_key)
        last_d = _get_nearest_snapshot_date(last_friday, region_key)
        this_snap = _load_snapshot(region_key, this_date) if this_date else None
        last_snap = _load_snapshot(region_key, last_d) if last_d else None

        if not this_snap:
            continue

        def count_signals(snap):
            c = Counter()
            for a in snap:
                c[_signal_class(a.get("signal", ""))] += 1
            return c

        now_counts = count_signals(this_snap)
        last_counts = count_signals(last_snap) if last_snap else Counter()

        lines.append(f"**{region_label}** | Assets: {len(this_snap)}")
        for cls in ["BULLISH", "NEUTRAL", "OPPORTUNITY", "WARNING", "BEARISH", "DANGER"]:
            nc = now_counts.get(cls, 0)
            lc = last_counts.get(cls, 0)
            delta = nc - lc
            if delta > 0:
                delta_str = f" (+{delta})"
            elif delta < 0:
                delta_str = f" ({delta})"
            else:
                delta_str = ""
            if nc > 0 or lc > 0:
                lines.append(f"  - {cls}: {nc}{delta_str}")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} | Data: snapshots → positions → decisions*")
    lines.append("")

    return "\n".join(lines)


# ── PDF generation ───────────────────────────────────────────────────────────


def _create_pdf(markdown_content: str, market_map_path: Optional[Path], output_path: Path, report_type: str):
    """Create PDF from markdown content with embedded market map."""
    if not HAS_REPORTLAB:
        # Fallback: save markdown as text file
        output_path = output_path.with_suffix(".txt")
        output_path.write_text(markdown_content, encoding="utf-8")
        print(f"Warning: reportlab not available, saved as text: {output_path}")
        return output_path
    
    doc = SimpleDocTemplate(str(output_path), pagesize=landscape(A4),
                            leftMargin=0.5*inch, rightMargin=0.5*inch,
                            topMargin=0.4*inch, bottomMargin=0.4*inch)
    
    styles = getSampleStyleSheet()
    # Compact styles for single-page PDF
    title_style = ParagraphStyle("Title", parent=styles["Heading1"], fontSize=14, spaceAfter=6)
    heading_style = ParagraphStyle("Heading", parent=styles["Heading2"], fontSize=10, spaceAfter=4, spaceBefore=6)
    normal_style = ParagraphStyle("Normal", parent=styles["Normal"], fontSize=9, leading=11)
    table_style = ParagraphStyle("Table", fontSize=7, leading=9)  # Smaller for tables
    
    story = []
    
    # Title
    story.append(Paragraph(f"Market Report — {report_type.title()}", title_style))
    story.append(Spacer(1, 0.1*inch))
    
    # Parse simple markdown (headers, lists, tables)
    for line in markdown_content.split("\n"):
        line = line.strip()
        if not line:
            story.append(Spacer(1, 0.05*inch))
        elif line.startswith("# "):
            story.append(Paragraph(line[2:], title_style))
        elif line.startswith("## "):
            story.append(Paragraph(line[3:], heading_style))
        elif line.startswith("| ") and "|" in line[1:]:
            # Compact table rows
            story.append(Paragraph(f"<font size=7>{line}</font>", table_style))
        elif line.startswith("- "):
            story.append(Paragraph(f"• {line[2:]}", normal_style))
        else:
            story.append(Paragraph(line, normal_style))
    
    # Add market map if available - FULL PAGE landscape
    if market_map_path and market_map_path.exists():
        story.append(PageBreak())
        # Wider image for landscape
        img = Image(str(market_map_path), width=10*inch, height=5*inch)
        story.append(img)
    
    doc.build(story)
    return output_path


# ── Feishu push ─────────────────────────────────────────────────────────────


def _send_to_feishu(files: list[Path]) -> bool:
    """Send multiple files to Feishu group via OpenClaw."""
    try:
        import subprocess
        import shlex
        import os
        import shutil

        # Add nvm node to PATH
        env = os.environ.copy()
        nvm_node = "/root/.nvm/versions/node/v22.22.0/bin"
        env["PATH"] = f"{nvm_node}:{env.get('PATH', '')}"

        # Allow a dedicated 7S chat while keeping Jarvis parity as fallback.
        chat_id = os.environ.get("SEVENS_FEISHU_CHAT_ID")
        if not chat_id:
            raise ValueError("SEVENS_FEISHU_CHAT_ID environment variable not set")

        # Media directory (Feishu only allows files from media dir)
        media_dir = Path("/root/.openclaw/media")
        media_dir.mkdir(parents=True, exist_ok=True)

        # Send each file
        for file_path in files:
            if not file_path or not file_path.exists():
                print(f"Skipping non-existent file: {file_path}", file=sys.stderr)
                continue

            # Copy to media directory
            media_path = media_dir / file_path.name
            shutil.copy2(file_path, media_path)

            cmd = f'openclaw message send --channel feishu --target {chat_id} --media "{media_path}"'
            result = subprocess.run(shlex.split(cmd), capture_output=True, text=True, env=env)
            if result.returncode != 0:
                raise Exception(result.stderr)
            print(f"Sent to Feishu: {file_path.name}")

        return True
    except Exception as e:
        print(f"Failed to send to Feishu: {e}", file=sys.stderr)
        print(f"Files saved locally: {files}")
        return False


# ── main ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Push daily/weekly report to Feishu")
    parser.add_argument("--type", choices=["daily", "weekly"], default="daily",
                        help="Report type")
    parser.add_argument("--region", choices=["cn", "us", "all"], default="all",
                        help="Region to include")
    parser.add_argument("--date", default=str(date.today()),
                        help="Report date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate report without sending to Feishu")
    parser.add_argument("--skip-map", action="store_true",
                        help="Skip market map generation")
    args = parser.parse_args()
    
    run_date = date.fromisoformat(args.date)
    
    # Build region reports
    cn_report = _build_region_report("cn", run_date) if args.region in ("cn", "all") else {"error": "skipped"}
    us_report = _build_region_report("us", run_date) if args.region in ("us", "all") else {"error": "skipped"}
    
    # Check if we have any data
    if cn_report.get("error") and us_report.get("error"):
        print("ERROR: No data available", file=sys.stderr)
        sys.exit(1)
    
    # Format content
    if args.type == "daily":
        content = _format_daily_markdown(cn_report, us_report, run_date, region=args.region)
    else:
        # Weekly v2 with vector comparison (replaces old stub)
        content = _format_weekly_v2_markdown(cn_report, us_report, run_date)
    
    # Generate market map (unless skipped)
    market_map_path = None
    if not args.skip_map and HAS_MATPLOTLIB:
        cn_data = _load_snapshot("cn", run_date) if args.region in ("cn", "all") else []
        us_data = _load_snapshot("us", run_date) if args.region in ("us", "all") else []
        if cn_data or us_data:
            output_path = _REPORT_DIR / f"{run_date}_market_map_{args.region}.png"
            market_map_path = generate_market_map(cn_data or [], us_data or [], output_path, region=args.region)
            print(f"Market map generated: {market_map_path}")
    
    # Create PDF
    pdf_path = _REPORT_DIR / f"{run_date}_{args.type}_report_{args.region}.pdf"
    pdf_path = _create_pdf(content, market_map_path, pdf_path, args.type)
    print(f"Report saved: {pdf_path}")
    
    # Save markdown too
    md_path = pdf_path.with_suffix(".md")
    md_path.write_text(content, encoding="utf-8")
    print(f"Markdown saved: {md_path}")
    
    # Send to Feishu (unless dry-run): send markdown + PNG (not PDF)
    if args.dry_run:
        print("\n=== DRY RUN - Content Preview ===")
        print(content[:1000])
        print("..." if len(content) > 1000 else "")
    else:
        files_to_send = [md_path]
        if market_map_path and market_map_path.exists():
            files_to_send.append(market_map_path)
        _send_to_feishu(files_to_send)
    
    print(f"\n{args.type.title()} report push complete.")


if __name__ == "__main__":
    main()
