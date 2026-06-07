"""Historical allocation analysis: cumulative return chart + metrics table + rebalancing comparison.

Generates:
- {date}_{plan}_{region}_review.png  (chart)
- Review section markdown

Design decisions:
- Chart shows BOTH combined allocation line AND individual asset lines
- Lookback: max(500 days, shortest individual asset history), capped at 2520 days
- Risk-free rate: configurable per region (not hardcoded)
- Table always uses vertical layout (assets as rows, metrics as columns)
- Falls back to yfinance live fetch if asset not found in 3_processed
- Rebalancing comparison: drift > 10% triggers rebalance; no reallocation costs
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Region-level risk-free rate configuration (annualised, decimal)
# ---------------------------------------------------------------------------
RISK_FREE_RATES: dict[str, float] = {
    "CN": 0.02,   # China: 2%
    "US": 0.04,   # United States: 4%
}

# Rebalancing drift threshold (decimal, e.g. 0.10 = 10%)
REBALANCE_DRIFT_THRESHOLD = 0.10


def _skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[3]


# Add paths for Plan loading (dao + config)
_workspace = str(_workspace_root())
if _workspace not in sys.path:
    sys.path.insert(0, _workspace)
sys.path.insert(0, str(_workspace_root() / "dao"))
sys.path.insert(0, str(_workspace_root() / "config"))

from dao.models import Plan

# Resolve locally to avoid config.py shadowing (Lingma etc.)
PLANS_DIR = _workspace_root() / "config" / "plans"


def default_outputs_dir() -> Path:
    return _workspace_root() / "logs" / "review"


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _region_slug(region: str) -> str:
    return str(region or "ALL").strip().lower()


def _processed_csv_path(root: Path, symbol: str, region: str) -> Path:
    return root / "knowledge" / _region_slug(region) / "3_processed" / f"{symbol}.csv"


# ---------------------------------------------------------------------------
# Live data fallback — delegates to DailyUpdateCN / DailyUpdateUS
# ---------------------------------------------------------------------------

def _fetch_live_total_return(symbol: str, region: str, trading_days: int) -> pd.Series | None:
    """Fetch historical total-return series using the existing DailyUpdate skill.

    Uses DailyUpdateCN._fetch_from_api for CN assets (akshare) and
    DailyUpdateUS._fetch_from_api for US assets (yfinance).
    """
    region_upper = region.upper()
    base_path = str(_workspace_root() / "knowledge" / _region_slug(region))

    try:
        if region_upper == "CN":
            sys.path.insert(0, str(_workspace_root() / "skills" / "data-daily-update" / "scripts"))
            from cn_daily import DailyUpdateCN

            updater = DailyUpdateCN(base_path=base_path)
            # Detect asset type: check asset-master first, fall back to prefix
            asset_type = None
            try:
                from dao.asset_dao import AssetManifest
                _a = AssetManifest().get(symbol)
                if _a:
                    _at = str(_a.asset_type)
                    if "CN_OTC" in _at:
                        asset_type = "CN_OTC"
                    elif "CN_INDEX" in _at:
                        asset_type = "CN_INDEX"
                    else:
                        asset_type = "CN_ETF"
            except Exception:
                pass
            if asset_type is None:
                s = symbol.strip()
                if s.startswith(("00", "007", "012")):
                    asset_type = "CN_OTC"
                elif s.startswith(("000", "399")):
                    asset_type = "CN_INDEX"
                elif any(s.startswith(p) for p in ["159", "51", "56", "58"]):
                    asset_type = "CN_ETF"
                else:
                    # 59xxx, 67xxx etc. are OTC fund codes
                    asset_type = "CN_OTC"
            # Go back far enough to cover trading_days + buffer
            calendar_days = min(int(trading_days * 1.5) + 60, 3650)
            start_date = (date.today() - timedelta(days=calendar_days)).strftime("%Y-%m-%d")

            df = updater._fetch_from_api(symbol, asset_type, start_date)
            if df.empty:
                return None

            prices = df["price"].dropna()
            if len(prices) < 30:
                return None

            # CN_OTC (NAV) is already total-return; ETF/INDEX need rebasing
            base = prices.iloc[0]
            total_return = (prices / base) * 100

            total_return.index = pd.to_datetime(df["date"].iloc[:len(total_return)])
            return total_return.sort_index()

        else:
            sys.path.insert(0, str(_workspace_root() / "skills" / "data-daily-update" / "scripts"))
            from us_daily import DailyUpdateUS

            updater = DailyUpdateUS(base_path=base_path)
            calendar_days = min(int(trading_days * 1.6) + 60, 3650)
            start_date = (date.today() - timedelta(days=calendar_days)).strftime("%Y-%m-%d")

            df = updater._fetch_from_yfinance(symbol, start_date)
            if df.empty:
                return None

            prices = df["price"].dropna()
            if len(prices) < 30:
                return None

            base = prices.iloc[0]
            total_return = (prices / base) * 100
            total_return.index = pd.to_datetime(df["date"].iloc[:len(total_return)])
            return total_return.sort_index()

    except Exception as exc:
        print(f"  ⚠ {symbol} ({region_upper}): daily-update fetch failed — {exc}", file=sys.stderr)
        return None


def load_total_return_series(csv_path: Path, lookback_days: int | None = None) -> pd.Series | None:
    """Load total_return series from a processed CSV.

    The CSV has columns: date, total_return
    Values are indexed from base 1000.

    Returns:
        pd.Series with date index and total_return values (not returns).
    """
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path, parse_dates=["date"])
    if "total_return" not in df.columns:
        return None
    series = pd.to_numeric(df["total_return"], errors="coerce").dropna()
    if series.empty:
        return None
    series = series.set_axis(pd.to_datetime(df["date"].iloc[:len(series)]))
    series = series.sort_index()
    if lookback_days is not None and len(series) > lookback_days:
        series = series.iloc[-lookback_days:]
    return series


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    non_negative = {s: max(0.0, float(w)) for s, w in weights.items()}
    total = sum(non_negative.values())
    if total <= 0:
        return {}
    return {s: w / total for s, w in non_negative.items()}


def compute_allocation_series(
    asset_series: dict[str, pd.Series],
    weights: dict[str, float],
) -> pd.Series:
    """Build a weighted allocation total-return series from individual asset series.

    Each asset series is already indexed from 1000.
    Allocation value = sum(weight_i * asset_value_i)
    """
    # Align all series to a common date index
    common_index: pd.DatetimeIndex | None = None
    for s in asset_series.values():
        if common_index is None:
            common_index = s.index
        else:
            common_index = common_index.intersection(s.index)

    if common_index is None or len(common_index) == 0:
        raise ValueError("No common dates across assets")

    normalized = {}
    for symbol, series in asset_series.items():
        aligned = series.reindex(common_index)
        w = weights.get(symbol, 0.0)
        normalized[symbol] = aligned * w

    combined = pd.concat(normalized.values(), axis=1).sum(axis=1)
    return combined


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def total_return(series: pd.Series) -> float:
    """Total return over the period: (final / initial - 1)."""
    if len(series) < 2:
        return 0.0
    return (series.iloc[-1] / series.iloc[0]) - 1.0


def annualized_return(series: pd.Series, risk_free_rate: float = 0.04) -> float:
    """Annualised return using geometric mean, scaled to 252 trading days."""
    if len(series) < 2:
        return 0.0
    tr = total_return(series)
    years = len(series) / 252.0
    if years <= 0:
        return 0.0
    return (1 + tr) ** (1 / years) - 1


def max_drawdown(series: pd.Series) -> float:
    """Maximum drawdown as a positive percentage (e.g. 0.20 = 20%)."""
    if len(series) < 2:
        return 0.0
    peak = series.expanding().max()
    drawdown = (series - peak) / peak
    return abs(drawdown.min())


def daily_returns(series: pd.Series) -> pd.Series:
    """Daily percentage returns from total-return series."""
    return series.pct_change().dropna()


def sharpe_ratio(series: pd.Series, risk_free_rate: float = 0.04) -> float:
    """Annualised Sharpe ratio: (ann_return - rf) / ann_vol * sqrt(252)."""
    if len(series) < 2:
        return 0.0
    ann_ret = annualized_return(series, risk_free_rate)
    daily_ret = daily_returns(series)
    if daily_ret.std() == 0:
        return 0.0
    ann_vol = daily_ret.std() * math.sqrt(252)
    return (ann_ret - risk_free_rate) / ann_vol


def sortino_ratio(series: pd.Series, risk_free_rate: float = 0.04) -> float:
    """Annualised Sortino ratio: (ann_return - rf) / downside_dev * sqrt(252)."""
    if len(series) < 2:
        return 0.0
    ann_ret = annualized_return(series, risk_free_rate)
    daily_ret = daily_returns(series)
    downside = daily_ret[daily_ret < 0]
    if len(downside) == 0 or downside.std() == 0:
        return 0.0
    downside_dev = downside.std() * math.sqrt(252)
    return (ann_ret - risk_free_rate) / downside_dev


def calmar_ratio(series: pd.Series, risk_free_rate: float = 0.04) -> float:
    """Calmar ratio: annualized_return / max_drawdown."""
    if len(series) < 2:
        return 0.0
    ann_ret = annualized_return(series, risk_free_rate)
    mdd = max_drawdown(series)
    if mdd == 0:
        return 0.0
    return ann_ret / mdd


def annualized_volatility(series: pd.Series) -> float:
    """Annualised volatility: daily std * sqrt(252)."""
    if len(series) < 2:
        return 0.0
    daily_ret = daily_returns(series)
    if daily_ret.std() == 0:
        return 0.0
    return float(daily_ret.std() * math.sqrt(252))


def best_worst_day(series: pd.Series) -> tuple[float, float]:
    """Best and worst single-day return (as decimal)."""
    if len(series) < 2:
        return 0.0, 0.0
    daily_ret = daily_returns(series)
    if daily_ret.empty:
        return 0.0, 0.0
    return float(daily_ret.max()), float(daily_ret.min())


def compute_metrics(series: pd.Series, risk_free_rate: float = 0.04) -> dict[str, Any]:
    """Compute all allocation metrics."""
    tr = total_return(series)
    ann = annualized_return(series, risk_free_rate)
    mdd = max_drawdown(series)
    best, worst = best_worst_day(series)
    return {
        "total_return": tr,
        "annualized_return": ann,
        "volatility": annualized_volatility(series),
        "max_drawdown": mdd,
        "sharpe_ratio": sharpe_ratio(series, risk_free_rate),
        "sortino_ratio": sortino_ratio(series, risk_free_rate),
        "calmar_ratio": calmar_ratio(series, risk_free_rate),
        "best_day": best,
        "worst_day": worst,
    }


# ---------------------------------------------------------------------------
# Rebalancing simulation
# ---------------------------------------------------------------------------

def _simulate_allocation(
    asset_series: dict[str, pd.Series],
    weights: dict[str, float],
    drift_threshold: float,
) -> tuple[pd.Series, dict[str, float], int]:
    """Simulate allocation with drift-based rebalancing.

    On each date:
      1. Grow each asset allocation by its daily return
      2. Compute each asset's current weight (value / total)
      3. If any |weight - target| > drift_threshold → rebalance back to target

    Args:
        asset_series: dict of symbol -> total-return series (base 1000)
        weights: target weights per symbol
        drift_threshold: rebalance trigger threshold (decimal); pass 999 to disable

    Returns:
        (allocation_series_normalized, final_weights, rebalance_count)
        allocation_series_normalized: base 100, rebalance_count: 0 if disabled
    """
    symbols = list(weights.keys())

    # Align all series to a common date index
    common_index: pd.DatetimeIndex | None = None
    for s in asset_series.values():
        if common_index is None:
            common_index = s.index
        else:
            common_index = common_index.intersection(s.index)

    if common_index is None or len(common_index) == 0:
        raise ValueError("No common dates across assets")

    # Reindex all asset series to the common index
    aligned = {sym: asset_series[sym].reindex(common_index) for sym in symbols}

    # Initialise: 1000 units total, allocated per target weights
    total_value = 1000.0
    current_weights = dict(weights)
    rebalance_count = 0

    # Track allocation value over time
    allocation_values: list[float] = []

    for i, _ in enumerate(common_index):
        if i == 0:
            allocation_values.append(total_value)
            continue

        # Grow each asset by its daily return
        asset_values: dict[str, float] = {}
        for sym in symbols:
            w = current_weights.get(sym, 0.0)
            prev_asset_val = total_value * w
            daily_ret = aligned[sym].iloc[i] / aligned[sym].iloc[i - 1] - 1.0
            asset_values[sym] = prev_asset_val * (1.0 + daily_ret)

        total_value = sum(asset_values.values())

        # Update current weights
        if total_value > 0:
            current_weights = {sym: asset_values.get(sym, 0.0) / total_value for sym in symbols}

        # Check drift — rebalance if needed
        if drift_threshold < 999:
            needs_rebalance = any(
                abs(current_weights.get(sym, 0.0) - weights.get(sym, 0.0)) > drift_threshold
                for sym in symbols
            )
            if needs_rebalance:
                rebalance_count += 1
                current_weights = dict(weights)

        allocation_values.append(total_value)

    # Normalise to base 100
    series = pd.Series(allocation_values, index=common_index)
    base = series.iloc[0]
    if base == 0:
        base = 1000.0
    series = series / base * 100

    return series, dict(current_weights), rebalance_count


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

DEFAULT_MIN_LOOKBACK = 500
DEFAULT_MAX_LOOKBACK = 2520
DEFAULT_LOOKBACK = 500


def run_review(
    root: Path | None = None,
    plan_payload: dict[str, Any | None] | None = None,
    lookback_days: int = DEFAULT_LOOKBACK,
    risk_free_rate: float | None = None,
    output_dir: Path | None = None,
    drift_threshold: float = REBALANCE_DRIFT_THRESHOLD,
) -> dict[str, Any]:
    """Generate plan review chart + metrics + rebalancing comparison.

    Args:
        root: workspace root (defaults to auto-detect)
        plan_payload: dict with keys:
            - name: plan name (e.g. "cn_hb")
            - region: "CN" or "US"
            - positions: list of {"symbol": str, "weight": float}
        lookback_days: default lookback window (default 500)
        risk_free_rate: override risk-free rate (if None, uses region default)
        output_dir: output directory (defaults to logs/review/)
        drift_threshold: rebalance trigger threshold (decimal, e.g. 0.08 for 8%%)

    Returns:
        dict with keys: chart_path, review_section, asset_metrics, allocation_metrics,
                        rebalance_metrics, no_rebalance_metrics, final_weights
    """
    root = root or _workspace_root()
    output_dir = output_dir or default_outputs_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not plan_payload:
        raise ValueError("plan_payload is required")

    plan_name = str(plan_payload.get("name") or plan_payload.get("plan") or "unknown")
    region = str(plan_payload.get("region") or "US").upper()
    positions: list[dict[str, Any]] = plan_payload.get("positions", [])
    plan_weights: dict[str, float] = plan_payload.get("_plan_weights", {})

    if not positions and not plan_weights:
        raise ValueError(f"No allocation assets or weights provided for plan '{plan_name}'")

    # Effective lookback: max(lookback_days, shortest asset history, 500)
    asset_series: dict[str, pd.Series] = {}
    asset_weights: dict[str, float] = {}
    missing_assets: list[str] = []
    min_history = DEFAULT_MIN_LOOKBACK

    # Build iteration list: use positions + plan_weights merged
    if positions:
        symbols_to_load = []
        for pos in positions:
            sym = str(pos["symbol"])
            # Use target_weight from plan config if available, else fallback to position weight
            wt = plan_weights.get(sym, float(pos.get("target_weight", 0.0) or pos.get("weight", 0.0)))
            symbols_to_load.append((sym, wt))
    else:
        symbols_to_load = [(symbol, weight) for symbol, weight in plan_weights.items() if weight > 0]

    for symbol, weight in symbols_to_load:
        if weight <= 0:
            continue
        # 1. Try processed CSV
        csv_path = _processed_csv_path(root, symbol, region)
        series = load_total_return_series(csv_path) if csv_path.exists() else None
        # 2. Fallback to region-aware live fetch
        if series is None or len(series) < 2:
            print(f"  → {symbol}: not in 3_processed, fetching via live API...")
            series = _fetch_live_total_return(symbol, region=region, trading_days=lookback_days)
        if series is None or len(series) < 2:
            missing_assets.append(symbol)
            continue
        asset_series[symbol] = series
        asset_weights[symbol] = weight
        min_history = max(min_history, len(series))

    if missing_assets:
        print(f"  ⚠ Skipped assets (no data available): {', '.join(missing_assets)}", file=sys.stderr)

    if not asset_series:
        raise RuntimeError(f"No valid asset data found for plan '{plan_name}' region {region}")

    # Enforce minimum lookback = max(500, shortest history)
    effective_lookback = min(int(min_history), DEFAULT_MAX_LOOKBACK)

    # Reload with effective lookback
    trimmed_asset_series: dict[str, pd.Series] = {}
    for symbol, series in asset_series.items():
        if len(series) > effective_lookback:
            trimmed_asset_series[symbol] = series.iloc[-effective_lookback:]
        else:
            trimmed_asset_series[symbol] = series

    # Position combined series
    weights = _normalize_weights(asset_weights)
    allocation_series = compute_allocation_series(trimmed_asset_series, weights)

    # Risk-free rate
    rf = risk_free_rate if risk_free_rate is not None else RISK_FREE_RATES.get(region, 0.04)

    # Compute metrics
    allocation_metrics = compute_metrics(allocation_series, rf)
    asset_metrics: dict[str, dict[str, Any]] = {}
    for symbol, series in trimmed_asset_series.items():
        asset_metrics[symbol] = compute_metrics(series, rf)

    # ---- Rebalancing simulation ----
    rebalance_series, rebalance_final_weights, rebalance_count = _simulate_allocation(
        trimmed_asset_series, weights, drift_threshold=drift_threshold
    )
    no_rebalance_series, no_rebalance_final_weights, _ = _simulate_allocation(
        trimmed_asset_series, weights, drift_threshold=999.0
    )

    rebalance_metrics = compute_metrics(rebalance_series, rf)
    rebalance_metrics["rebalance_count"] = rebalance_count
    no_rebalance_metrics = compute_metrics(no_rebalance_series, rf)

    # Final weights (end of series)
    end_date = trimmed_asset_series[list(trimmed_asset_series.keys())[0]].index[-1].strftime("%Y-%m-%d")
    final_weights: dict[str, dict[str, float]] = {}
    for sym in weights:
        final_weights[sym] = {
            "target": weights[sym],
            "no_rebalance": no_rebalance_final_weights.get(sym, weights[sym]),
            "rebalance": rebalance_final_weights.get(sym, weights[sym]),
        }

    # ---- Generate chart ----
    chart_path = _generate_chart(
        output_dir=output_dir,
        plan_name=plan_name,
        region=region,
        allocation_series=allocation_series,
        asset_series=trimmed_asset_series,
        allocation_metrics=allocation_metrics,
        asset_metrics=asset_metrics,
        rf=rf,
    )

    # ---- Build review section ----
    review_lines = _build_review_section(
        plan_name=plan_name,
        region=region,
        lookback=len(allocation_series),
        rf=rf,
        allocation_metrics=allocation_metrics,
        asset_series=trimmed_asset_series,
        asset_metrics=asset_metrics,
        asset_weights=weights,
        no_rebalance_metrics=no_rebalance_metrics,
        rebalance_metrics=rebalance_metrics,
        final_weights=final_weights,
        end_date=end_date,
        drift_threshold=drift_threshold,
    )

    return {
        "chart_path": str(chart_path),
        "review_section": "\n".join(review_lines),
        "allocation_metrics": allocation_metrics,
        "asset_metrics": asset_metrics,
        "effective_lookback": effective_lookback,
        "risk_free_rate": rf,
        "weights": weights,
        "no_rebalance_metrics": no_rebalance_metrics,
        "rebalance_metrics": rebalance_metrics,
        "final_weights": final_weights,
    }


def _generate_chart(
    output_dir: Path,
    plan_name: str,
    region: str,
    allocation_series: pd.Series,
    asset_series: dict[str, pd.Series],
    allocation_metrics: dict[str, Any],
    asset_metrics: dict[str, Any],
    rf: float,
) -> Path:
    """Generate the cumulative return chart with metrics table."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
    except ImportError:
        raise RuntimeError("matplotlib is required for chart generation. Install with: pip install matplotlib")

    run_date = date.today().strftime("%Y-%m-%d")

    # Normalise series to start at 100 for readability
    def normalise(s: pd.Series) -> pd.Series:
        if s.empty or s.iloc[0] == 0:
            return s
        return s / s.iloc[0] * 100

    allocation_norm = normalise(allocation_series)
    asset_norms = {sym: normalise(s) for sym, s in asset_series.items()}

    fig, (ax_chart, ax_table) = plt.subplots(
        nrows=2,
        figsize=(14, 9),
        gridspec_kw={"height_ratios": [3, 1]},
        facecolor="#ffffff",
    )
    fig.patch.set_facecolor("#ffffff")
    ax_chart.set_facecolor("#fafafa")
    ax_table.set_facecolor("#ffffff")

    # Color palette for assets
    palette = [
        "#0969da", "#1a7f37", "#9a6700", "#cf222e",
        "#8250df", "#0550ae", "#116329", "#7d4e00",
    ]
    asset_colors = {sym: palette[i % len(palette)] for i, sym in enumerate(asset_series)}

    # Plot individual assets (thin, muted)
    for i, (symbol, series_norm) in enumerate(asset_norms.items()):
        ax_chart.plot(
            series_norm.index, series_norm.values,
            color=asset_colors[symbol], alpha=0.5, linewidth=1.2,
            label=f"{symbol}",
        )

    # Plot allocation (bold, prominent)
    ax_chart.plot(
        allocation_norm.index, allocation_norm.values,
        color="#24292f", linewidth=2.4,
        label="Position",
        zorder=10,
    )

    # Reference line at 100
    ax_chart.axhline(100, color="#999999", linewidth=0.8, linestyle="--", alpha=0.6)

    ax_chart.set_title(
        f"{plan_name} ({region}) — Backtest\n"
        f"Risk-free rate: {rf:.1%}  |  Lookback: {len(allocation_norm)} days",
        color="#24292f", fontsize=13, pad=12,
    )
    ax_chart.tick_params(colors="#57606a", labelsize=8)
    ax_chart.yaxis.set_major_formatter(mticker.PercentFormatter())
    ax_chart.set_ylabel("Cumulative Return (base=100)", color="#57606a", fontsize=9)
    ax_chart.set_xlabel("", color="#57606a")
    ax_chart.legend(
        loc="upper left", fontsize=7.5,
        facecolor="#f6f8fa", edgecolor="#d0d7de",
        labelcolor="#24292f", ncol=2,
    )
    ax_chart.grid(True, color="#d0d7de", linewidth=0.6)
    for spine in ax_chart.spines.values():
        spine.set_color("#d0d7de")

    # ---- Metrics table ----
    ax_table.axis("off")

    def fmt(v: float, as_pct: bool = False, as_ratio: bool = False) -> str:
        if as_pct:
            return f"{v:.2%}"
        if as_ratio:
            return f"{v:.2f}"
        return f"{v:.4f}"

    METRIC_KEY = {
        "total return": "total_return",
        "ann. return": "annualized_return",
        "volatility": "volatility",
        "max drawdown": "max_drawdown",
        "sharpe ratio": "sharpe_ratio",
        "sortino ratio": "sortino_ratio",
        "calmar ratio": "calmar_ratio",
        "worst day": "worst_day",
    }
    METRICS = [
        ("Total Return",  True,  False),
        ("Ann. Return",   True,  False),
        ("Volatility",    True,  False),
        ("Max Drawdown",  True,  False),
        ("Sharpe Ratio",  False, True),
        ("Sortino Ratio", False, True),
        ("Calmar Ratio",  False, True),
        ("Worst Day",     True,  False),
    ]

    symbols = ["Allocation"] + list(asset_series.keys())

    table_data = []
    for sym in symbols:
        row = [sym]
        for label, as_pct, as_ratio in METRICS:
            k = METRIC_KEY.get(label.lower(), label.lower().replace(" ", "_").replace(".", "").replace("-", "_"))
            if sym == "Allocation":
                v = float(allocation_metrics.get(k, 0))
            else:
                v = float(asset_metrics.get(sym, {}).get(k, 0))
            row.append(fmt(v, as_pct, as_ratio))
        table_data.append(row)
    col_labels = ["Asset"] + [m[0] for m in METRICS]
    table = ax_table.table(
        cellText=table_data,
        colLabels=col_labels,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7)
    table.scale(1, 1.1)
    cell_h = 0.09

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#d0d7de")
        if row == 0:
            cell.set_facecolor("#24292f")
            cell.set_text_props(color="#ffffff", fontweight="bold")
        else:
            cell.set_facecolor("#f6f8fa" if row % 2 == 0 else "#ffffff")
            cell.set_text_props(color="#24292f")
        cell.set_height(cell_h)

    plt.tight_layout(pad=1.5)

    out_path = output_dir / f"{run_date}_{plan_name}_{_region_slug(region)}_review.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    return out_path


def _build_review_section(
    plan_name: str,
    region: str,
    lookback: int,
    rf: float,
    allocation_metrics: dict[str, Any],
    asset_series: dict[str, pd.Series],
    asset_metrics: dict[str, Any],
    asset_weights: dict[str, float],
    no_rebalance_metrics: dict[str, Any],
    rebalance_metrics: dict[str, Any],
    final_weights: dict[str, dict[str, float]],
    end_date: str,
    drift_threshold: float = REBALANCE_DRIFT_THRESHOLD,
) -> list[str]:
    def fmt_pct(v: float) -> str:
        return f"{v:.2%}"

    def fmt_ratio(v: float) -> str:
        return f"{v:.2f}"

    lines = [
        f"## Backtest — {plan_name} ({region})",
        "",
        f"- Lookback: {lookback} trading days",
        f"- Risk-free rate: {rf:.1%} (region default for {region})",
        "",
        "### Allocation Metrics",
        "",
        "| Metric | Value |",
        "|---|",
        f"| Total Return | {fmt_pct(allocation_metrics['total_return'])} |",
        f"| Annualized Return | {fmt_pct(allocation_metrics['annualized_return'])} |",
        f"| Volatility | {fmt_pct(allocation_metrics['volatility'])} |",
        f"| Max Drawdown | {fmt_pct(allocation_metrics['max_drawdown'])} |",
        f"| Sharpe Ratio | {fmt_ratio(allocation_metrics['sharpe_ratio'])} |",
        f"| Sortino Ratio | {fmt_ratio(allocation_metrics['sortino_ratio'])} |",
        f"| Calmar Ratio | {fmt_ratio(allocation_metrics['calmar_ratio'])} |",
        f"| Worst Day | {fmt_pct(allocation_metrics['worst_day'])} |",
        "",
        "### Per-Asset Metrics",
        "",
        "| Symbol | Weight | Total Return | Ann. Return | Volatility | Max DD | Sharpe | Sortino | Calmar | Worst Day |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for symbol in sorted(asset_series.keys()):
        am = asset_metrics.get(symbol, {})
        w = asset_weights.get(symbol, 0.0)
        lines.append(
            f"| {symbol} | {fmt_pct(w)} | "
            f"{fmt_pct(am.get('total_return', 0))} | "
            f"{fmt_pct(am.get('annualized_return', 0))} | "
            f"{fmt_pct(am.get('volatility', 0))} | "
            f"{fmt_pct(am.get('max_drawdown', 0))} | "
            f"{fmt_ratio(am.get('sharpe_ratio', 0))} | "
            f"{fmt_ratio(am.get('sortino_ratio', 0))} | "
            f"{fmt_ratio(am.get('calmar_ratio', 0))} | "
            f"{fmt_pct(am.get('worst_day', 0))} |"
        )

    # ---- Rebalancing Comparison ----
    lines.extend([
        "",
        f"### Rebalancing Comparison — drift>{fmt_pct(drift_threshold)}",
        "",
        "| Metric | No Rebalance | Rebalance |",
        "|---|---:|---:|",
        f"| Total Return | {fmt_pct(no_rebalance_metrics['total_return'])} | {fmt_pct(rebalance_metrics['total_return'])} |",
        f"| Max Drawdown | {fmt_pct(no_rebalance_metrics['max_drawdown'])} | {fmt_pct(rebalance_metrics['max_drawdown'])} |",
        f"| Sharpe Ratio | {fmt_ratio(no_rebalance_metrics['sharpe_ratio'])} | {fmt_ratio(rebalance_metrics['sharpe_ratio'])} |",
        f"| Sortino Ratio | {fmt_ratio(no_rebalance_metrics['sortino_ratio'])} | {fmt_ratio(rebalance_metrics['sortino_ratio'])} |",
        f"| Calmar Ratio | {fmt_ratio(no_rebalance_metrics['calmar_ratio'])} | {fmt_ratio(rebalance_metrics['calmar_ratio'])} |",
        f"| Rebalances | — | {rebalance_metrics.get('rebalance_count', 0)} |",
    ])

    # ---- Final Weights ----
    lines.extend([
        "",
        f"### Final Weights (end of {end_date})",
        "",
        "| Symbol | Target | No Rebalance | Rebalance |",
        "|---|---:|---:|---:|",
    ])
    for symbol in sorted(final_weights.keys()):
        fw = final_weights[symbol]
        lines.append(
            f"| {symbol} | {fmt_pct(fw['target'])} | "
            f"{fmt_pct(fw['no_rebalance'])} | {fmt_pct(fw['rebalance'])} |"
        )

    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Plan review: chart + metrics + rebalancing comparison")
    parser.add_argument("--plan", required=True, help="Plan name, e.g. cn_hb, us_hb")
    parser.add_argument("--plan-version", type=int, default=None, help="Plan version (default: latest)")
    parser.add_argument("--region", required=True, help="Region: CN or US")
    parser.add_argument("--holdings", default=None, help="Path to holdings JSON (optional; plan weights used if not provided)")
    parser.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK, help="Lookback days (default 500)")
    parser.add_argument("--rf", type=float, default=None, help="Risk-free rate override (e.g. 0.03 for 3%%)")
    parser.add_argument("--drift", type=float, default=None,
                        help="Rebalance trigger threshold (decimal, e.g. 0.08 for 8%%, default 0.10)")
    parser.add_argument("--output-dir", default=None, help="Output directory")
    args = parser.parse_args()

    root = _workspace_root()

    # Load plan for weights (always needed — holdings lacks weights)
    version = args.plan_version
    if version is None:
        plan_dir = PLANS_DIR / args.plan
        if not plan_dir.exists():
            sys.exit(f"Error: plan '{args.plan}' not found.")
        versions = sorted([int(f.stem[1:]) for f in plan_dir.glob("v*.json") if f.stem[1:].isdigit()])
        if not versions:
            sys.exit(f"Error: no versions found for plan '{args.plan}'.")
        version = max(versions)

    try:
        plan = Plan.load(args.plan, version, PLANS_DIR)
    except FileNotFoundError:
        sys.exit(f"Error: plan '{args.plan}' v{version} not found.")

    # Extract weights from plan's all_assets
    plan_weights = {asset.symbol: asset.target_weight for asset in plan.all_assets}

    # Use plan assets (no holdings JSON needed — review doesn't need positions)
    positions = [
        {"symbol": asset.symbol, "target_weight": asset.target_weight}
        for asset in plan.all_assets
    ]

    plan_payload = {
        "name": args.plan,
        "region": args.region,
        "positions": positions,
        "_plan_weights": plan_weights,
    }

    result = run_review(
        root=root,
        plan_payload=plan_payload,
        lookback_days=args.lookback,
        risk_free_rate=args.rf,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        drift_threshold=args.drift if args.drift is not None else REBALANCE_DRIFT_THRESHOLD,
    )

    print(f"Chart: {result['chart_path']}")
    print("\n=== Review Section ===")
    print(result["review_section"])
