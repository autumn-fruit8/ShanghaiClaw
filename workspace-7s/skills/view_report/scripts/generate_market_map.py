#!/usr/bin/env python3
"""
generate_market_map.py — Global Market Regime Map (CN + US combined)

Reads today's snapshots for both regions and produces a single scatter-plot PNG
that the report can attach to a Feishu message or daily briefing.

Axes:
  X  — Trend Proximity: (Price - MA60) / MA60 * 100  (from snapshot.MA60_pct)
  Y  — Statistical Stretch: LDev  (from snapshot.LDev)

Color by asset type:
  STEADY   → green   (trend-following)
  MOMENTUM → orange  (momentum)
  VOLATILE → blue    (mean-reversion)

Usage:
    python3 skills/view_report/scripts/generate_market_map.py
    python3 skills/view_report/scripts/generate_market_map.py --date 2026-03-08
    python3 skills/view_report/scripts/generate_market_map.py --date 2026-03-08 --output /tmp/map.png
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless — no display needed
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

_SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = _SCRIPT_DIR.parents[2]  # scripts -> view_report -> skills -> workspace-7s
sys.path.insert(0, str(WORKSPACE_ROOT))

from config import SNAPSHOTS_DIR, REPORTS_DIR

# ── color scheme ─────────────────────────────────────────────────────────────

_TYPE_COLORS = {
    "STEADY":   "#2ca02c",   # green
    "MOMENTUM": "#ff7f0e",   # orange
    "VOLATILE": "#1f77b4",   # blue
}
_TYPE_DEFAULT = "#888888"


def _configure_font_fallbacks() -> None:
    """Prefer an installed CJK-capable sans-serif font for label rendering."""
    preferred = [
        "PingFang SC",
        "Heiti SC",
        "STHeiti",
        "Songti SC",
        "Arial Unicode MS",
        "Noto Sans CJK SC",
        "SimHei",
        "Microsoft YaHei",
    ]
    available = {font.name for font in fm.fontManager.ttflist}
    selected = [name for name in preferred if name in available]
    matplotlib.rcParams["font.sans-serif"] = selected + ["DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False


_configure_font_fallbacks()


def _load_snapshot(region: str, run_date: date) -> list[dict]:
    path = SNAPSHOTS_DIR / f"{run_date}_{region}.json"
    if not path.exists():
        # try 1-3 days back (weekends/holidays)
        for offset in (1, 2, 3):
            p = SNAPSHOTS_DIR / f"{run_date - timedelta(days=offset)}_{region}.json"
            if p.exists():
                path = p
                break
        else:
            print(f"  [WARN] No snapshot found for {region} around {run_date}")
            return []
    with path.open() as f:
        return json.load(f)


def generate(run_date: date, out_path: Path, assets: list[dict] = None) -> None:
    # Load both regions if assets not provided
    if assets is None:
        assets = _load_snapshot("cn", run_date) + _load_snapshot("us", run_date)
    if not assets:
        print("⚠️  No snapshot data available. Run run_strategy.py first.")
        return  # Don't exit, let caller handle

    # Filter out assets with missing map coordinates
    # Use ldev (lowercase) from snapshot, fall back to z if LDev unavailable
    # Use z as fallback for MA60_pct if it's missing
    plotable = []
    for a in assets:
        ldev = a.get("LDev") or a.get("ldev") or a.get("z")
        ma60 = a.get("MA60_pct") or a.get("ma60_pct") or a.get("z")
        if ldev is not None and ma60 is not None:
            a_copy = dict(a)
            a_copy["LDev"] = float(ldev)
            a_copy["MA60_pct"] = float(ma60)
            plotable.append(a_copy)
    skipped = len(assets) - len(plotable)
    if skipped:
        print(f"  [WARN] Skipped {skipped} assets with missing MA60_pct or LDev (tried ldev/z for both)")

    # Labels: show the configured asset name for all assets
    xs      = [a["MA60_pct"] for a in plotable]
    ys      = [a["LDev"]     for a in plotable]
    colors  = [_TYPE_COLORS.get(a.get("Type") or a.get("type", ""), _TYPE_DEFAULT) for a in plotable]
    labels  = []
    for a in plotable:
        region = a.get("region", "").lower()
        # Always show English name (from assets.py name field)
        labels.append(a.get("name", a.get("symbol", "?")))
    symbols = [a.get("symbol", "") for a in plotable]

    # ── layout ────────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(18, 10))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # Dynamic axis limits with 15% padding
    if xs and ys:
        x_span = max(xs) - min(xs) or 2.0
        y_span = max(ys) - min(ys) or 1.0
        pad = 0.18
        xlim = (min(xs) - x_span * pad, max(xs) + x_span * pad)
        ylim = (min(ys) - y_span * pad, max(ys) + y_span * pad)
    else:
        xlim, ylim = (-10, 10), (-3, 3)

    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)

    # ── background zones ──────────────────────────────────────────────────────
    zone_alpha = 0.08
    # Melt-up zone (top, y > 2)
    ax.add_patch(plt.Rectangle((xlim[0], 2.0), xlim[1] - xlim[0], ylim[1] - 2.0,
                                color="#cc0000", alpha=zone_alpha, zorder=0))
    # Healthy trend zone (right, x > 0, -1 < y < 2)
    ax.add_patch(plt.Rectangle((0, -1.0), xlim[1], 3.0,
                                color="#00cc44", alpha=zone_alpha / 2, zorder=0))
    # Deep value zone (bottom, y < -2)
    ax.add_patch(plt.Rectangle((xlim[0], ylim[0]), xlim[1] - xlim[0], -2.0 - ylim[0],
                                color="#ddaa00", alpha=zone_alpha, zorder=0))

    # Grid + axes
    ax.axhline(0, color="#666", linewidth=0.8, zorder=1)
    ax.axvline(0, color="#666", linewidth=0.8, zorder=1)
    ax.grid(True, linestyle="--", alpha=0.3, color="#888", zorder=1)

    # ── scatter ───────────────────────────────────────────────────────────────
    ax.scatter(xs, ys, s=200, c=colors, edgecolors="black", linewidths=0.8,
               alpha=0.88, zorder=10)

    # Labels: show name (English) for all assets
    for i, lbl in enumerate(labels):
        ax.annotate(lbl, (xs[i], ys[i]),
                    xytext=(0, 12), textcoords="offset points",
                    fontsize=7, color="black",
                    ha="center", va="bottom", zorder=11)

    # ── zone labels ───────────────────────────────────────────────────────────
    def _zone_text(x, y, txt, color):
        ax.text(x, y, txt, fontsize=10, color=color, alpha=0.45,
                ha="right" if x > 0 else "left", va="top" if y > 0 else "bottom",
                fontweight="bold", zorder=2)

    if ylim[1] > 2.0:
        _zone_text(xlim[1] * 0.98, min(ylim[1] * 0.97, ylim[1] - 0.1),
                   "MELT-UP ZONE", "#ff6666")
    if xlim[1] > 0:
        ax.text(xlim[1] * 0.98, 0.1, "HEALTHY TREND", fontsize=9,
                color="#66cc88", alpha=0.4, ha="right", fontweight="bold", zorder=2)
    if ylim[0] < -2.0:
        _zone_text(xlim[0] * 0.98, max(ylim[0] * 0.97, ylim[0] + 0.1),
                   "DEEP VALUE", "#ddaa33")

    # ── legend ────────────────────────────────────────────────────────────────
    type_legend = [
        Line2D([0], [0], marker="o", color="w", label=t,
               markerfacecolor=c, markersize=11, markeredgewidth=0)
        for t, c in _TYPE_COLORS.items()
    ]
    leg = ax.legend(handles=type_legend,
                    loc="upper left", fontsize=9,
                    framealpha=0.75, facecolor="white",
                    labelcolor="black", edgecolor="#666")

    # ── titles & labels ───────────────────────────────────────────────────────
    ax.set_title(f"GLOBAL MARKET REGIME MAP — {run_date}  ({len(plotable)} assets: CN + US)",
                 fontsize=16, fontweight="bold", color="black", pad=18)
    ax.set_xlabel("Trend Proximity: Distance from MA60  (%)",
                  fontsize=12, color="#333333", labelpad=10)
    ax.set_ylabel("Statistical Stretch: LDev  (σ)",
                  fontsize=12, color="#333333", labelpad=10)
    ax.tick_params(colors="black")
    for spine in ax.spines.values():
        spine.set_edgecolor("#666")

    # Asset count annotation
    cn_count = sum(1 for a in plotable if a.get("region") == "cn")
    us_count = sum(1 for a in plotable if a.get("region") == "us")
    ax.text(0.01, 0.01, f"CN: {cn_count}  US: {us_count}",
            transform=ax.transAxes, fontsize=9, color="#333333", va="bottom")

    # ── save ──────────────────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close()
    print(f"Market map saved → {out_path}")


def generate_from_data(cn_data: list[dict], us_data: list[dict], output_path: Path) -> Path:
    """
    Generate market map from raw data (used by report_pusher.py).
    
    Args:
        cn_data: List of CN asset dicts from snapshot
        us_data: List of US asset dicts from snapshot
        output_path: Where to save PNG
        
    Returns:
        Path to generated PNG, or None if no data
    """
    if not cn_data and not us_data:
        print("Warning: no asset data for market map", file=sys.stderr)
        return None
    from datetime import date as dt
    generate(dt.today(), output_path, cn_data + us_data)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate global market regime map PNG")
    parser.add_argument("--date", default=str(date.today()),
                        help="Reference date YYYY-MM-DD (default: today)")
    parser.add_argument("--region", default="all",
                        help="Region: cn, us, or all (default: all)")
    parser.add_argument("--output", default=None,
                        help="Output PNG path (default: logs/reports/{date}_market_map_{region}.png)")
    args = parser.parse_args()

    run_date = date.fromisoformat(args.date)
    region = args.region.lower()
    out_path = (Path(args.output) if args.output
                else REPORTS_DIR / f"{run_date}_market_map_{region}.png")

    # Load data based on region
    if region == "all":
        assets = _load_snapshot("cn", run_date) + _load_snapshot("us", run_date)
    elif region == "cn":
        assets = _load_snapshot("cn", run_date)
    elif region == "us":
        assets = _load_snapshot("us", run_date)
    else:
        assets = []
    
    generate(run_date, out_path, assets)


if __name__ == "__main__":
    main()
