"""run_momentum.py — Momentum rotation CLI.

Modes:
  - Scan: rank assets, dual-threshold signals, per-asset chart (default)
  - Rotate: simulate periodic rebalance of top-N holdings (--rotate)

Multi-period: combine short(20d), medium(60d), long(120d) via weighted average.
Chart layout: rank summary bar (top) + per-asset price/score (below).

Usage:
    python3 skills/momentum/scripts/run_momentum.py --active
    python3 skills/momentum/scripts/run_momentum.py --symbol 159259,512050,159263,159207
    python3 skills/momentum/scripts/run_momentum.py --active --method multi

    # Rotate simulation
    python3 skills/momentum/scripts/run_momentum.py --active --rotate
    python3 skills/momentum/scripts/run_momentum.py --symbol A,B,C --rotate --reb-period 20 --top-n 2
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

_THIS = Path(__file__).resolve()
_WORKSPACE_ROOT = _THIS.parents[3]
sys.path.insert(0, str(_WORKSPACE_ROOT))

from dao.asset_dao import AssetManifest
from utils.data_service.data_resolver import resolve_price_data
from utils.symbols.state_resolver import detect_region, resolve_symbols_from_args, load_state_symbols
from skills.analyze.scripts.s4_strategy.registry import StrategyRegistry

from skills.analyze.scripts.s4_strategy.signal_computer import compute_profile
from skills.momentum.scripts.momentum_engine import (
    calc_simple_momentum, calc_slope_momentum, calc_composite, calc_multi_period,
    calc_daily_momentum,
)
from skills.momentum.scripts.ranker import (
    MomentumResult, rank, signal_for, generate_decision,
)


ADHOC_DIR = _WORKSPACE_ROOT / "adhoc" / "momentum"
INITIAL_CASH = 100_000.0

# Load momentum profile once
_MOMENTUM_PROFILE = None


def _get_momentum_profile() -> dict:
    global _MOMENTUM_PROFILE
    if _MOMENTUM_PROFILE is None:
        try:
            reg = StrategyRegistry()
            _MOMENTUM_PROFILE = reg.load_profile("momentum")
        except Exception:
            _MOMENTUM_PROFILE = None
    return _MOMENTUM_PROFILE


def _compute_momentum_score(df, method: str, period: int, profile=None) -> float:
    """Compute momentum score using signal_computer when possible, else fallback."""
    if profile and method in ("simple", "slope"):
        result_df = compute_profile(df, profile, symbol="")
        if result_df is not None:
            if method == "simple" and "roc" in result_df.columns:
                vals = result_df["roc"].dropna().values
                return float(vals[-1] / 100.0) if len(vals) > 0 else 0.0
            if method == "slope" and "slope" in result_df.columns:
                vals = result_df["slope"].dropna().values
                return float(vals[-1] / 252.0) if len(vals) > 0 else 0.0
    if method == "simple":
        return calc_simple_momentum(df, period)
    elif method == "slope":
        return calc_slope_momentum(df, period)
    elif method == "multi":
        return calc_multi_period(df, method="simple")
    else:
        return calc_simple_momentum(df, period)


def _compute_daily_scores(df, method: str, period: int) -> list:
    """Compute daily momentum scores using signal_computer when possible."""
    profile = _get_momentum_profile()
    if profile:
        result_df = compute_profile(df, profile, symbol="")
        if result_df is not None and method in ("simple", "slope"):
            col_name = "roc" if method == "simple" else "slope"
            if col_name in result_df.columns:
                import pandas as pd
                vals = result_df[col_name].values
                dates = result_df["date"].values
                return [(str(pd.to_datetime(dates[i]).strftime("%Y-%m-%d")),
                         float(vals[i] / 100.0 if col_name == "roc" else vals[i] / 252.0))
                        for i in range(period, len(vals))]
    return calc_daily_momentum(df, method=method, period=period)


def _maxdd(series: list) -> tuple:
    """Return (current_dd, max_dd_fraction)."""
    if len(series) < 2:
        return 0.0, 0.0
    peak = series[0]
    max_dd = 0.0
    for v in series:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
    current_dd = (peak - series[-1]) / peak if peak > 0 else 0.0
    return current_dd, max_dd


def _lookup_name(symbol: str) -> str:
    try:
        manifest = AssetManifest()
        assets = [a for a in manifest.get_all() if a.symbol.upper() == symbol.upper()]
        if assets:
            return assets[0].name
    except Exception:
        pass
    return symbol


# ═══════════════════════════════════════════════════════════════════════════
#  CHART — SCAN MODE
# ═══════════════════════════════════════════════════════════════════════════

def _build_scan_chart(
    symbols: list[str], names: list[str], scores: list[float], signals: list[str],
    histories: dict[str, list[tuple[str, float]]], dfs: dict[str, pd.DataFrame],
    buy_threshold: float, sell_threshold: float, method: str, period: int,
    output_path: Path,
) -> None:
    """Summary bar (top) + per-asset price/score (below)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import pandas as pd

    n = len(symbols)
    fig = plt.figure(figsize=(12, 5 + 2.5 * n))
    gs = fig.add_gridspec(n + 1, 1, height_ratios=[2] + [2.5] * n, hspace=0.4)

    sig_colors = {"BUY": "#2ecc71", "HOLD": "#f1c40f", "SELL": "#e74c3c", "IGNORE": "#95a5a6"}

    # ── Summary rank bar ──
    ax_top = fig.add_subplot(gs[0])
    bar_colors = [sig_colors.get(s, "#95a5a6") for s in signals]
    bars = ax_top.barh(range(len(symbols)), scores, color=bar_colors, height=0.5, edgecolor="white")
    ax_top.set_yticks(range(len(symbols)))
    ax_top.set_yticklabels([f"{sym} {names[i][:16]}" for i, sym in enumerate(symbols)], fontsize=8)
    ax_top.axvline(0, color="black", linewidth=0.5)
    ax_top.axvline(buy_threshold, color="#2ecc71", linestyle="--", alpha=0.5)
    ax_top.axvline(sell_threshold, color="#e74c3c", linestyle="--", alpha=0.5)
    ax_top.set_title(f"Momentum Ranking — {method} (period={period})", fontsize=11, fontweight="bold")
    ax_top.set_xlabel("Score")
    ax_top.grid(True, alpha=0.2)

    # Signal legend
    legend_elements = [
        plt.Rectangle((0, 0), 1, 1, facecolor="#2ecc71", label="BUY"),
        plt.Rectangle((0, 0), 1, 1, facecolor="#f1c40f", label="HOLD"),
        plt.Rectangle((0, 0), 1, 1, facecolor="#e74c3c", label="SELL"),
    ]
    ax_top.legend(handles=legend_elements, fontsize=8, loc="lower right")

    one_year_ago = datetime.now() - timedelta(days=365)

    for idx, sym in enumerate(symbols):
        ax1 = fig.add_subplot(gs[idx + 1])
        ax2 = ax1.twinx()
        name = names[idx]

        df = dfs.get(sym)
        if df is not None and len(df) > 0:
            dts = pd.to_datetime(df["date"])
            log_prices = np.log(df["total_return"].values.astype(float))
            mask = dts >= pd.Timestamp(one_year_ago)
            ax1.plot(dts[mask], log_prices[mask], "k", lw=1.2, alpha=0.8)

        hist = histories.get(sym, [])
        if hist:
            hd = [datetime.strptime(h[0], "%Y-%m-%d") for h in hist]
            hs = [h[1] for h in hist]
            hm = np.array([d >= one_year_ago for d in hd])
            if hm.any():
                hd_f = [hd[i] for i in range(len(hist)) if hm[i]]
                hs_f = [hs[i] for i in range(len(hist)) if hm[i]]
                ax2.plot(hd_f, hs_f, color="#3498db", linestyle=":", lw=1.5, alpha=0.8)
                s_min, s_max = min(hs_f), max(hs_f)
                rg = max(s_max - s_min, 0.1)
                ax2.set_ylim(s_min - rg * 0.2, s_max + rg * 0.2)
                ax2.axhline(buy_threshold, color="#2ecc71", linestyle="--", alpha=0.5)
                ax2.axhline(sell_threshold, color="#e74c3c", linestyle="--", alpha=0.5)

                dts_num = mdates.date2num(dts[mask]) if df is not None else np.array([])
                lp = log_prices[mask] if df is not None else np.array([])
                if len(dts_num) > 0:
                    hn = np.array([mdates.date2num(d) for d in hd_f], dtype=float)
                    bm = np.array([hs_f[i] > buy_threshold for i in range(len(hd_f))])
                    sm = np.array([hs_f[i] < sell_threshold for i in range(len(hd_f))])
                    if bm.any():
                        bn = hn[bm]
                        ax1.scatter([mdates.num2date(n) for n in bn], np.interp(bn, dts_num, lp),
                                    c="#2ecc71", marker="^", s=35, zorder=5, edgecolors="white", linewidth=0.5)
                    if sm.any():
                        sn = hn[sm]
                        ax1.scatter([mdates.num2date(n) for n in sn], np.interp(sn, dts_num, lp),
                                    c="#e74c3c", marker="v", s=35, zorder=5, edgecolors="white", linewidth=0.5)

        sig = signals[idx]
        sc = sig_colors.get(sig, "#95a5a6")
        ax1.set_title(f"{name} ({sym})  Score: {scores[idx]:+.4f}  [{sig}]", fontsize=9, color=sc)
        ax1.set_ylabel("Log Price", fontsize=8)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax1.tick_params(labelsize=7)
        ax2.set_ylabel("Score", color="#3498db", fontsize=8)
        ax2.tick_params(axis="y", labelcolor="#3498db", labelsize=7)

    fig.suptitle("Momentum Rotation", fontsize=14, fontweight="bold")
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
#  CHART — ROTATE MODE
# ═══════════════════════════════════════════════════════════════════════════

def _build_rotate_chart(
    trade_log: list[dict], equity_curve: list[dict], bh_values: dict[str, list[float]],
    dfs: dict[str, pd.DataFrame], top_n: int, period: int, reb_period: int,
    output_path: Path,
) -> None:
    """Two-panel: log price + trade markers (top), equity curve (bottom)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import pandas as pd

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={"hspace": 0.35})

    # Extract equity curve data
    ec_dates = [datetime.strptime(e["date"], "%Y-%m-%d") for e in equity_curve]
    strat_vals = [e["strategy"] for e in equity_curve]
    bh_vals = [e["buyhold"] for e in equity_curve]

    # --- Top panel: normalized log prices with trade markers (draw first for colors) ---
    for sym, df in dfs.items():
        if df is not None and len(df) > 0:
            dts = pd.to_datetime(df["date"])
            tr = df["total_return"].values.astype(float)
            lp = np.log(tr / tr[0])  # Normalize: all start at 0
            ret = (tr[-1] / tr[0] - 1) * 100
            ax1.plot(dts, lp, lw=0.8, alpha=0.5,
                     label=f"{_lookup_name(sym)} ({ret:+.1f}%)")

    # Fill trades
    for t in trade_log:
        dt = datetime.strptime(t["date"], "%Y-%m-%d")
        action = t["action"]
        c = "#e74c3c" if action == "SELL" else "#2ecc71"
        marker = "v" if action == "SELL" else "^"
        sym = t.get("symbol", "")
        if sym:
            df = dfs.get(sym)
            if df is not None:
                dts = pd.to_datetime(df["date"])
                idx = (dts - pd.Timestamp(dt)).abs().argmin()
                first_tr = float(df["total_return"].values[0])
                price = np.log(float(df["total_return"].values[idx]) / first_tr)
                ax1.scatter(dt, price, c=c, marker=marker, s=25, zorder=5,
                            edgecolors="white", linewidth=0.5)

    ax1.set_title(f"Rotation Simulation — Top-{top_n}, rebalance every {reb_period}d",
                  fontsize=11, fontweight="bold")
    ax1.set_ylabel("Rel. Log Price (normalized)")
    handles, labels = ax1.get_legend_handles_labels()
    from matplotlib.lines import Line2D
    handles.append(Line2D([0], [0], marker="^", color="w", markerfacecolor="#2ecc71",
                          markersize=8, label="BUY"))
    handles.append(Line2D([0], [0], marker="v", color="w", markerfacecolor="#e74c3c",
                          markersize=8, label="SELL"))
    if handles:
        ax1.legend(handles=handles, fontsize=7, ncol=4)
    ax1.grid(True, alpha=0.2)

    # --- Bottom panel: holdings composition stack (absolute value) ---
    # Extract holdings composition from equity_curve
    all_syms: list[str] = []
    for e in equity_curve:
        for sym in e.get("holdings", {}):
            if sym not in all_syms:
                all_syms.append(sym)
    all_syms = sorted([s for s in all_syms if s != "$CASH"])
    if "$CASH" in [s for e in equity_curve for s in e.get("holdings", {})]:
        all_syms.append("$CASH")

    if all_syms:
        stack_data: dict[str, list[float]] = {sym: [] for sym in all_syms}
        for e in equity_curve:
            h = e.get("holdings", {})
            for sym in all_syms:
                stack_data[sym].append(h.get(sym, 0))

        for sym in all_syms:
            stack_data[sym] = [v / INITIAL_CASH * 100 for v in stack_data[sym]]

        # Match colors from top panel lines
        line_colors = {}
        for line in ax1.get_lines():
            lbl = line.get_label()
            if lbl not in ("BUY", "SELL", ""):
                base = lbl.split(" (")[0]
                line_colors[base] = line.get_color()
        stack_colors = []
        stack_labels = []
        for sym in all_syms:
            name = _lookup_name(sym) if sym != "$CASH" else "Cash"
            if sym == "$CASH":
                stack_colors.append("#d5d5d5")
            else:
                stack_colors.append(line_colors.get(name, plt.cm.Set2(len(stack_colors))))
            stack_labels.append(name)

        ax2.stackplot(ec_dates, [stack_data[sym] for sym in all_syms],
                      labels=stack_labels, colors=stack_colors, alpha=0.85)
        ax2.axhline(100, color="k", linestyle=":", alpha=0.4)

    ret_s = (strat_vals[-1] / strat_vals[0] - 1) * 100
    ret_b = (bh_vals[-1] / bh_vals[0] - 1) * 100

    daily_s = np.diff(strat_vals) / np.array(strat_vals[:-1])
    daily_b = np.diff(bh_vals) / np.array(bh_vals[:-1])
    vol_s = np.std(daily_s) * np.sqrt(252)
    vol_b = np.std(daily_b) * np.sqrt(252)
    shp_s = np.mean(daily_s) / np.std(daily_s) * np.sqrt(252) if np.std(daily_s) > 0 else 0
    shp_b = np.mean(daily_b) / np.std(daily_b) * np.sqrt(252) if np.std(daily_b) > 0 else 0

    ax2.set_title(f"Holdings Composition  |  Strategy: {ret_s:+.1f}% Vol:{vol_s*100:.1f}% Shp:{shp_s:.2f}  |  B&H: {ret_b:+.1f}% Vol:{vol_b*100:.1f}% Shp:{shp_b:.2f}",
                  fontsize=10, fontweight="bold")
    ax2.set_ylabel("Normalized Value (100 = start)")
    if stack_labels:
        ax2.legend(fontsize=7, ncol=min(len(stack_labels), 4), loc="upper left")
    ax2.grid(True, alpha=0.2)

    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
#  RUN — SCAN
# ═══════════════════════════════════════════════════════════════════════════

def run_scan(selected_syms: list[str], method: str, period: int,
             buy_threshold: float, sell_threshold: float,
             dfs: dict) -> None:
    """Scan mode: rank assets, print signals, generate chart."""
    active_symbols = load_state_symbols(_WORKSPACE_ROOT, "active")

    results: list[MomentumResult] = []
    daily_histories: dict[str, list[tuple[str, float]]] = {}
    dfs_loaded: dict[str, pd.DataFrame] = {}

    for sym in selected_syms:
        rgn = detect_region(sym)
        df = resolve_price_data(_WORKSPACE_ROOT, sym, rgn)
        if df is None:
            print(f"  [SKIP] {sym}: no data")
            continue
        dfs_loaded[sym] = df
        name = _lookup_name(sym)

        profile = _get_momentum_profile()

        if method == "multi":
            score = _compute_momentum_score(df, "simple", 20, profile)
            daily_hist = _compute_daily_scores(df, "simple", 20)
        elif method == "composite":
            simple_score = _compute_momentum_score(df, "simple", period, profile)
            slope_score = _compute_momentum_score(df, "slope", period, profile)
            score = calc_composite(simple_score, slope_score, [0], [0])
            daily_hist = _compute_daily_scores(df, "simple", period)
        else:
            score = _compute_momentum_score(df, method, period, profile)
            daily_hist = _compute_daily_scores(df, method, period)

        daily_histories[sym] = daily_hist
        results.append(MomentumResult(symbol=sym, name=name, score=score, method=method, period=period))
        print(f"  [{sym:>8}] {name:<20} score={score:+.4f}")

    if not results:
        print("[ERROR] No assets could be scored")
        return

    ranking = rank(results, buy_threshold=buy_threshold, sell_threshold=sell_threshold,
                   active_symbols=active_symbols)

    # Table
    ms = {"simple": "S", "slope": "L", "composite": "C/Z", "multi": "M"}
    print(f"\n{'='*70}")
    print(f"  Momentum — {ms.get(method, method)} (period={period})")
    print(f"  BUY > {buy_threshold:+.2f} | SELL < {sell_threshold:+.2f}")
    print(f"{'='*70}")
    print(f"  {'Rank':<6} {'Symbol':<8} {'Name':<22} {'Score':<10} {'Signal':<8}")
    print(f"  {'-'*54}")
    for i, r in enumerate(ranking.results):
        sig = signal_for(ranking, r)
        print(f"  #{i+1:<4} {r.symbol:<8} {r.name:<22} {r.score:+.4f}   {sig:<8}")

    decision = generate_decision(ranking)
    print(f"\n  Decision:\n  {'-'*40}")
    for line in decision.split("\n"):
        print(f"  {line}")

    # Chart
    ADHOC_DIR.mkdir(parents=True, exist_ok=True)
    mode = "scan"
    chart_path = ADHOC_DIR / f"{date.today().isoformat()}_{mode}_momentum.png"
    _build_scan_chart(
        [r.symbol for r in ranking.results], [r.name for r in ranking.results],
        [r.score for r in ranking.results], [signal_for(ranking, r) for r in ranking.results],
        daily_histories, dfs_loaded, buy_threshold, sell_threshold, method, period, chart_path,
    )
    print(f"\n  Chart -> {chart_path}")


# ═══════════════════════════════════════════════════════════════════════════
#  RUN — ROTATE
# ═══════════════════════════════════════════════════════════════════════════

def run_rotate(selected_syms: list[str], method: str, period: int,
               top_n: int, reb_period: int, ttm: int | None = None,
               buy_threshold: float = 0.05,
               spread_th: float = 0.0, slow_confirm: bool = False,
               vol_cap: float = 0.0) -> None:
    """Rotate mode: simulate periodic rebalance of top-N holdings."""
    import pandas as pd

    # Load data
    prices: dict[str, pd.Series] = {}
    names: dict[str, str] = {}
    dfs_rotate: dict[str, pd.DataFrame] = {}
    for sym in selected_syms:
        df = resolve_price_data(_WORKSPACE_ROOT, sym, detect_region(sym))
        if df is None or len(df) < period + 1:
            print(f"  [SKIP] {sym}: insufficient data")
            continue
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        prices[sym] = pd.Series(df["total_return"].values, index=df["date"])
        names[sym] = _lookup_name(sym)
        dfs_rotate[sym] = df

    if not prices:
        print("[ERROR] No assets with sufficient data")
        return

    # Build a common index of all dates
    common_idx = prices[list(prices.keys())[0]].index
    for s in prices:
        common_idx = common_idx.intersection(prices[s].index)
    common_idx = sorted(common_idx)

    # Align all prices
    aligned: dict[str, pd.Series] = {}
    for sym, s in prices.items():
        aligned[sym] = s.loc[common_idx].ffill().bfill()

    common_dates = list(pd.DatetimeIndex(common_idx))

    # TTM mode: last N trading days
    if ttm:
        cutoff = common_dates[-ttm] if len(common_dates) > ttm else common_dates[0]
        common_dates = [d for d in common_dates if d >= cutoff]
        for sym in aligned:
            aligned[sym] = aligned[sym].loc[common_dates]
        for sym in dfs_rotate:
            dfs_rotate[sym] = dfs_rotate[sym][dfs_rotate[sym]["date"] >= cutoff].copy()

    initial_capital = INITIAL_CASH

    # Simulate rotation
    cash = initial_capital
    holdings: dict[str, float] = {}
    trade_log: list[dict] = []
    equity_curve: list[dict] = []
    bh_value = initial_capital
    bh_initial_prices: dict[str, float] = {}

    for i, dt in enumerate(common_dates):
        # Rebalance every reb_period days
        is_rebalance = (i % reb_period == 0) and (i >= period)

        if is_rebalance:
            # Compute momentum
            profile = _get_momentum_profile()
            scores: list[tuple[str, float]] = []
            for sym in aligned:
                df_slice = pd.DataFrame({"total_return": aligned[sym].iloc[:i + 1]})
                sc = _compute_momentum_score(df_slice, method, period, profile)
                scores.append((sym, sc))

            scores.sort(key=lambda x: x[1], reverse=True)

            # ── Strategy layers ────────────────────────────────────
            # Base: top_symbols = top-N by momentum

            # Layer 1: Spread filter — hold both if gap too small
            use_top_symbols = [s[0] for s in scores[:top_n]]
            if spread_th > 0 and len(scores) >= 2:
                gap = scores[0][1] - scores[1][1]
                if abs(gap) < spread_th:
                    use_top_symbols = [s[0] for s in scores[:max(top_n, 2)]]

            # Layer 2: Slow confirm — require 20d and 60d alignment
            if slow_confirm and len(scores) >= 2:
                slow_scores = []
                for sym in aligned:
                    ss = pd.DataFrame({"total_return": aligned[sym].iloc[:i+1]})
                    sc60 = _compute_momentum_score(ss, method, min(60, period * 3), profile)
                    slow_scores.append((sym, float(sc60)))
                slow_scores.sort(key=lambda x: x[1], reverse=True)
                fast_winner = scores[0][0]
                slow_winner = slow_scores[0][0]
                if fast_winner != slow_winner:
                    use_top_symbols = list(dict.fromkeys([s[0] for s in scores[:top_n]]))

            # Layer 3: Vol cap — go equal-weight if vol too high
            if vol_cap > 0:
                for sym in [s[0] for s in scores[:top_n]]:
                    vals = aligned[sym].iloc[max(0,i-60):i+1].values
                    if len(vals) > 10:
                        dr = np.diff(vals) / vals[:-1]
                        ann_vol = float(np.std(dr) * np.sqrt(252))
                        if ann_vol > vol_cap:
                            use_top_symbols = [s[0] for s in scores[:max(top_n, 2)]]
                            break

            top_symbols = use_top_symbols

            # Sell positions not in top-N
            for held_sym in list(holdings.keys()):
                if held_sym not in top_symbols:
                    price = float(aligned[held_sym].iloc[i])
                    cash += holdings[held_sym] * price
                    trade_log.append({"date": str(dt.date()), "action": "SELL", "symbol": held_sym,
                                      "price": price, "shares": holdings[held_sym],
                                      "reason": f"dropped out of top-{top_n}"})
                    holdings.pop(held_sym, None)

            # Buy new top-N positions (equal weight)
            # Cash threshold: if top momentum too low, hold cash
            top_score = scores[0][1] if scores else 0
            all_below_threshold = top_score < buy_threshold

            if all_below_threshold:
                # Sell everything, hold cash
                for held_sym in list(holdings.keys()):
                    price = float(aligned[held_sym].iloc[i])
                    cash += holdings[held_sym] * price
                    trade_log.append({"date": str(dt.date()), "action": "SELL", "symbol": held_sym,
                                      "price": price, "shares": holdings[held_sym],
                                      "reason": f"momentum below threshold ({top_score:.3f} < {buy_threshold})"})
                    holdings.pop(held_sym, None)
            elif top_symbols:
                per_sym = cash / len(top_symbols)
                for bsym in top_symbols:
                    price = float(aligned[bsym].iloc[i])
                    if price > 0 and cash > 0 and cash >= per_sym:
                        shares = per_sym / price
                        holdings[bsym] = holdings.get(bsym, 0) + shares
                        cash -= per_sym
                        trade_log.append({"date": str(dt.date()), "action": "BUY", "symbol": bsym,
                                          "price": price, "shares": shares,
                                          "reason": f"top-{top_n} momentum (score={top_score:.3f})"})

        # Record equity
        pos_value = sum(holdings.get(sym, 0) * float(aligned[sym].iloc[i]) for sym in holdings)
        total_equity = cash + pos_value

        # Record equity with holdings composition
        holdings_detail: dict[str, float] = {}
        for sym in aligned:
            val = holdings.get(sym, 0) * float(aligned[sym].iloc[i])
            holdings_detail[sym] = val
        holdings_detail["$CASH"] = cash

        pos_value = sum(holdings_detail.get(sym, 0) for sym in aligned)
        total_equity = cash + pos_value

        # Buy-hold: equal weight at start, hold forever
        if i == 0:
            bh_per_sym = initial_capital / len(aligned)
            for sym in aligned:
                p = float(aligned[sym].iloc[0])
                if p > 0:
                    bh_initial_prices[sym] = p
        bh_value = sum(bh_per_sym * float(aligned[sym].iloc[i]) / bh_initial_prices.get(sym, 1)
                       for sym in aligned)

        equity_curve.append({
            "date": str(dt.date()),
            "strategy": total_equity / initial_capital * 100,
            "buyhold": bh_value / initial_capital * 100,
            "holdings": holdings_detail,
        })

    # Print summary
    strat_ret = (equity_curve[-1]["strategy"] / equity_curve[0]["strategy"] - 1) * 100
    bh_ret = (equity_curve[-1]["buyhold"] / equity_curve[0]["buyhold"] - 1) * 100

    # Compute volatility and Sharpe ratio
    strat_daily = np.diff([e["strategy"] for e in equity_curve]) / np.array([e["strategy"] for e in equity_curve[:-1]])
    bh_daily = np.diff([e["buyhold"] for e in equity_curve]) / np.array([e["buyhold"] for e in equity_curve[:-1]])
    strat_vol = float(np.std(strat_daily) * np.sqrt(252) * 100)
    bh_vol = float(np.std(bh_daily) * np.sqrt(252) * 100)
    strat_sharpe = float(np.mean(strat_daily) / np.std(strat_daily) * np.sqrt(252)) if np.std(strat_daily) > 0 else 0
    bh_sharpe = float(np.mean(bh_daily) / np.std(bh_daily) * np.sqrt(252)) if np.std(bh_daily) > 0 else 0

    # Compute drawdown
    _, strat_maxdd = _maxdd([e["strategy"] for e in equity_curve])
    _, bh_maxdd = _maxdd([e["buyhold"] for e in equity_curve])

    print(f"\n{'='*60}")
    layers = []
    if spread_th > 0: layers.append(f"spread={spread_th}")
    if slow_confirm: layers.append("slow-confirm")
    if vol_cap > 0: layers.append(f"vol-cap={vol_cap}")
    layers_str = f" [{', '.join(layers)}]" if layers else ""
    print(f"  Rotation Simulation — Top-{top_n}, rebalance every {reb_period}d{layers_str}")
    print(f"{'='*60}")
    print(f"          Return    Drawdown  Volatility  Sharpe")
    print(f"  Strategy: {strat_ret:>+7.1f}%  {strat_maxdd*100:>6.1f}%    {strat_vol:>5.1f}%     {strat_sharpe:.2f}")
    print(f"  B&H:      {bh_ret:>+7.1f}%  {bh_maxdd*100:>6.1f}%    {bh_vol:>5.1f}%     {bh_sharpe:.2f}")
    print(f"  ────────  ───────  ───────  ─────────  ──────")
    print(f"  Delta:    {strat_ret-bh_ret:>+7.1f}%  {(bh_maxdd-strat_maxdd)*100:>+6.1f}%   {bh_vol-strat_vol:>+5.1f}%   {strat_sharpe-bh_sharpe:>+5.2f}")
    print(f"  Trades: {len(trade_log)}  |  Period: {common_dates[0].date()} → {common_dates[-1].date()}")
    print(f"\n  Holdings at last rebalance:")
    for sym, sh in sorted(holdings.items(), key=lambda x: -x[1] * float(aligned[x[0]].iloc[-1])):
        val = sh * float(aligned[sym].iloc[-1])
        print(f"    {sym:<8} {names.get(sym, ''):<20} ${val:,.0f}")

    # Chart
    ADHOC_DIR.mkdir(parents=True, exist_ok=True)
    chart_path = ADHOC_DIR / f"{date.today().isoformat()}_rotate_momentum.png"
    _build_rotate_chart(trade_log, equity_curve, aligned, dfs_rotate, top_n, period, reb_period, chart_path)
    print(f"\n  Chart -> {chart_path}")


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="7S Momentum Rotation")
    parser.add_argument("--symbol", type=str, default=None, help="Symbol or comma-separated basket")
    parser.add_argument("--active", action="store_true", help="Scan active holdings")
    parser.add_argument("--watchlist", action="store_true", help="Scan watchlist")
    parser.add_argument("--void", action="store_true", help="Scan void assets")
    parser.add_argument("--region", default="all", choices=["cn", "us", "all"], help="Region")

    # Scoring
    parser.add_argument("--method", default="simple",
                        choices=["simple", "slope", "composite", "multi"],
                        help="Momentum method (multi = weighted short/medium/long)")
    parser.add_argument("--period", type=int, default=20, help="Lookback trading days (base)")
    parser.add_argument("--buy-th", type=float, default=0.05, help="BUY threshold")
    parser.add_argument("--sell-th", type=float, default=-0.02, help="SELL threshold")

    # Rotate
    parser.add_argument("--rotate", action="store_true", help="Run rotation simulation")
    parser.add_argument("--reb-period", type=int, default=20,
                        help="Rebalance period in trading days (rotate mode)")
    parser.add_argument("--top-n", type=int, default=2, help="Top-N assets to hold (rotate mode)")
    parser.add_argument("--ttm", nargs="?", type=int, const=252, default=None,
                        help="Trailing N trading days only (rotate mode, default: 252 = 1yr)")
    parser.add_argument("--spread-th", type=float, default=0.0,
                        help="Spread filter: hold both when gap < threshold (0=off)")
    parser.add_argument("--slow-confirm", action="store_true",
                        help="Slow confirm: require 20d & 60d alignment")
    parser.add_argument("--vol-cap", type=float, default=0.0,
                        help="Vol cap: equal-weight when vol exceeds (0=off)")

    args = parser.parse_args()

    selected_syms = resolve_symbols_from_args(
        _WORKSPACE_ROOT, region=args.region, symbol=None, symbols=args.symbol,
        use_active_state=args.active, use_watchlist_state=args.watchlist,
        use_void_state=args.void,
    )
    if not selected_syms:
        print("[ERROR] No symbols resolved")
        return

    if args.rotate:
        run_rotate(selected_syms, args.method, args.period, args.top_n, args.reb_period,
                   ttm=args.ttm, buy_threshold=args.buy_th,
                   spread_th=args.spread_th, slow_confirm=args.slow_confirm,
                   vol_cap=args.vol_cap)
    else:
        run_scan(selected_syms, args.method, args.period, args.buy_th, args.sell_th, {})


if __name__ == "__main__":
    main()
