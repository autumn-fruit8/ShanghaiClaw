"""
run_backtest.py — Standalone 10-year backtest simulation.

Run on-demand (adhoc) or as part of monthly evolve review.
Saves to logs/backtest/monthly/ with previous-month delta comparison.

Usage:
    # Single symbol — generates combined PNG to adhoc/backtest/
    python3 skills/backtest/scripts/run_backtest.py --symbol 159207

    # Full backtest for a region
    python3 skills/backtest/scripts/run_backtest.py --region cn

    # Specific date marker
    python3 skills/backtest/scripts/run_backtest.py --region us --date 2026-05-18

    # All regions
    python3 skills/backtest/scripts/run_backtest.py --region all

    # Backtest + generate universe report
    python3 skills/backtest/scripts/run_backtest.py --region cn --report

    # Report only (from existing monthly JSON, no re-run)
    python3 skills/backtest/scripts/run_backtest.py --region cn --report-only
"""

import argparse
import json
import math
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

_THIS = Path(__file__).resolve()
_WORKSPACE_ROOT = _THIS.parents[3]
sys.path.insert(0, str(_WORKSPACE_ROOT))

import yaml

import pandas as pd

from config import BACKTEST_DIR
from dao.asset_dao import AssetManifest
from dao.config_dao import ConfigLoader
from skills.analyze.scripts.strategy import StrategyEngine
from skills.analyze.scripts.s4_strategy.pipeline import run_strategy_pipeline
from utils.data_service.data_resolver import resolve_price_data
from utils.symbols.state_resolver import resolve_symbols_from_args


ADHOC_DIR = _WORKSPACE_ROOT / "adhoc" / "backtest"


def _load_previous(filepath: Path) -> list[dict] | None:
    if not filepath.exists():
        return None
    try:
        with open(filepath) as f:
            return json.load(f)
    except Exception:
        return None


def _compute_delta(current: dict, previous: dict | None) -> dict:
    if previous is None:
        return {"has_previous": False}

    c = current
    p = previous
    fields = ["strategy_ret", "buyhold_ret", "strat_sharpe", "buyhold_sharpe",
              "strategy_dd", "buyhold_dd"]

    deltas = {}
    for f in fields:
        cv = c.get(f, 0)
        pv = p.get(f, 0)
        if isinstance(cv, (int, float)) and isinstance(pv, (int, float)):
            deltas[f + "_delta"] = round(cv - pv, 6)
            if pv != 0 and f in ("strategy_ret", "buyhold_ret", "strat_sharpe", "buyhold_sharpe"):
                deltas[f + "_pct"] = round((cv - pv) / abs(pv), 4)

    c_alpha = c.get("strategy_ret", 0) - c.get("buyhold_ret", 0)
    p_alpha = p.get("strategy_ret", 0) - p.get("buyhold_ret", 0)
    deltas["alpha_delta"] = round(c_alpha - p_alpha, 6)
    deltas["trend_warning"] = _check_trend(deltas)

    return {
        "has_previous": True,
        "previous_date": p.get("backtest_date", "?"),
        "deltas": deltas,
    }


def _check_trend(deltas: dict) -> str | None:
    warnings = []
    if deltas.get("alpha_delta", 0) < -0.02:
        warnings.append("alpha shrinking")
    if deltas.get("strat_sharpe_delta", 0) < -0.05:
        warnings.append("sharpe declining")
    return "; ".join(warnings) if warnings else None


def _print_results(assets: list[dict], with_previous: bool) -> None:
    print()
    print("| Symbol | Name | S.Ret | S.DD | S.Sharpe | B.Ret | B.DD | B.Sharpe |", end="")
    if with_previous:
        print(" α-Δ | Warning |", end="")
    print()

    col_count = 8 + (2 if with_previous else 0)
    print("|" + "|".join(["--------"] * col_count) + "|")

    for a in assets:
        bt = a.get("backtest", {})
        dp = a.get("delta", {})
        symbol = bt.get("symbol", "?")
        name   = bt.get("name", "")[:12]
        sr     = f"{bt.get('strategy_ret', 0):.1%}" if isinstance(bt.get('strategy_ret'), float) else "N/A"
        sd     = f"{bt.get('strategy_dd', 0):.1%}" if isinstance(bt.get('strategy_dd'), float) else "N/A"
        ss     = f"{bt.get('strat_sharpe', 0):.2f}" if isinstance(bt.get('strat_sharpe'), float) else "N/A"
        br     = f"{bt.get('buyhold_ret', 0):.1%}" if isinstance(bt.get('buyhold_ret'), float) else "N/A"
        bd     = f"{bt.get('buyhold_dd', 0):.1%}" if isinstance(bt.get('buyhold_dd'), float) else "N/A"
        bs     = f"{bt.get('buyhold_sharpe', 0):.2f}" if isinstance(bt.get('buyhold_sharpe'), float) else "N/A"

        row = f"| {symbol:<8} | {name:<12} | {sr:>7} | {sd:>6} | {ss:>8} | {br:>7} | {bd:>6} | {bs:>8} |"

        if with_previous:
            deltas = dp.get("deltas", {})
            ad = f"{deltas.get('alpha_delta', 0):+.2%}" if deltas.get('alpha_delta') is not None else "N/A"
            warn = (dp.get("trend_warning") or "\u2014")[:16]
            row += f" {ad:>7} | {warn:<16} |"

        print(row)


def _pct(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "N/A"
    return f"{v * 100:+.1f}%"

def _fmt(v, decimals=2) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "N/A"
    return f"{v:.{decimals}f}"


# ---------------------------------------------------------------------------
# Chart generation (PNG) — matching PDF reference format from main3.py
# ---------------------------------------------------------------------------

INITIAL_CASH = 100_000.0


def _reconstruct_account(dates_pd_series, trades, initial_capital):
    df = pd.DataFrame({"date": dates_pd_series})
    df.set_index("date", inplace=True)
    df["cash"] = float(initial_capital)
    df["shares"] = 0.0

    if trades:
        tdf = pd.DataFrame(trades)
        tdf["date"] = pd.to_datetime(tdf["date"])
        tdf = tdf.sort_values("date")
        changes = pd.DataFrame(index=df.index, columns=["d_cash", "d_shares"]).fillna(0.0)
        for _, t in tdf.iterrows():
            t_date = t["date"]
            if t_date in changes.index:
                changes.loc[t_date, "d_cash"] += float(t.get("amt_delta", 0))
                changes.loc[t_date, "d_shares"] += float(t.get("shares_delta", 0))
        df["cash"] = initial_capital + changes["d_cash"].cumsum()
        df["shares"] = 0.0 + changes["d_shares"].cumsum()
        df["shares"] = df["shares"].clip(lower=0)

    min_cash = df["cash"].min()
    if min_cash < 0:
        df["cash"] += (abs(min_cash) + 1000)

    return df["cash"].astype(float), df["shares"].astype(float)


def _build_asset_entry(result: dict, sym: str, name: str, run_date: str,
                       profile_sma_windows: list[int] | None = None) -> dict:
    """Build a single asset entry dict from engine.analyze() result."""
    m = result["meta"]
    strategy_name = result.get("strategy_name", "")
    tactic_name = result.get("tactic_name", "")
    entry = {
        "symbol": sym,
        "name": name,
        "strategy_name": strategy_name,
        "tactic_name": tactic_name,
        "backtest": {
            "symbol": sym, "name": name, "strategy_name": strategy_name,
            "type": m["Type"], "backtest_date": run_date,
            "strategy_ret": m["Strategy_Ret"], "strategy_dd": m["Strategy_DD"],
            "strat_vol": m["Strat_Vol"], "strat_sharpe": m["Strat_Sharpe"],
            "buyhold_ret": m["BuyHold_Ret"], "buyhold_dd": m["BuyHold_DD"],
            "buyhold_vol": m["BuyHold_Vol"], "buyhold_sharpe": m["BuyHold_Sharpe"],
            "trades_count": len(result.get("trades", [])),
            "period_start": m.get("Backtest_Period_Start", "?"),
            "period_end": m.get("Backtest_Period_End", "?"),
            "period_years": m.get("Backtest_Years", 0),
        },
        "signal": m["Signal"],
    }
    data = result.get("data")
    if data is not None and "strategy_equity" in data.columns:
        vals = data["val"].values
        entry["series"] = {
            "dates": [str(d)[:10] for d in data["date"].values],
            "log_price": [float(np.log(v)) if v > 0 else 0.0 for v in vals],
            "strategy_equity": [float(v) for v in data["strategy_equity"].values],
            "buyhold_value": [float(v) for v in vals],
            "cash_array": [float(v) for v in data["_cash_array"].values] if "_cash_array" in data.columns else [],
            "shares_array": [float(v) for v in data["_shares_array"].values] if "_shares_array" in data.columns else [],
            "roll_trend": [float(v) for v in data["roll_trend"].values] if "roll_trend" in data.columns else [],
            "roll_sigma": [float(v) for v in data["roll_sigma"].values] if "roll_sigma" in data.columns else [],
            "ma250": [float(v) for v in data["sma_250"].values] if "sma_250" in data.columns
                     else ([float(v) for v in data["ma_base"].values] if "ma_base" in data.columns else []),
        }
        # Dynamically include sma_* columns, filtered to profile's explicit windows
        mas = {}
        sma_windows = profile_sma_windows if profile_sma_windows else []
        for col in data.columns:
            if col.startswith("sma_"):
                w_str = col.replace("sma_", "")
                try:
                    w = int(w_str)
                    # Only include if in profile's sma list
                    if not sma_windows or w in sma_windows:
                        mas[col] = [float(v) for v in data[col].values]
                except ValueError:
                    pass
        if mas:
            entry["series"]["mas"] = mas
    if result.get("trades"):
        entry["sim_actions"] = [
            {"date": str(t.get("date", ""))[:10], "action": t.get("type", ""),
             "desc": t.get("desc", ""), "price": t.get("val", 0),
             "shares_delta": t.get("shares_delta", 0), "amt_delta": t.get("amt_delta", 0)}
            for t in result["trades"]
        ]
    return entry


# ---------------------------------------------------------------------------
# Single-symbol: combined PNG (both charts stacked vertically)
# ---------------------------------------------------------------------------

def _generate_combined_chart(asset: dict, output_path: Path) -> Path:
    """Generate a single combined PNG with both charts stacked vertically."""
    series = asset.get("series", {})
    dates_raw = series.get("dates", [])
    if not dates_raw:
        raise ValueError("No series data")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    dates_pd = pd.to_datetime(dates_raw)
    buyhold_value = series.get("buyhold_value", [])
    sim_actions = asset.get("sim_actions", [])

    sym = asset.get("symbol", "?")
    name = asset.get("name", "?")
    bt = asset.get("backtest", {})
    strat_type = bt.get("type", "STEADY")

    if strat_type == "STEADY":
        low_ldev, high_ldev = -1.5, 3.0
    elif strat_type == "VOLATILE":
        low_ldev, high_ldev = -2.0, 1.5
    elif strat_type == "MOMENTUM":
        low_ldev, high_ldev = -1.0, 3.0
    else:
        low_ldev, high_ldev = -1.5, 3.0

    meta = {
        "Strategy_Ret": bt.get("strategy_ret", 0),
        "Strategy_DD": bt.get("strategy_dd", 0),
        "Strat_Vol": bt.get("strat_vol", 0),
        "Strat_Sharpe": bt.get("strat_sharpe", 0),
        "BuyHold_Ret": bt.get("buyhold_ret", 0),
        "BuyHold_DD": bt.get("buyhold_dd", 0),
        "BuyHold_Vol": bt.get("buyhold_vol", 0),
        "BuyHold_Sharpe": bt.get("buyhold_sharpe", 0),
    }

    bt_start = pd.to_datetime(bt.get("period_start", dates_raw[0]))
    bt_end = pd.to_datetime(bt.get("period_end", dates_raw[-1]))
    bt_mask = (dates_pd >= bt_start) & (dates_pd <= bt_end)
    bt_dates_pd = dates_pd[bt_mask]
    bt_dates = bt_dates_pd.to_pydatetime().tolist()
    bt_bh = [buyhold_value[i] for i, m in enumerate(bt_mask) if m]

    bt_trades = [t for t in sim_actions if bt_start <= pd.to_datetime(t["date"]) <= bt_end]
    raw_cash, raw_shares = _reconstruct_account(bt_dates_pd, bt_trades, INITIAL_CASH)

    # Categorize trades
    trend_buy = [t for t in bt_trades if t["action"] == "BUY" and
                 any(k in t.get("desc", "") for k in ["Trend", "趋势", "Accum", "加仓", "积累"])]
    dip_buy = [t for t in bt_trades if t["action"] == "BUY" and
               any(k in t.get("desc", "") for k in ["Dip", "Seeding", "回调", "买入", "超卖",
                                                      "低估半仓", "低估", "Oversold", "半仓"])]
    deep_buy = [t for t in bt_trades if t["action"] == "BUY" and
                not any(k in t.get("desc", "") for k in ["Trend", "趋势", "加仓"]) and
                not any(k in t.get("desc", "") for k in ["Dip", "回调", "Oversold", "超卖", "半仓"]) and
                any(k in t.get("desc", "") for k in ["Deep", "深度", "Value", "Bottom", "全仓",
                                                      "极端", "低估全仓"])]
    all_sells = [t for t in bt_trades if t["action"] == "SELL"]
    hy_sells = [t for t in all_sells if any(k in t.get("desc", "") for k in
                ["Bubble", "Extreme", "Clear", "泡沫", "过热", "清空", "退出", "减半"])]
    lt_sells = [t for t in all_sells if t not in hy_sells]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 9), gridspec_kw={"height_ratios": [7, 3], "hspace": 0.3})

    # --- Chart 1: Simulated Actions (semi-log scale with raw total_return values) ---
    ax1.plot(bt_dates, bt_bh, "k", lw=1, label="Total Return")
    ax1.set_yscale('log')

    roll_trend = series.get("roll_trend", [])
    roll_sigma = series.get("roll_sigma", [])
    if roll_trend and roll_sigma and len(roll_trend) == len(dates_raw):
        bt_trend = [roll_trend[i] for i, m in enumerate(bt_mask) if m]
        bt_sigma = [roll_sigma[i] for i, m in enumerate(bt_mask) if m]
        bt_sigma_arr = np.array(bt_sigma)
        bt_trend_arr = np.array(bt_trend)
        valid = bt_sigma_arr > 0
        if valid.any():
            vd = [bt_dates[i] for i, v in enumerate(valid) if v]
            # Convert trend/sigma from log-space to raw-space for log-scaled axes
            vt_raw = np.exp(bt_trend_arr[valid])
            ax1.plot(vd, vt_raw, "b--", lw=1, alpha=0.5, label="Trend")
            ax1.fill_between(vd, np.exp(bt_trend_arr[valid] + low_ldev * bt_sigma_arr[valid]),
                             np.exp(bt_trend_arr[valid] - 3.5 * bt_sigma_arr[valid]), color="green", alpha=0.1)
            ax1.fill_between(vd, np.exp(bt_trend_arr[valid] + high_ldev * bt_sigma_arr[valid]),
                             np.exp(bt_trend_arr[valid] + 3.5 * bt_sigma_arr[valid]), color="red", alpha=0.1)

# --- Dynamic MA lines (from profile) ---
    mas = series.get("mas", {})
    if mas and len(dates_raw) == len(next(iter(mas.values()), [])):
        windows = sorted([int(k.replace("sma_", "")) for k in mas])
        ma_colors = {20: "#1f77b4", 50: "#9467bd", 60: "#9467bd", 100: "#d62728",
                     200: "#ff7f0e", 250: "#ff7f0e"}
        default_palette = ["#7f7f7f", "#bcbd22", "#17becf", "#8c564b", "#e377c2",
                           "#2ca02c", "#d62728", "#9467bd", "#1f77b4", "#ff7f0e"]
        for i, w in enumerate(windows):
            col = f"sma_{w}"
            vals = mas.get(col, [])
            if vals and len(vals) == len(dates_raw):
                bt_ma = [vals[i] for i, m in enumerate(bt_mask) if m]
                color = ma_colors.get(w, default_palette[i % len(default_palette)])
                ax1.plot(bt_dates, bt_ma, color=color, lw=1, alpha=0.6, label=f"MA{w}")


    base_price = bt_bh[0]
    norm_bh = np.asarray([(v / base_price) * 100 for v in bt_bh], dtype=float)
    norm_cash = np.asarray(raw_cash.values, dtype=float) / INITIAL_CASH * 100
    norm_pos = np.asarray(raw_shares.values, dtype=float) * np.asarray(bt_bh, dtype=float) / INITIAL_CASH * 100

    if trend_buy:
        ax1.scatter([t["date"] for t in trend_buy], [t["price"] for t in trend_buy],
                     c="cyan", marker="^", s=12, zorder=5, label="Trend Buy")
    if dip_buy:
        ax1.scatter([t["date"] for t in dip_buy], [t["price"] for t in dip_buy],
                     c="green", marker="^", s=12, zorder=5, label="Dip/Seed")
    if deep_buy:
        ax1.scatter([t["date"] for t in deep_buy], [t["price"] for t in deep_buy],
                     c="gold", marker="*", s=12, zorder=5, label="Deep/Btm")
    if lt_sells:
        ax1.scatter([t["date"] for t in lt_sells], [t["price"] for t in lt_sells],
                     c="red", marker="v", s=12, zorder=5, label="Lt Sell")
    if hy_sells:
        ax1.scatter([t["date"] for t in hy_sells], [t["price"] for t in hy_sells],
                     c="#8B0000", marker="v", s=12, zorder=6, label="Hy Sell")

    duration = bt.get("period_years", 0)
    strategy_label = asset.get("strategy_name", "")
    tactic_label = asset.get("tactic_name", "")
    title_str = f"{sym} {name} Backtest of {duration:.1f}yr [{strategy_label}]"
    if tactic_label and tactic_label != strategy_label:
        title_str += f" ({tactic_label})"
    ax1.set_title(title_str, fontsize=11, fontweight="bold", loc="left")
    ax1.legend(loc="upper left", fontsize=8, ncol=4)
    ax1.grid(True, alpha=0.3)

    # --- Chart 2: Performance ---
    ax2.stackplot(bt_dates, norm_pos, norm_cash,
                  labels=["Strategy Position", "Strategy Cash"],
                  colors=["#87CEFA", "#E0E0E0"], alpha=0.3, zorder=0)
    ax2.plot(bt_dates, norm_bh, "k-", lw=1.5, label="B&H (Index)")
    ax2.axhline(100, color="k", linestyle=":", alpha=0.5)

    data_period = f"Data: {bt_dates[0].strftime('%Y-%m-%d')} → {bt_dates[-1].strftime('%Y-%m-%d')}"
    strat_metrics = (
        f"Strategy:  CAGR {(1+meta['Strategy_Ret'])**(1/max(duration,1))-1:+.1%}"
        f" | Ret {meta['Strategy_Ret']:+.1%} | DD {meta['Strategy_DD']:.1%}"
        f" | Vol {meta['Strat_Vol']:.1%} | Sharpe {meta['Strat_Sharpe']:.2f}"
    )
    bh_metrics = (
        f"B&H:       CAGR {(1+meta['BuyHold_Ret'])**(1/max(duration,1))-1:+.1%}"
        f" | Ret {meta['BuyHold_Ret']:+.1%} | DD {meta['BuyHold_DD']:.1%}"
        f" | Vol {meta['BuyHold_Vol']:.1%} | Sharpe {meta['BuyHold_Sharpe']:.2f}"
    )
    subtitle_text = f"{strat_metrics}\n{bh_metrics}\n{data_period}"

    ax2.text(0.0, 1.02, subtitle_text, transform=ax2.transAxes,
             fontsize=9, color="#333333", va="bottom", ha="left", family="monospace")
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(True, alpha=0.3)
    ax2.set_ylabel("Normalized Value")

    fig.subplots_adjust(top=0.95, hspace=0.3)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


# ---------------------------------------------------------------------------
# Region-level: per-asset separate PNGs + Markdown report
# ---------------------------------------------------------------------------

def _generate_charts(asset: dict, charts_dir: Path) -> dict[str, Path]:
    series = asset.get("series", {})
    dates_raw = series.get("dates", [])
    if not dates_raw:
        return {}

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    dates_pd = pd.to_datetime(dates_raw)
    dates = dates_pd.to_pydatetime().tolist()
    buyhold_value = series.get("buyhold_value", [])
    sim_actions = asset.get("sim_actions", [])
    trades = asset.get("sim_actions", [])

    sym = asset.get("symbol", "?")
    name = asset.get("name", "?")
    bt = asset.get("backtest", {})
    strat_type = bt.get("type", "STEADY")

    if strat_type == "STEADY":
        low_ldev, high_ldev = -1.5, 3.0
    elif strat_type == "VOLATILE":
        low_ldev, high_ldev = -2.0, 1.5
    elif strat_type == "MOMENTUM":
        low_ldev, high_ldev = -1.0, 3.0
    else:
        low_ldev, high_ldev = -1.5, 3.0

    meta = {
        "Strategy_Ret": bt.get("strategy_ret", 0),
        "Strategy_DD": bt.get("strategy_dd", 0),
        "Strat_Vol": bt.get("strat_vol", 0),
        "Strat_Sharpe": bt.get("strat_sharpe", 0),
        "BuyHold_Ret": bt.get("buyhold_ret", 0),
        "BuyHold_DD": bt.get("buyhold_dd", 0),
        "BuyHold_Vol": bt.get("buyhold_vol", 0),
        "BuyHold_Sharpe": bt.get("buyhold_sharpe", 0),
    }

    df_dates = pd.to_datetime(dates_raw)
    bt_start = pd.to_datetime(bt.get("period_start", dates_raw[0]))
    bt_end = pd.to_datetime(bt.get("period_end", dates_raw[-1]))
    bt_mask = (df_dates >= bt_start) & (df_dates <= bt_end)
    bt_dates_pd = dates_pd[bt_mask]
    bt_dates = bt_dates_pd.to_pydatetime().tolist()
    bt_bh = [buyhold_value[i] for i, m in enumerate(bt_mask) if m]
    bt_trades = [t for t in trades if bt_start <= pd.to_datetime(t["date"]) <= bt_end]

    raw_cash, raw_shares = _reconstruct_account(bt_dates_pd, bt_trades, INITIAL_CASH)

    trend_buy = [t for t in bt_trades if t["action"] == "BUY" and
                 any(k in t.get("desc", "") for k in ["Trend", "趋势", "Accum", "加仓", "积累"])]
    dip_buy = [t for t in bt_trades if t["action"] == "BUY" and
               any(k in t.get("desc", "") for k in ["Dip", "Seeding", "回调", "买入", "超卖",
                                                      "低估半仓", "低估", "Oversold", "半仓"])]
    deep_buy = [t for t in bt_trades if t["action"] == "BUY" and
                not any(k in t.get("desc", "") for k in ["Trend", "趋势", "加仓"]) and
                not any(k in t.get("desc", "") for k in ["Dip", "回调", "Oversold", "超卖", "半仓"]) and
                any(k in t.get("desc", "") for k in ["Deep", "深度", "Value", "Bottom", "全仓",
                                                      "极端", "低估全仓"])]
    all_sells = [t for t in bt_trades if t["action"] == "SELL"]
    hy_sells = [t for t in all_sells if any(k in t.get("desc", "") for k in
                ["Bubble", "Extreme", "Clear", "泡沫", "过热", "清空", "退出", "减半"])]
    lt_sells = [t for t in all_sells if t not in hy_sells]

    # Chart 1: Simulated Actions (semi-log scale with raw total_return values)
    fig, ax1 = plt.subplots(figsize=(10, 4.5))
    ax1.plot(bt_dates, bt_bh, "k", lw=1, label="Total Return")
    ax1.set_yscale('log')

    roll_trend = series.get("roll_trend", [])
    roll_sigma = series.get("roll_sigma", [])
    if roll_trend and roll_sigma and len(roll_trend) == len(dates_raw):
        bt_trend = [roll_trend[i] for i, m in enumerate(bt_mask) if m]
        bt_sigma = [roll_sigma[i] for i, m in enumerate(bt_mask) if m]
        bt_sigma_arr = np.array(bt_sigma)
        bt_trend_arr = np.array(bt_trend)
        valid = bt_sigma_arr > 0
        if valid.any():
            vd = [bt_dates[i] for i, v in enumerate(valid) if v]
            vt_raw = np.exp(bt_trend_arr[valid])
            ax1.plot(vd, vt_raw, "b--", lw=1, alpha=0.5, label="Trend")
            ax1.fill_between(vd, np.exp(bt_trend_arr[valid] + low_ldev * bt_sigma_arr[valid]),
                             np.exp(bt_trend_arr[valid] - 3.5 * bt_sigma_arr[valid]), color="green", alpha=0.1)
            ax1.fill_between(vd, np.exp(bt_trend_arr[valid] + high_ldev * bt_sigma_arr[valid]),
                             np.exp(bt_trend_arr[valid] + 3.5 * bt_sigma_arr[valid]), color="red", alpha=0.1)

# --- Dynamic MA lines (from profile) ---
    mas = series.get("mas", {})
    if mas and len(dates_raw) == len(next(iter(mas.values()), [])):
        windows = sorted([int(k.replace("sma_", "")) for k in mas])
        ma_colors = {20: "#1f77b4", 50: "#9467bd", 60: "#9467bd", 100: "#d62728",
                     200: "#ff7f0e", 250: "#ff7f0e"}
        default_palette = ["#7f7f7f", "#bcbd22", "#17becf", "#8c564b", "#e377c2",
                           "#2ca02c", "#d62728", "#9467bd", "#1f77b4", "#ff7f0e"]
        for i, w in enumerate(windows):
            col = f"sma_{w}"
            vals = mas.get(col, [])
            if vals and len(vals) == len(dates_raw):
                bt_ma = [vals[i] for i, m in enumerate(bt_mask) if m]
                color = ma_colors.get(w, default_palette[i % len(default_palette)])
                ax1.plot(bt_dates, bt_ma, color=color, lw=1, alpha=0.6, label=f"MA{w}")


    base_price = bt_bh[0]
    norm_bh = np.asarray([(v / base_price) * 100 for v in bt_bh], dtype=float)
    norm_cash = np.asarray(raw_cash.values, dtype=float) / INITIAL_CASH * 100
    norm_pos = np.asarray(raw_shares.values, dtype=float) * np.asarray(bt_bh, dtype=float) / INITIAL_CASH * 100

    if trend_buy:
        ax1.scatter([t["date"] for t in trend_buy], [t["price"] for t in trend_buy],
                     c="cyan", marker="^", s=12, zorder=5, label="Trend Buy")
    if dip_buy:
        ax1.scatter([t["date"] for t in dip_buy], [t["price"] for t in dip_buy],
                     c="green", marker="^", s=12, zorder=5, label="Dip/Seed")
    if deep_buy:
        ax1.scatter([t["date"] for t in deep_buy], [t["price"] for t in deep_buy],
                     c="gold", marker="*", s=12, zorder=5, label="Deep/Btm")
    if lt_sells:
        ax1.scatter([t["date"] for t in lt_sells], [t["price"] for t in lt_sells],
                     c="red", marker="v", s=12, zorder=5, label="Lt Sell")
    if hy_sells:
        ax1.scatter([t["date"] for t in hy_sells], [t["price"] for t in hy_sells],
                     c="#8B0000", marker="v", s=12, zorder=6, label="Hy Sell")

    duration = bt.get("period_years", 0)
    strategy_label = asset.get("strategy_name", "")
    tactic_label = asset.get("tactic_name", "")
    title_str = f"{sym} {name} Backtest of {duration:.1f}yr [{strategy_label}]"
    if tactic_label and tactic_label != strategy_label:
        title_str += f" ({tactic_label})"
    ax1.set_title(title_str, fontsize=11, fontweight="bold", loc="left")
    ax1.legend(loc="upper left", fontsize=8, ncol=4)
    ax1.grid(True, alpha=0.3)
    fig.tight_layout()
    chart1_path = charts_dir / f"{sym}_simulated_actions.png"
    fig.savefig(chart1_path, dpi=150)
    plt.close(fig)

    # Chart 2: Performance
    fig, ax2 = plt.subplots(figsize=(10, 4.5))
    ax2.stackplot(bt_dates, norm_pos, norm_cash,
                  labels=["Strategy Position", "Strategy Cash"],
                  colors=["#87CEFA", "#E0E0E0"], alpha=0.3, zorder=0)
    ax2.plot(bt_dates, norm_bh, "k-", lw=1.5, label="B&H (Index)")
    ax2.axhline(100, color="k", linestyle=":", alpha=0.5)

    data_period2 = f"Data: {bt_dates[0].strftime('%Y-%m-%d')} → {bt_dates[-1].strftime('%Y-%m-%d')}"
    strat_metrics = (
        f"Strategy:  CAGR {(1+meta['Strategy_Ret'])**(1/max(duration,1))-1:+.1%}"
        f" | Ret {meta['Strategy_Ret']:+.1%} | DD {meta['Strategy_DD']:.1%}"
        f" | Vol {meta['Strat_Vol']:.1%} | Sharpe {meta['Strat_Sharpe']:.2f}"
    )
    bh_metrics = (
        f"B&H:       CAGR {(1+meta['BuyHold_Ret'])**(1/max(duration,1))-1:+.1%}"
        f" | Ret {meta['BuyHold_Ret']:+.1%} | DD {meta['BuyHold_DD']:.1%}"
        f" | Vol {meta['BuyHold_Vol']:.1%} | Sharpe {meta['BuyHold_Sharpe']:.2f}"
    )
    subtitle_text = f"{strat_metrics}\n{bh_metrics}\n{data_period2}"

    ax2.text(0.0, 1.02, subtitle_text, transform=ax2.transAxes,
             fontsize=9, color="#333333", va="bottom", ha="left", family="monospace")
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(True, alpha=0.3)
    ax2.set_ylabel("Normalized Value")
    fig.tight_layout()
    chart2_path = charts_dir / f"{sym}_performance.png"
    fig.savefig(chart2_path, dpi=150)
    plt.close(fig)

    return {"simulated_actions": chart1_path, "performance": chart2_path}


def _generate_report(assets: list[dict], region: str, run_date: str) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append(f"# Backtest Universe Report \u2014 {region.upper()}")
    lines.append(f"Generated: {generated} | Date: {run_date}")
    lines.append("")

    periods = []
    for a in assets:
        bt = a.get("backtest", a)
        ps = bt.get("period_start", "?")
        pe = bt.get("period_end", "?")
        py = bt.get("period_years", 0)
        if ps != "?":
            periods.append((ps, pe, py))
    if periods:
        common_start = min(p[0] for p in periods)
        common_end = max(p[1] for p in periods)
        lines.append(f"**Period**: {common_start} \u2192 {common_end} | **Assets**: {len(assets)}")
        lines.append("")

    for a in assets:
        bt = a.get("backtest", a)
        sym = bt.get("symbol", "?")
        name = bt.get("name", "?")
        typ = bt.get("type", "?")
        lines.append("---")
        lines.append(f"## {name} ({sym}) \u2014 {typ}")
        lines.append("")
        lines.append("### Performance: Strategy vs Buy & Hold")
        lines.append("")
        lines.append("| Metric | Strategy | Buy & Hold |")
        lines.append("|--------|----------|------------|")
        lines.append(f"| Return | {_pct(bt.get('strategy_ret'))} | {_pct(bt.get('buyhold_ret'))} |")
        lines.append(f"| Max Drawdown | {_pct(bt.get('strategy_dd'))} | {_pct(bt.get('buyhold_dd'))} |")
        lines.append(f"| Volatility | {_pct(bt.get('strat_vol'))} | {_pct(bt.get('buyhold_vol'))} |")
        lines.append(f"| Sharpe | {_fmt(bt.get('strat_sharpe'))} | {_fmt(bt.get('buyhold_sharpe'))} |")
        lines.append(f"| Trades | {bt.get('trades_count', 0)} | \u2014 |")
        lines.append("")

        chart_dir = BACKTEST_DIR / f"{run_date}_{region}_charts"
        chart1 = chart_dir / f"{sym}_simulated_actions.png"
        chart2 = chart_dir / f"{sym}_performance.png"
        if chart1.exists() and chart2.exists():
            rel_dir = f"{run_date}_{region}_charts"
            lines.append(f"![Simulated Actions]({rel_dir}/{sym}_simulated_actions.png)")
            lines.append("")
            lines.append(f"![Performance]({rel_dir}/{sym}_performance.png)")
            lines.append("")

        sim_actions = a.get("sim_actions") or a.get("trades", [])
        if sim_actions:
            lines.append("### Simulated Actions")
            lines.append("")
            lines.append("| Date | Action | Description | Price |")
            lines.append("|------|--------|-------------|-------|")
            for t in sim_actions[-20:]:
                dt = str(t.get("date", ""))[:10]
                act = str(t.get("action", t.get("type", "")))[:6]
                desc = str(t.get("desc", ""))[:30]
                price = t.get("price", t.get("val", 0))
                price_str = f"{price:.4f}" if isinstance(price, (int, float)) else str(price)
                lines.append(f"| {dt} | {act} | {desc} | {price_str} |")
            lines.append("")

    lines.append("---")
    lines.append("## Underlying Assets Performance")
    lines.append("")
    lines.append("| Asset | Type | Return | MaxDD | Vol | Sharpe |")
    lines.append("|------|------|-------:|------:|----:|------:|")
    for a in assets:
        bt = a.get("backtest", a)
        name = bt.get("name", "")[:20]
        sym = bt.get("symbol", "?")
        typ = bt.get("type", "")[:8]
        ret = _pct(bt.get("strategy_ret"))
        dd = _pct(bt.get("strategy_dd"))
        vol = _pct(bt.get("strat_vol"))
        shp = _fmt(bt.get("strat_sharpe"))
        lines.append(f"| {name} ({sym}) | {typ} | {ret} | {dd} | {vol} | {shp} |")

    valid = [a for a in assets if isinstance(a.get("backtest", {}).get("strategy_ret"), float)]
    if valid:
        eq_ret = sum(a["backtest"]["strategy_ret"] for a in valid) / len(valid)
        eq_dd = sum(a["backtest"]["strategy_dd"] for a in valid) / len(valid)
        eq_vol = sum(a["backtest"]["strat_vol"] for a in valid) / len(valid)
        eq_shp = sum(a["backtest"]["strat_sharpe"] for a in valid) / len(valid)
        lines.append(f"| **Portfolio (Equal Weight)** | **COMBINED** | **{_pct(eq_ret)}** | **{_pct(eq_dd)}** | **{_pct(eq_vol)}** | **{_fmt(eq_shp)}** |")

    lines.append("")
    return "\n".join(lines)


def _save_report(assets: list[dict], region: str, run_date: str) -> None:
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    charts_dir = BACKTEST_DIR / f"{run_date}_{region}_charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    chart_count = 0
    for a in assets:
        paths = _generate_charts(a, charts_dir)
        chart_count += len(paths)

    report = _generate_report(assets, region, run_date)
    path = BACKTEST_DIR / f"{run_date}_{region}_backtest_report.md"
    path.write_text(report, encoding="utf-8")
    print(f"\nBacktest universe report saved -> {path}")
    if chart_count:
        print(f"Charts saved -> {charts_dir}/ ({chart_count} PNGs)")


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------

STRATEGIES_DIR = _WORKSPACE_ROOT / "config" / "strategies"


def _resolve_default_strategy(strategy_type: str, symbol: str = "") -> str | None:
    """Resolve strategy name from strategy_routing.yaml.

    When symbol is provided, uses resolve_strategy_for_asset for per-asset
    overrides (symbol + optional tactic). Falls back to species_defaults.
    """
    from skills.analyze.scripts.s4_strategy.registry import StrategyRegistry
    
    registry = StrategyRegistry()
    
    # Priority 1: with symbol → resolve_strategy_for_asset (checks routing yaml)
    if symbol:
        try:
            strategy = registry.resolve_strategy_for_asset(symbol, strategy_type)
            pn = strategy.profile.get("name", "?")
            tn = strategy.tactic.get("name", "?")
            return f"{pn}+{tn}"
        except Exception:
            pass
    
    # Priority 2: species default from routing yaml
    try:
        strategy = registry.resolve_strategy_for(strategy_type)
        pn = strategy.profile.get("name", "?")
        tn = strategy.tactic.get("name", "?")
        return f"{pn}+{tn}"
    except Exception:
        return None


def _resolve_profile_sma_windows(strategy_name: str) -> list[int]:
    """Get profile's explicit sma window list for a strategy name.
    
    Falls back to parse routing display labels (e.g. 'dual-ma+dual-ma-follow')
    to extract profile name, since registry.load() may return a fallback profile
    for display-name lookups.
    """
    # Try parsing display label "profile+tactic" → load profile directly
    try:
        from skills.analyze.scripts.s4_strategy.registry import StrategyRegistry
        registry = StrategyRegistry()
        profile_name = strategy_name.split("+")[0] if "+" in strategy_name else strategy_name
        profile = registry.load_profile(profile_name)
        sma = profile.get("indicators", {}).get("sma", [])
        if isinstance(sma, int):
            return [sma]
        return sorted(sma) if sma else []
    except Exception:
        pass
    # Fallback: try direct strategy YAML load
    try:
        from skills.analyze.scripts.s4_strategy.registry import StrategyRegistry
        strategy = StrategyRegistry().load(strategy_name)
        sma = strategy.profile.get("indicators", {}).get("sma", [])
        if isinstance(sma, int):
            return [sma]
        return sorted(sma) if sma else []
    except Exception:
        return []


def run_single(symbol: str, run_date: str, strategy_override: str | None = None,
               debug: bool = False) -> Path:
    """Run backtest for a single symbol and output combined PNG to adhoc/."""
    from skills.analyze.scripts.strategy import run_strategy_pipeline

    ConfigLoader()
    manifest = AssetManifest()
    assets_all = manifest.get_all()
    asset = next((a for a in assets_all if a.symbol.upper() == symbol.upper()), None)

    if not asset:
        print(f"[ERROR] Symbol {symbol!r} not found in asset master")
        sys.exit(1)

    region = "us" if asset.region.upper() == "US" else "cn"
    rgn = region
    name = asset.name
    desc = asset.description
    strat = getattr(asset, "strategy_type", "STEADY")
    asset_sleeve = getattr(asset, "sleeve", "")
    asset_tags = list(getattr(asset, "tags", []))

    df = resolve_price_data(_WORKSPACE_ROOT, symbol, rgn)
    if df is None:
        print(f"[ERROR] No price data for {symbol}")
        sys.exit(1)

    print(f"  Data: {df['date'].min()} → {df['date'].max()} ({len(df)} days)")

    # Resolve default strategy from config/strategies/*.yaml based on strategy_type
    if strategy_override:
        strategy_name = strategy_override
        force_strategy = True
    else:
        force_strategy = False
        resolved = _resolve_default_strategy(strat, symbol)
        if resolved:
            strategy_name = resolved
            print(f"  [Default strategy] {strat} → {resolved}")
        else:
            strategy_name = "dca-7s"
            print(f"  [Default strategy] {strat} → dca-7s (fallback)")

    if strategy_name:
        result = run_strategy_pipeline(
            df,
            {"symbol": symbol, "name": name, "description": desc,
             "strategy_class": strat, "sleeve": asset_sleeve,
             "tags": asset_tags, "region": rgn},
            strategy_name=strategy_name,
            force_strategy=force_strategy,
        )
        if result is None:
            print(f"[ERROR] Strategy pipeline failed for {symbol}")
            sys.exit(1)
        # Resolve profile SMA windows for dynamic MA rendering
        _profile_sma = _resolve_profile_sma_windows(strategy_name)
    else:
        _profile_sma = []

    entry = _build_asset_entry(result, symbol, name, run_date, profile_sma_windows=_profile_sma)

    ADHOC_DIR.mkdir(parents=True, exist_ok=True)
    png_path = ADHOC_DIR / f"{symbol}_backtest_{run_date}.png"
    _generate_combined_chart(entry, png_path)

    m = result["meta"]
    print(f"\n  [{symbol:>8}] S:{m['Strategy_Ret']:.1%}  B:{m['BuyHold_Ret']:.1%}  \u03b1:{m['Strategy_Ret'] - m['BuyHold_Ret']:+.1%}")
    print(f"  Combined chart -> {png_path}")

    # Debug: save trade log CSV with cash, market_value, equity columns
    if debug:
        trades = result.get("trades", [])
        csv_path = ADHOC_DIR / f"{symbol}_trades_{run_date}.csv"
        import csv, json
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            # Header — dynamically include signal columns from first trade's signals dict
            sample_trade = trades[0] if trades else {}
            sample_signals = sample_trade.get("signals", {}) if isinstance(sample_trade.get("signals"), dict) else {}
            signal_keys = sorted(sample_signals.keys())
            header = ["date", "type", "price", "shares_delta", "amt_delta",
                      "cash_after", "position_value", "total_equity", "reason"]
            header += list(signal_keys)
            w.writerow(header)
            for t in trades:
                d = str(t.get("date", ""))[:10]
                action = t.get("type", t.get("action", "?"))
                price = t.get("val", 0)
                shares_d = t.get("shares_delta", 0)
                amt = t.get("amt_delta", 0)
                desc = t.get("desc", "")
                cash_after = t.get("cash_after", 0)
                pos_val = t.get("position_value", 0)
                total_eq = t.get("total_equity", 0)
                signals = t.get("signals", {}) if isinstance(t.get("signals"), dict) else {}
                row = [d, action, f"{float(price):.2f}",
                       f"{float(shares_d):+.4f}", f"{float(amt):+.2f}",
                       f"{float(cash_after):.2f}", f"{float(pos_val):.2f}", f"{float(total_eq):.2f}",
                       desc]
                for sk in signal_keys:
                    sv = signals.get(sk, "")
                    if isinstance(sv, float):
                        row.append(f"{sv:.4f}")
                    else:
                        row.append(str(sv))
                w.writerow(row)
        print(f"  Trade log   -> {csv_path}")
        print(f"  Trade log   -> {csv_path}")

    return png_path


def run_region(region: str, run_date: str, with_report: bool = False,
               symbol: str | None = None, symbols: str | None = None,
               use_active_state: bool = False, use_watchlist_state: bool = False,
               use_void_state: bool = False,
               strategy_override: str | None = None) -> None:
    ConfigLoader()
    manifest = AssetManifest()

    # Resolve symbols from selectors
    selected_syms = resolve_symbols_from_args(
        _WORKSPACE_ROOT, region=region, symbol=symbol, symbols=symbols,
        use_active_state=use_active_state,
        use_watchlist_state=use_watchlist_state,
        use_void_state=use_void_state,
    )

    all_assets = manifest.get_by_region(region.upper())
    region_map = {a.symbol: a for a in all_assets}
    assets = [region_map[sym] for sym in selected_syms if sym in region_map]

    if not assets:
        print(f"[WARN] No assets found for region={region!r}")
        return

    print(f"\nBacktest \u2014 region={region}, date={run_date} ({len(assets)} assets)")
    print(f"Assets: {len(assets)}\n")

    results = []
    for asset in assets:
        sym   = asset.symbol
        name  = asset.name
        desc  = asset.description
        strat = getattr(asset, "strategy_type", "STEADY")
        rgn   = "us" if region.upper() == "US" else "cn"

        df = resolve_price_data(_WORKSPACE_ROOT, sym, rgn)
        if df is None:
            print(f"  [SKIP] {sym}: no price data")
            continue

        # Resolve strategy per asset from config mapping
        resolved_strat = strategy_override or _resolve_default_strategy(strat, sym) or "dca-7s"
        result = run_strategy_pipeline(
            df,
            {"symbol": sym, "name": name, "description": desc,
             "strategy_class": strat, "sleeve": getattr(asset, "sleeve", ""),
             "tags": list(getattr(asset, "tags", [])), "region": rgn},
            strategy_name=resolved_strat,
            force_strategy=bool(strategy_override),
        )
        if result is None:
            print(f"  [SKIP] {sym}: insufficient data")
            continue

        m = result["meta"]
        _profile_sma = _resolve_profile_sma_windows(resolved_strat)
        entry = _build_asset_entry(result, sym, name, run_date, profile_sma_windows=_profile_sma)
        results.append(entry)
        print(f"  [{sym:>8}] S:{m['Strategy_Ret']:.1%}  B:{m['BuyHold_Ret']:.1%}  \u03b1:{m['Strategy_Ret'] - m['BuyHold_Ret']:+.1%}")

    if not results:
        return

    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    prev_path = BACKTEST_DIR / f"latest_{region}.json"

    previous = _load_previous(prev_path) if prev_path.exists() else None
    for r in results:
        prev_data = None
        if previous:
            for p in previous:
                if p.get("symbol") == r["symbol"]:
                    prev_data = p.get("backtest", p)
                    break
        r["delta"] = _compute_delta(r["backtest"], prev_data)

    any_previous = any(r["delta"].get("has_previous") for r in results)
    if any_previous:
        warnings = [r for r in results if r["delta"].get("deltas", {}).get("trend_warning")]
        if warnings:
            print("\n\u26a0\ufe0f  Trend warnings:")
            for w in warnings:
                warn = w["delta"]["deltas"]["trend_warning"]
                print(f"  {w['symbol']:<8} {warn}")
            print()

    _print_results(results, with_previous=any_previous)

    dated_path = BACKTEST_DIR / f"{run_date}_{region}.json"
    with open(dated_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nBacktest saved -> {dated_path}")

    with open(prev_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Backtest saved -> {prev_path}")

    if with_report:
        _save_report(results, region, run_date)


def run_report_only(region: str, run_date: str) -> None:
    latest_path = BACKTEST_DIR / f"latest_{region}.json"
    if not latest_path.exists():
        dated_fallback = BACKTEST_DIR / f"{run_date}_{region}.json"
        if dated_fallback.exists():
            latest_path = dated_fallback
        else:
            print(f"[WARN] No backtest data for region={region!r} at {run_date}")
            return

    try:
        with open(latest_path) as f:
            assets = json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to load {latest_path}: {e}")
        return

    _save_report(assets, region, run_date)


def main():
    available_strategies = []
    try:
        from skills.analyze.scripts.s4_strategy.registry import StrategyRegistry
        available_strategies = StrategyRegistry().list_strategies()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="7S standalone backtest runner")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Single symbol to backtest")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbol basket")
    parser.add_argument("--active", action="store_true",
                        help="Backtest active holdings")
    parser.add_argument("--watchlist", action="store_true",
                        help="Backtest watchlist")
    parser.add_argument("--void", action="store_true",
                        help="Backtest void assets")
    parser.add_argument("--region", default="all", choices=["cn", "us", "all"],
                        help="Region to run (default: all)")
    parser.add_argument("--date", default=str(date.today()),
                        help="Date marker (default: today)")
    parser.add_argument("--strategy", type=str, default=None,
                        choices=available_strategies or None,
                        help=f"Strategy to use (default: dca-7s). Available: {available_strategies}")
    parser.add_argument("--debug", action="store_true",
                        help="Print full trade log after backtest")
    parser.add_argument("--report", action="store_true",
                        help="Also generate backtest universe report (region only)")
    parser.add_argument("--report-only", action="store_true",
                        help="Generate report from existing data, skip backtest")
    args = parser.parse_args()

    if args.symbol:
        run_single(args.symbol, args.date, strategy_override=args.strategy,
                   debug=args.debug)
        return

    region = args.region or "all"
    regions = ["cn", "us"] if region == "all" else [region]

    if args.report_only:
        for r in regions:
            run_report_only(r, args.date)
        return

    for r in regions:
        run_region(r, args.date, with_report=args.report,
                   symbol=args.symbol, symbols=args.symbols,
                   use_active_state=args.active,
                   use_watchlist_state=args.watchlist,
                   use_void_state=args.void)


if __name__ == "__main__":
    main()
