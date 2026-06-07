"""
account_simulator.py — Simulate equity curve from trades.

Takes a list of trades + price series, applies them to an initial cash
account, returns equity curve + performance metrics.

Position bookkeeping delegates to utils.data_service.position_engine
so the same execute_trade() logic is shared with live signal filtering.

Usage:
    from skills.backtest.scripts.account_simulator import simulate_account

    equity_df, metrics = simulate_account(df, trades, initial_cash=100000, per_trade_cash=1000)
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import linregress

from utils.data_service.account_engine import AccountState, execute_trade


def simulate_account(
    df: pd.DataFrame,
    trades: list,
    initial_cash: float = 100000.0,
    backtest_years: int = 10,
    signal_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, dict, list]:
    """Simulate a cash/shares account from a trade list against a price series.

    fraction = percentage of available resources (0~1 only, no leverage).
      BUY:  amt = cash × fraction   (fraction of current cash on hand)
      SELL: sold = shares × fraction (fraction of current position)
      CLOSE: close entire position

    Trade log entries are self-contained: each trade includes post-trade
    state (cash_after, position_value, total_equity) and a snapshot of
    signal columns at trigger moment (signals dict).

    Args:
        df: DataFrame with 'date' and 'val' (price) columns. Must be sorted by date.
        trades: List of Trade objects (or dict-like with date, verb, fraction, label).
        initial_cash: Starting cash amount. Total available fund for this asset.
        backtest_years: How many years of history to simulate (from latest date).
        signal_columns: Column names to snapshot into trade's "signals" dict.
                        If None, only standard fields are recorded.

    Returns:
        (equity_df, metrics, trade_log)
        equity_df: DataFrame with date, val, cash, shares, strategy_equity, buyhold_value.
        metrics: Dict with strategy_ret, buyhold_ret, strategy_dd, buyhold_dd, etc.
        trade_log: List of executed trade dicts with post-trade state + signal snapshot.
    """
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

    # Determine backtest window
    end_dt = df["date"].max()
    start_dt = end_dt - pd.DateOffset(years=backtest_years)

    # Build trade lookup: date → list of trades
    trade_map: dict[str, list] = {}
    for t in trades:
        t_date = str(t.date)[:10] if hasattr(t, "date") else str(t["date"])[:10]
        trade_map.setdefault(t_date, []).append(t)

    # Simulate
    pos = AccountState(cash=initial_cash)
    cash_arr = []
    shares_arr = []
    equity_arr = []
    buyhold_arr = []
    trade_log = []

    # Pre-compute signal column values for fast row lookup
    signal_data: dict[str, list] = {}
    if signal_columns:
        for col in signal_columns:
            if col in df.columns:
                signal_data[col] = df[col].values

    def _snapshot_signals(idx: int) -> dict:
        """Snapshot signal columns at row index idx."""
        if not signal_columns:
            return {}
        snap = {}
        for col in signal_columns:
            if col in signal_data and idx < len(signal_data[col]):
                v = signal_data[col][idx]
                if isinstance(v, (int, float, np.floating, np.integer)) and not (isinstance(v, float) and np.isnan(v)):
                    snap[col] = float(v)
                elif isinstance(v, str) and v:
                    snap[col] = v
        return snap

    def _append_trade(entry: dict, idx: int):
        """Append trade with post-trade state + signal snapshot."""
        entry["cash_after"] = float(pos.cash)
        entry["position_value"] = float(pos.shares * price)
        entry["total_equity"] = float(pos.cash + pos.shares * price)
        entry["signals"] = _snapshot_signals(idx)
        trade_log.append(entry)

    n = len(df)
    for i in range(n):
        row = df.iloc[i]
        date_str = str(row["date"].date()) if hasattr(row["date"], "date") else str(row["date"])
        price = float(row["val"])
        dt = pd.to_datetime(row["date"])

        in_backtest = dt >= start_dt

        # Apply trades scheduled for this date
        if in_backtest and date_str in trade_map:
            for t in trade_map[date_str]:
                verb = t.verb if hasattr(t, "verb") else t["verb"]
                fraction = float(t.fraction if hasattr(t, "fraction") else t["fraction"])
                label = t.label if hasattr(t, "label") else t.get("label", "")

                new_pos, result = execute_trade(pos, verb, fraction, price, label)
                if result.executed:
                    pos = new_pos
                    _append_trade({
                        "type": verb, "desc": label,
                        "shares_delta": result.shares_delta, "amt_delta": result.amt_delta,
                        "date": date_str, "val": price,
                    }, i)

        equity = pos.cash + pos.shares * price
        buyhold = (price / df["val"].iloc[0]) * initial_cash if i > 0 else initial_cash

        equity_arr.append(float(equity))
        cash_arr.append(float(pos.cash))
        shares_arr.append(float(pos.shares))
        buyhold_arr.append(float(buyhold))

    # Attach to df
    df["strategy_equity"] = equity_arr
    df["_cash_array"] = cash_arr
    df["_shares_array"] = shares_arr
    df["buyhold_value"] = buyhold_arr

    # Compute metrics over backtest window
    mask = df["date"] >= start_dt
    sim_df = df[mask].reset_index(drop=True)

    if len(sim_df) < 2:
        metrics = {
            "Strategy_Ret": 0.0, "Strategy_DD": 0.0,
            "Strat_Vol": 0.0, "Strat_Sharpe": 0.0,
            "BuyHold_Ret": 0.0, "BuyHold_DD": 0.0,
            "BuyHold_Vol": 0.0, "BuyHold_Sharpe": 0.0,
        }
        return df, metrics, trade_log

    sim_eq = sim_df["strategy_equity"]
    sim_bh = sim_df["buyhold_value"]

    # Set date index for proper day-count in metrics
    date_idx = pd.DatetimeIndex(pd.to_datetime(sim_df["date"]))
    sim_eq.index = date_idx
    sim_bh.index = date_idx

    # Strategy metrics
    s_ret = float(sim_eq.iloc[-1] / sim_eq.iloc[0]) - 1
    s_cagr, s_vol, s_shp = _metrics_from_series(sim_eq)
    _, _, s_dd = _maxdd(sim_eq)

    # BuyHold metrics
    b_ret = float(sim_bh.iloc[-1] / sim_bh.iloc[0]) - 1
    b_cagr, b_vol, b_shp = _metrics_from_series(sim_bh)
    _, _, b_dd = _maxdd(sim_bh)

    bt_start = str(sim_df["date"].iloc[0].date()) if not sim_df.empty else "?"
    bt_end = str(sim_df["date"].iloc[-1].date()) if not sim_df.empty else "?"
    bt_years = round((sim_df["date"].iloc[-1] - sim_df["date"].iloc[0]).days / 365.25, 1) if len(sim_df) > 1 else 0.0

    # Long-term R²
    y_all = np.log(df["val"].values)
    x_all = np.arange(len(y_all))
    _, inter, r_val, _, _ = linregress(x_all, y_all)

    metrics = {
        "Strategy_Ret": s_ret, "Strategy_DD": s_dd,
        "Strat_Vol": s_vol, "Strat_Sharpe": s_shp,
        "BuyHold_Ret": b_ret, "BuyHold_DD": b_dd,
        "BuyHold_Vol": b_vol, "BuyHold_Sharpe": b_shp,
        "Stats_Info": f"R2: {r_val**2:.2f}  σ_Glob: {float(np.std(y_all - (inter + r_val * x_all))):.2f}",
        "Backtest_Period_Start": bt_start,
        "Backtest_Period_End": bt_end,
        "Backtest_Years": bt_years,
    }

    return df, metrics, trade_log


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _maxdd(series: pd.Series) -> tuple:
    """Return (max_dd_fraction, peak_value, trough_value)."""
    if len(series) == 0:
        return 0.0, 0.0, 0.0
    roll_max = series.cummax()
    dd = (series - roll_max) / roll_max
    return float(dd.iloc[-1]), float(roll_max.iloc[-1]), float(dd.min())


def _metrics_from_series(series: pd.Series) -> tuple:
    """Return (cagr, vol, sharpe) from an equity curve series."""
    if len(series) < 2:
        return 0.0, 0.0, 0.0
    days = (series.index[-1] - series.index[0]).days
    if days < 1 or series.iloc[0] <= 0:
        return 0.0, 0.0, 0.0
    cagr = ((series.iloc[-1] / series.iloc[0]) ** (365.25 / max(days, 1))) - 1
    dr = series.pct_change().dropna()
    vol = dr.std() * np.sqrt(252)
    sharpe = (dr.mean() / dr.std() * np.sqrt(252)) if dr.std() != 0 else 0.0
    return float(cagr), float(vol), float(sharpe)
