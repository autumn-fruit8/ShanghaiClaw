#!/usr/bin/env python3
"""
draw_log_chart.py - Semi-log total-return chart with CAGR trend line & drawdown panel.

Data resolution (3-tier cascade):
  Tier 1 - Active state:    knowledge/{region}/3_processed/{symbol}.csv
  Tier 2 - Watchlist/Void:  scan adhoc/ for latest run → adhoc/{run}/knowledge/.../{symbol}.csv
  Tier 3 - Unknown symbol:  live fetch via DailyUpdateUS/DailyUpdateCN

Usage:
  python3 skills/logarithm/scripts/draw_log_chart.py --symbol 159263
  python3 skills/logarithm/scripts/draw_log_chart.py --symbol XLV --years 5
  python3 skills/logarithm/scripts/draw_log_chart.py --symbol TLT --all-history
  python3 skills/logarithm/scripts/draw_log_chart.py --symbol SPYM,TLT,GLDM
  python3 skills/logarithm/scripts/draw_log_chart.py --symbol 000218 --region cn --years 3
  python3 skills/logarithm/scripts/draw_log_chart.py --symbol 003376 --rolling
  python3 skills/logarithm/scripts/draw_log_chart.py --symbol SPY --rolling 3
  python3 skills/logarithm/scripts/draw_log_chart.py --plan cn_bond
  python3 skills/logarithm/scripts/draw_log_chart.py --plan cn_hb --rolling
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
from matplotlib.ticker import FuncFormatter

# ── CJK Font Setup ──────────────────────────────────────────────────────────
_CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
]
for _fp in _CJK_FONT_CANDIDATES:
    if os.path.exists(_fp):
        _cjk_prop = fm.FontProperties(fname=_fp)
        plt.rcParams["font.family"] = _cjk_prop.get_name()
        fm.fontManager.addfont(_fp)
        break
else:
    # Try to find any CJK font via fc-list
    import subprocess
    try:
        _result = subprocess.run(
            ["fc-list", ":lang=zh", "file"],
            capture_output=True, text=True, timeout=5,
        )
        for _line in _result.stdout.strip().split("\n"):
            _fp = _line.split(":")[0].strip()
            if _fp and os.path.exists(_fp):
                fm.fontManager.addfont(_fp)
                _cjk_prop = fm.FontProperties(fname=_fp)
                plt.rcParams["font.family"] = _cjk_prop.get_name()
                break
    except Exception:
        pass
# Fallback: allow DejaVu with missing glyphs
plt.rcParams["axes.unicode_minus"] = False

# ── Workspace root ──────────────────────────────────────────────────────────
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WORKSPACE_ROOT))

# ── Imports that need workspace root on sys.path ────────────────────────────
from dao.state_dao import load_state_records
from utils.data_service.data_resolver import resolve_price_data
from utils.symbols.state_resolver import detect_region, resolve_symbols_from_args, load_state_symbols

# ── Rolling trend utility ───────────────────────────────────────────────────
from utils.indicators.trend_utils import calc_rolling_trend_curve

STATE_DIR = WORKSPACE_ROOT / "config" / "states"
KNOWLEDGE_DIR = WORKSPACE_ROOT / "knowledge"
ADHOC_DIR = WORKSPACE_ROOT / "adhoc"
OUTPUT_DIR = ADHOC_DIR / "logarithm"

# Color palette (professional)
COLOR_CURVE = "#1f77b4"  # steel blue
COLOR_TREND = "#d62728"  # brick red
COLOR_DRAWDOWN = "#d62728"
COLOR_DRAWDOWN_FILL = "#ffcccc"


# ════════════════════════════════════════════════════════════════════════════
#  SYMBOL NAME LOOKUP
# ════════════════════════════════════════════════════════════════════════════


def load_state_names() -> dict[str, str]:
    """Build a {symbol: name} dict from asset-master (all known assets)."""
    names: dict[str, str] = {}
    try:
        from dao.asset_dao import AssetManifest
        for asset in AssetManifest().get_all():
            names[asset.symbol] = asset.name
    except Exception:
        pass
    return names


def resolve_symbol_label(symbol: str, name_map: dict[str, str]) -> str:
    """Return 'symbol (Name)' if known, else just 'symbol'."""
    name = name_map.get(symbol)
    if name and name != symbol:
        return f"{symbol} ({name})"
    return symbol


def calc_ldev(df: pd.DataFrame, window: int = 1250) -> float:
    """Calculate LDev at the last data point using OLS on log prices.

    Args:
        df: DataFrame with 'total_return' column, sorted by date ascending.
        window: OLS lookback in trading days. Default 2500 (~10yr).
                Pass len(df) for full-history mode.
    """
    from scipy.stats import linregress
    vals = df["total_return"].values
    idx = len(vals) - 1
    lookback = min(window, len(vals) - 1)
    window_min = 500
    if idx < window_min:
        return 0.0
    start = max(0, idx - lookback)
    y = np.log(vals[start:idx])
    x = np.arange(len(y))
    slope, intercept, _, _, _ = linregress(x, y)
    residuals = y - (intercept + slope * x)
    sigma = float(np.std(residuals))
    if sigma == 0:
        sigma = 1e-6
    expected = intercept + slope * len(y)
    ldev = (np.log(vals[idx]) - expected) / sigma
    return ldev


def calc_ldev_bands(
    df: pd.DataFrame,
    trend_line: np.ndarray,
    low_threshold: float = -2.0,
    high_threshold: float = 3.0,
    far_bound: float = 3.5,
    global_sigma: bool = False,
) -> tuple:
    """Compute LDev band boundaries matching backtest chart behavior.

    Returns band fill boundaries (lower green, upper red) as price-space arrays.
    Fills are drawn UNCONDITIONALLY wherever sigma is valid — matching the backtest
    which fills the full zone (trend + k*σ) regardless of where price sits.

    Works in log space: residuals = log(price) - log(trend_line).
    If global_sigma=True: fixed σ → straight parallel band lines.
    If global_sigma=False: rolling σ per point (min 500, max 1250 lookback).

    Args:
        df: DataFrame with 'total_return' column.
        trend_line: trend values (same length as df).
        low_threshold: sigma below trend = green zone entry.
        high_threshold: sigma above trend = red zone entry.
        far_bound: sigma for outer fill bound.
        global_sigma: use single global sigma instead of rolling.

    Returns:
        (lower_fill, lower_bound, upper_fill, upper_bound)
        Each is a 1D numpy array (NaN where invalid). Plot with fill_between.
    """
    vals = df["total_return"].values
    log_vals = np.log(vals)
    safe_trend = np.maximum(trend_line, 1e-10)
    log_trend = np.log(safe_trend)
    residuals = log_vals - log_trend

    n = len(residuals)
    sigma_arr = np.full(n, np.nan)
    if global_sigma:
        sigma_all = np.nanstd(residuals, ddof=1)
        if sigma_all <= 0:
            sigma_all = 1e-6
        sigma_arr[:] = sigma_all
    else:
        for i in range(n):
            lookback = min(1250, max(500, i + 1))
            seg = residuals[i - lookback + 1:i + 1]
            if len(seg) >= 50:
                sigma_arr[i] = np.std(seg, ddof=1)

    valid = (sigma_arr > 0) & ~np.isnan(sigma_arr)

    lower_fill = np.full(n, np.nan)
    lower_bound = np.full(n, np.nan)
    upper_fill = np.full(n, np.nan)
    upper_bound = np.full(n, np.nan)

    lower_fill[valid] = np.exp(log_trend[valid] + low_threshold * sigma_arr[valid])
    lower_bound[valid] = np.exp(log_trend[valid] - far_bound * sigma_arr[valid])
    upper_fill[valid] = np.exp(log_trend[valid] + high_threshold * sigma_arr[valid])
    upper_bound[valid] = np.exp(log_trend[valid] + far_bound * sigma_arr[valid])

    return lower_fill, lower_bound, upper_fill, upper_bound


def get_species_thresholds(symbol: str) -> tuple:
    """Look up strategy type from asset master and return LDev thresholds.

    Returns (low_threshold, high_threshold) matching backtest rules:
      STEADY:   (-1.5, 3.0)
      VOLATILE: (-2.0, 1.5)
      MOMENTUM: (-1.0, 3.0)
      default:  (-1.5, 3.0)
    """
    try:
        from dao.asset_dao import AssetManifest
        asset = AssetManifest().get(symbol)
        st = asset.strategy_type if asset else "STEADY"
    except Exception:
        st = "STEADY"

    if st == "VOLATILE":
        return -2.0, 1.5
    elif st == "MOMENTUM":
        return -1.0, 3.0
    else:
        return -1.5, 3.0


# ════════════════════════════════════════════════════════════════════════════
#  TIER RESOLUTION
# ════════════════════════════════════════════════════════════════════════════


def load_state_symbols(filename: str) -> set[str]:
    """Legacy wrapper — delegates to shared symbol_resolver."""
    state = filename.replace(".json", "")
    from utils.symbols.state_resolver import load_state_symbols as _load_state_symbols
    return _load_state_symbols(WORKSPACE_ROOT, state)


def resolve_tier(symbol: str) -> int:
    """Determine resolution tier for a symbol (1=active, 2=watchlist/void, 3=unknown)."""
    active_syms = load_state_symbols("active.json")
    if symbol in active_syms:
        return 1

    watchlist_syms = load_state_symbols("watchlist.json")
    void_syms = load_state_symbols("void.json")
    if symbol in watchlist_syms or symbol in void_syms:
        return 2

    return 3


def detect_region(symbol: str, explicit_region: str | None) -> str:
    """Legacy wrapper — delegates to shared state_resolver."""
    from utils.symbols.state_resolver import detect_region as _detect_region
    return _detect_region(symbol, explicit_region)


# ════════════════════════════════════════════════════════════════════════════
#  DATA LOADING
# ════════════════════════════════════════════════════════════════════════════


def find_adhoc_csv(symbol: str, region: str) -> Path | None:
    """Scan adhoc/ for latest run containing this symbol's processed CSV."""
    pattern = f"**/knowledge/{region}/3_processed/{symbol}.csv"
    matches = sorted(ADHOC_DIR.rglob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def load_from_csv(csv_path: Path) -> pd.DataFrame:
    """Load total_return CSV and parse dates."""
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    if "total_return" not in df.columns:
        raise ValueError(f"CSV missing total_return column: {csv_path}")
    df["total_return"] = pd.to_numeric(df["total_return"], errors="coerce")
    df = df.dropna(subset=["total_return"])
    return df


def fetch_tier3_data(symbol: str, region: str) -> pd.DataFrame:
    """Live bootstrap from API via shared data_resolver — no daily_update coupling."""
    from utils.data_service.data_resolver import bootstrap_csv

    cache_dir = WORKSPACE_ROOT / "adhoc" / "cache"
    cache_path = cache_dir / f"{symbol}.csv"
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = bootstrap_csv(WORKSPACE_ROOT, symbol, None, region, cache_dir)
        if result and Path(result).exists():
            df = pd.read_csv(result)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
            return df
    except Exception as e:
        print(f"[ERROR] Tier 3 bootstrap failed for {symbol}: {e}")
    return pd.DataFrame()

    if df.empty:
        print(f"[WARN] No data fetched for {symbol}")
        return df

    # Cache the fetched data
    df.to_csv(cache_path, index=False)
    print(f"[INFO] Tier 3 data cached → {cache_path}")
    return df


def load_data(symbol: str, region: str) -> pd.DataFrame:
    """Resolve data for a symbol — cache-first, CSI TR for refresh only.

    Priority:
      ① adhoc/cache/{sym}.csv  → use directly (zero API calls if fresh)
      ② Cache exists but old    → CSI TR refresh → use cache
      ③ No cache                → CSI TR bootstrap → write cache → use
      ④ CSI TR fails + cache    → use stale cache with ⚠️ warning
      ⑤ Above all fail           → fall through to knowledge/ tiers
    """
    from datetime import date, timedelta, datetime
    import json, pathlib

    CAC: pathlib.Path = WORKSPACE_ROOT / "adhoc" / "cache"
    cache_path = CAC / f"{symbol}.csv"
    today = date.today()

    # Priority 0: Check knowledge/ path first (shared data layer, state-agnostic).
    # Avoids API bootstrap for assets that already have processed CSVs, regardless
    # of state (active/watchlist/void). Uses shared resolve_price_data (read-only).
    from utils.data_service.data_resolver import resolve_price_data
    _kb_df = resolve_price_data(WORKSPACE_ROOT, symbol, region)
    if _kb_df is not None:
        # resolve_price_data is cache-first: adhoc/cache → knowledge/3_processed
        cache_path = CAC / f"{symbol}.csv"
        src = "cache" if cache_path.exists() else "knowledge"
        print(f"[INFO] Data HIT → {src}/{symbol}.csv")
        _kb_df["date"] = pd.to_datetime(_kb_df["date"])
        _kb_df = _kb_df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
        return _kb_df

    # Helper: refresh cache from CSI TR or fallback
    def _try_refresh_cache(asset, cache_path, cache_dir):
        """Append new data to existing cache. Never overwrites history."""
        try:
            import pandas as pd, akshare as ak
            existing = pd.read_csv(cache_path)
            if existing.empty or "date" not in existing.columns:
                return False
            existing["date"] = pd.to_datetime(existing["date"])
            last_d = existing["date"].max().strftime("%Y%m%d")
            end_d = (today + timedelta(days=1)).strftime("%Y%m%d")
            tracks = getattr(asset, "tracks", None)
            provider = getattr(asset, "cal_source", None)
            prov_name = provider.provider if provider else ""

            # Try CSI TR
            tr_code = None
            if tracks and "中证指数" in prov_name:
                res_dir = WORKSPACE_ROOT / "config" / "symbol_resolution"
                tr_map = {}
                if (res_dir / "tr_mapping.json").exists():
                    tr_map = json.loads((res_dir / "tr_mapping.json").read_text())
                csi_p = {}
                if (res_dir / "csi_patterns.json").exists():
                    csi_p = json.loads((res_dir / "csi_patterns.json").read_text())
                if tracks in tr_map:
                    tr_code = tr_map[tracks]["tr_code"]
                elif tracks.startswith(("H", "92")) or "CNY" in tracks:
                    tr_code = tracks
                else:
                    for prefix, info in csi_p.items():
                        if tracks.startswith(prefix):
                            tr_code = f"{tracks}{info['suffix']}"
                            break
                    if not tr_code:
                        tr_code = tracks

            new_rows = None
            if tr_code:
                try:
                    from utils.data_service.market_service import fetch_csi_index
                    csi_data = fetch_csi_index(tr_code, tr_code, last_d, end_d)
                    if csi_data and "price" in csi_data:
                        ndf = csi_data["price"][csi_data["price"]["date"] > existing["date"].max()].copy()
                        if not ndf.empty:
                            ndf["total_return"] = ndf["close"].round(6)
                            ndf["close"] = ndf["close"]
                            new_rows = ndf[["date", "total_return", "close"]]
                except Exception:
                    pass

            if new_rows is not None and not new_rows.empty:
                combined = pd.concat([existing, new_rows], ignore_index=True)
                combined = combined.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
                combined.to_csv(cache_path, index=False)
        except Exception:
            pass

    # Helper: bootstrap cache (full history, first time)
    def _bootstrap_cache(symbol, asset, cache_dir):
        """Full bootstrap from CSI TR, CN_OTC, or Sina/EM via market_service."""
        try:
            import pandas as pd
            tracks = getattr(asset, "tracks", None)
            provider = getattr(asset, "cal_source", None)
            prov_name = provider.provider if provider else ""
            asset_type = getattr(asset, "asset_type", None)
            atype = asset_type.value if hasattr(asset_type, "value") else str(asset_type) if asset_type else "CN_ETF"
            end_d = (today + timedelta(days=1)).strftime("%Y%m%d")

            # Resolve TR code (for CSI index ETFs only)
            tr_code = None
            if tracks and "中证指数" in prov_name:
                res_dir = WORKSPACE_ROOT / "config" / "symbol_resolution"
                tr_map = {}
                if (res_dir / "tr_mapping.json").exists():
                    tr_map = json.loads((res_dir / "tr_mapping.json").read_text())
                csi_p = {}
                if (res_dir / "csi_patterns.json").exists():
                    csi_p = json.loads((res_dir / "csi_patterns.json").read_text())
                if tracks in tr_map:
                    tr_code = tr_map[tracks]["tr_code"]
                elif tracks.startswith(("H", "92")) or "CNY" in tracks:
                    tr_code = tracks
                else:
                    for prefix, info in csi_p.items():
                        if tracks.startswith(prefix):
                            tr_code = f"{tracks}{info['suffix']}"
                            break
                    if not tr_code:
                        tr_code = tracks

            # Path A: CSI TR via market_service
            if tr_code:
                try:
                    from utils.data_service.market_service import fetch_csi_index
                    csi_data = fetch_csi_index(tr_code, tr_code, "20000101", end_d)
                    if csi_data and "price" in csi_data:
                        df_p = csi_data["price"]
                        if len(df_p) > 252:
                            tr_vals = df_p["close"].values.astype(float)
                            df_out = pd.DataFrame({
                                "date": df_p["date"],
                                "total_return": tr_vals.round(6),
                                "close": tr_vals,
                            })
                            df_out = df_out.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
                            cache_dir.mkdir(parents=True, exist_ok=True)
                            df_out.to_csv(cache_path, index=False)
                            print(f"[Cache] Bootstrap {symbol}: CSI TR {tr_code} ({len(df_out)} rows)")
                            return True, tr_code
                except Exception:
                    pass

            # Path B: CN_OTC via market_service
            if atype == "CN_OTC" or (atype == "CN_ETF" and "天天基金" in prov_name):
                try:
                    from utils.data_service.market_service import fetch_total_return
                    tr_series = fetch_total_return(symbol, "cn")
                    if tr_series is not None and len(tr_series) > 20:
                        vals = tr_series.values.astype(float)
                        date_strs = [str(d) for d in tr_series.index]
                        df_out = pd.DataFrame({
                            "date": date_strs,
                            "total_return": vals.round(6),
                            "close": vals,
                        })
                        df_out = df_out.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        df_out.to_csv(cache_path, index=False)
                        print(f"[Cache] Bootstrap {symbol}: CN_OTC ({len(df_out)} rows)")
                        return True, None
                except Exception:
                    pass
            # Path C: Sina/EM fallback via market_service
            try:
                ohlcv_df = fetch_ohlcv(symbol, "cn")
                if ohlcv_df is not None and len(ohlcv_df) > 20:
                    from utils.normalize import close_to_total_return
                    closes = np.array(ohlcv_df['close'].dropna().values)
                    if len(closes) > 0:
                        normalized = close_to_total_return(closes)
                        df_out = pd.DataFrame({
                            "date": ohlcv_df["date"].values[:len(normalized)],
                            "total_return": normalized.round(6),
                            "close": closes,
                        })
                        df_out = df_out.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        df_out.to_csv(cache_path, index=False)
                        print(f"[Cache] Bootstrap {symbol}: Sina/EM via market_service ({len(df_out)} rows)")
                        return True, None
            except Exception:
                pass
            return False, None
        except Exception:
            return False, None

    # ── ① Try adhoc/cache/ first (SSOT for ALL symbols) ──
    tier = resolve_tier(symbol)

    if cache_path.exists():
        try:
            df = load_from_csv(cache_path)
            print(f"[INFO] Cache HIT → {cache_path} ({len(df)} rows)")
            # Try refresh in background (don't block if API fails)
            try:
                from dao.asset_dao import AssetManifest
                for asset in AssetManifest().get_all():
                    if asset.symbol == symbol:
                        _try_refresh_cache(asset, cache_path, CAC)
                        break
                df = load_from_csv(cache_path)
                df.attrs["data_source"] = "cache"
            except Exception:
                pass
            return df
        except Exception:
            print(f"[WARN] Cache corrupted for {symbol}, re-bootstrapping...")

    # ── ② Try adhoc history from previous runs (read-only, no API) ──
    if tier == 2:
        adhoc_path = find_adhoc_csv(symbol, region)
        if adhoc_path:
            source = f"Tier 2 (adhoc history) → {adhoc_path.relative_to(ADHOC_DIR)}"
            print(f"[INFO] {source}")
            # Copy to cache, then try incremental refresh (no full bootstrap)
            CAC.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(str(adhoc_path), str(cache_path))
            df = load_from_csv(cache_path)
            try:
                from dao.asset_dao import AssetManifest
                for asset in AssetManifest().get_all():
                    if asset.symbol == symbol:
                        _try_refresh_cache(asset, cache_path, CAC)
                        break
                df = load_from_csv(cache_path)
                df.attrs["data_source"] = "cache+refresh"
            except Exception:
                pass
            return df

    # ── ③ No cache, no history — bootstrap (non-active only) ──
    if not is_active:
        try:
            from dao.asset_dao import AssetManifest
            for asset in AssetManifest().get_all():
                if asset.symbol == symbol:
                    ok, tr_code = _bootstrap_cache(symbol, asset, CAC)
                    if ok:
                        df = load_from_csv(cache_path)
                        df.attrs["data_source"] = f"CSI TR {tr_code}" if tr_code else "cache"
                        return df
                    break
        except Exception as e:
            print(f"[WARN] Cache bootstrap failed for {symbol}: {e}")

    # ── ④ Fall through: knowledge/ (by state) → live fetch ──
    if tier == 1:
        csv_path = KNOWLEDGE_DIR / region / "3_processed" / f"{symbol}.csv"
        source = f"Tier 1 (active) → {csv_path}"
        if csv_path.exists():
            print(f"[INFO] {source}")
            return load_from_csv(csv_path)
        print(f"[WARN] {csv_path} not found, falling through tiers")

    # Check knowledge/ one more time regardless of state (catch-all)
    csv_path = KNOWLEDGE_DIR / region / "3_processed" / f"{symbol}.csv"
    if csv_path.exists():
        source = f"Fallback (knowledge) → {csv_path}"
        print(f"[INFO] {source}")
        return load_from_csv(csv_path)

    print(f"[INFO] Tier 3 (live fetch) → {symbol}")
    df = fetch_tier3_data(symbol, region)
    if not df.empty:
        return df

    print(f"[INFO] Tier 4 (symbol resolver) → {symbol}")
    try:
        from utils.symbols.symbol_bootstrapper import resolve as resolve_symbol
        csv_path, msg = resolve_symbol(symbol, region)
        if csv_path:
            return load_from_csv(Path(csv_path))
        print(f"[WARN] {msg}")
    except Exception as exc:
        print(f"[WARN] Symbol resolver failed for {symbol}: {exc}")

    return df


# ════════════════════════════════════════════════════════════════════════════
#  DATA TRANSFORMATION
# ════════════════════════════════════════════════════════════════════════════


def truncate_data(df: pd.DataFrame, years: int | None, all_history: bool) -> pd.DataFrame:
    """Truncate data window based on years/all-history flag."""
    if all_history:
        return df
    if years is None:
        years = 10

    end_date = df["date"].max()
    start_date = end_date - pd.Timedelta(days=int(years * 365.25))
    available_days = (end_date - df["date"].min()).days

    if available_days < years * 365.25:
        print(f"[INFO] Data only covers {available_days / 365.25:.1f}y, using full available range")
        return df

    return df[df["date"] >= start_date].reset_index(drop=True)


def calc_cagr(df: pd.DataFrame) -> float:
    """Calculate CAGR over the data period."""
    start_val = df["total_return"].iloc[0]
    end_val = df["total_return"].iloc[-1]
    years = (df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25
    if start_val <= 0 or years <= 0:
        return 0.0
    return (end_val / start_val) ** (1 / years) - 1


def calc_trend_line(df: pd.DataFrame, cagr: float) -> np.ndarray:
    """Calculate CAGR trend line values."""
    start_val = df["total_return"].iloc[0]
    days = (df["date"] - df["date"].iloc[0]).dt.days.values.astype(float)
    years_elapsed = days / 365.25
    return start_val * (1 + cagr) ** years_elapsed


def calc_ols_trend(df: pd.DataFrame) -> np.ndarray:
    """Calculate OLS trend curve on log prices (full data window)."""
    from scipy.stats import linregress
    vals = df["total_return"].values
    log_vals = np.log(vals)
    x = np.arange(len(vals))
    slope, intercept, _, _, _ = linregress(x, log_vals)
    trend = np.exp(intercept + slope * x)
    return trend


def calc_drawdown(df: pd.DataFrame) -> np.ndarray:
    """Calculate drawdown from peak as percentage."""
    peak = df["total_return"].cummax()
    return (df["total_return"] - peak) / peak * 100


def calc_rolling_annualized(df: pd.DataFrame, window_years: int = 3) -> pd.Series:
    """Calculate rolling annualized return for annotation."""
    window = int(window_years * 252)  # approximate trading days
    if len(df) < window:
        return pd.Series(index=df.index, dtype=float)
    daily_returns = df["total_return"].pct_change()
    rolling_ret = daily_returns.rolling(window=window).mean() * 252
    return rolling_ret


# ════════════════════════════════════════════════════════════════════════════
#  CHART RENDERING
# ════════════════════════════════════════════════════════════════════════════


def format_yaxis(value, _):
    """Format y-axis tick labels."""
    if value >= 1000:
        return f"{value/1000:.0f}k"
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


# ── Bond yield panel ──────────────────────────────────────────────────────
_YIELD_COLORS = {"10年": "#2ca02c", "30年": "#d62728", "10Y": "#2ca02c", "30Y": "#d62728"}


def _show_yield_panel(symbol: str) -> bool:
    """Check if symbol should show yield panel: bond sleeve with long or medium duration."""
    try:
        from dao.asset_dao import AssetManifest
        asset = AssetManifest().get(symbol)
        if asset and asset.sleeve == "bond":
            tags = asset.tags or []
            if "long_duration" in tags or "medium_duration" in tags:
                return True
    except Exception:
        pass
    return False


def _get_yield_panel(symbol: str, ax, df_dates):
    """Draw yield history panel for bond symbols (long/medium duration). Returns True if drawn."""
    if not _show_yield_panel(symbol):
        return False
    try:
        from scipy.stats import percentileofscore
        from utils.data_service.bond_service import get_yield_series

        # Determine region and duration from asset
        from dao.asset_dao import AssetManifest
        asset = AssetManifest().get(symbol)
        region = "us" if (asset and hasattr(asset, "region") and asset.region.upper() == "US") else "cn"
        tags = asset.tags or []
        is_long = "long_duration" in tags

        tenor = "30年" if is_long else "10年"
        tenor_label = "30年" if is_long else "10年"

        y_series = get_yield_series(region, tenor)

        # Overlay on ax2
        start = df_dates.min() if len(df_dates) > 0 else y_series.index[0]
        end = df_dates.max() if len(df_dates) > 0 else y_series.index[-1]
        y_window = y_series[(y_series.index >= start) & (y_series.index <= end)]

        color = _YIELD_COLORS.get(tenor_label, "#2ca02c")
        ax.plot(y_window.index, y_window.values, color=color, linewidth=1.2)

        # Percentile reference lines (using chart-period window, matching total_return timeline)
        current = float(y_window.iloc[-1])
        pcts = [10, 25, 50, 75, 90]
        for p in pcts:
            val = float(np.percentile(y_window.values, p))
            ax.axhline(y=val, color="gray", linewidth=0.5, linestyle=":", alpha=0.5)
            ax.text(y_window.index[0], val, f" {p}th", fontsize=7, color="gray", alpha=0.6)

        # Current yield marker
        pct = percentileofscore(y_window, current)
        ax.axhline(y=current, color="#d62728", linewidth=0.8, linestyle="--")
        ax.plot(y_window.index[-1], current, "o", color="#d62728", markersize=5)

        region_label = "US" if region == "us" else "CGB"
        ax.set_ylabel(f"{tenor_label} {region_label} Yield (%)", fontsize=10, color=color)
        ax.set_title(f"{tenor_label} {'美国' if region == 'us' else '中国'}国债收益率历史 (Pctile {pct:.0f}th)", fontsize=10)
        # Dynamic y-limit: yield data range with 15% padding
        y_min = y_window.min() * 0.85
        y_max = y_window.max() * 1.15
        ax.set_ylim(bottom=y_min, top=y_max)
        ax.grid(True, alpha=0.2)

        return True
    except Exception as e:
        print(f"  [Yield panel skipped: {e}]")
        return False


def draw_chart(
    df: pd.DataFrame,
    symbol: str,
    symbol_label: str,
    years_str: str,
    region: str,
    output_path: Path,
    no_display: bool = False,
    rolling_years: int = 0,
) -> Path:
    """Render the semi-log chart.

    Default: 10-year chart period, straight OLS trend line, 10-year LDev.
    --rolling N: 10-year chart, rolling OLS curve, N-year rolling LDev.
    """
    cagr = calc_cagr(df)
    drawdown = calc_drawdown(df)
    max_dd = drawdown.min()

    # Trend line: straight OLS by default, rolling curve when --rolling
    ldev_window = 2500 if rolling_years <= 0 else rolling_years * 250
    if rolling_years > 0:
        window_days = rolling_years * 250
        trend_line = calc_rolling_trend_curve(df["total_return"].values, window_days=window_days)
        trend_label = f"Rolling {rolling_years}Y OLS"
        # MA250 (1-year moving average)
        ma250 = df["total_return"].rolling(window=250, min_periods=250).mean()
    else:
        trend_line = calc_ols_trend(df)
        trend_label = f"CAGR {cagr:.1%}"

    ldev = calc_ldev(df, window=ldev_window)

    # Create figure
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(16, 9),
        gridspec_kw={"height_ratios": [7, 3]},
        sharex=True,
    )
    fig.patch.set_facecolor("white")

    # ── Top panel: Total Return (log scale) ───────────────────────────────────
    ax1.set_yscale("log")
    ax1.plot(df["date"], df["total_return"], color=COLOR_CURVE, linewidth=1.8, label="Total Return")
    if rolling_years > 0:
        valid = ~np.isnan(trend_line)
        ax1.plot(df["date"][valid], trend_line[valid], color=COLOR_TREND, linewidth=1.5, linestyle="--", label=trend_label)
        # MA250 (1-year moving average)
        valid_ma = ~ma250.isna()
        ax1.plot(df["date"][valid_ma], ma250[valid_ma], color="#e67e22", linewidth=1.2, linestyle=":", label="MA250")
        fill_vals = np.where(valid, trend_line, df["total_return"])
        ax1.fill_between(df["date"], df["total_return"], fill_vals, alpha=0.08, color=COLOR_TREND)
    else:
        ax1.plot(df["date"], trend_line, color=COLOR_TREND, linewidth=1.5, linestyle="--", label=trend_label)
        ax1.fill_between(df["date"], df["total_return"], trend_line, alpha=0.08, color=COLOR_TREND)

    # ── LDev bands (overheat / undervalued zones) ─────────────────────────
    # Matching backtest: fill the FULL zone (trend + k*σ) unconditionally,
    # not just where price is in the zone.
    low_ldev, high_ldev = get_species_thresholds(symbol)
    lower_fill, lower_bound, upper_fill, upper_bound = calc_ldev_bands(
        df, trend_line, low_threshold=low_ldev, high_threshold=high_ldev,
        global_sigma=(rolling_years == 0),
    )
    if not np.all(np.isnan(lower_fill)):
        ax1.fill_between(
            df["date"], lower_fill, lower_bound,
            color="#2ecc71", alpha=0.12, label="Undervalued Zone",
        )
    if not np.all(np.isnan(upper_fill)):
        ax1.fill_between(
            df["date"], upper_fill, upper_bound,
            color="#e74c3c", alpha=0.12, label="Overheat Zone",
        )

    # Volatility (annualized)
    daily_ret = df["total_return"].pct_change().dropna()
    vol = float(daily_ret.std() * np.sqrt(252))

    # Annotations
    end_ret = df["total_return"].iloc[-1]
    start_ret = df["total_return"].iloc[0]
    total_gain = (end_ret / start_ret - 1)

    ldev_label = f"LDev({ldev_window//250}Y)" if rolling_years > 0 else "LDev(10Y)"
    box_text = (
        f"CAGR {cagr:.1%}  |  Vol {vol:.1%}  |  {ldev_label} {ldev:+.2f}σ  |  "
        f"Max DD {max_dd:.1f}%  |  Gain {total_gain:.1%}"
        )

    # Valuation data (PE + dividend percentile) - objective only
    val_note = None
    try:
        from skills.analyze.scripts.s4_strategy.valuation import get_valuation
        val = get_valuation(symbol)
        if val:
            val_parts = []
            if val.get("pe") is not None:
                pct = val.get("pe_pctile")
                if pct is not None:
                    val_parts.append(f"PE {val['pe']} ({pct:.0f}%ile)")
                else:
                    val_parts.append(f"PE {val['pe']}")
            if val.get("div_yield") is not None:
                pct = val.get("div_pctile")
                if pct is not None:
                    val_parts.append(f"Div {val['div_yield']:.2f}% ({pct:.0f}%ile)")
                else:
                    val_parts.append(f"Div {val['div_yield']:.2f}%")
            if val_parts:
                box_text += "\n" + "  |  ".join(val_parts)
    except Exception:
        pass

    ax1.text(
        0.5, 0.95, box_text,
        transform=ax1.transAxes, fontsize=11,
        horizontalalignment="center", verticalalignment="top",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="white", edgecolor="#cccccc", alpha=0.9),
    )
    
    date_range = f"{df['date'].iloc[0].strftime('%Y-%m-%d')} → {df['date'].iloc[-1].strftime('%Y-%m-%d')}"
    title_suffix = f"{years_str} (LDev {ldev_window//250}Y rolling)" if rolling_years > 0 else years_str
    # Data source label for subtitle
    data_source = df.attrs.get("data_source", "")
    subtitle = f"数据源: {data_source}" if data_source else ""
    ax1.set_title(
        f"{symbol_label} 全收益对数坐标图 ({title_suffix})",
        fontsize=16, fontweight="bold", pad=15,
    )
    if subtitle:
        ax1.text(0.5, 1.02, subtitle, transform=ax1.transAxes, fontsize=9,
                 ha="center", va="bottom", color="#888888")
    ax1.set_ylabel("Total Return (log scale)", fontsize=11)
    ax1.legend(loc="upper left", fontsize=10)
    ax1.grid(True, alpha=0.3, which="both")
    # Dynamic y-limits based on data range (5% padding on log scale)
    data_min = df["total_return"].min()
    data_max = df["total_return"].max()
    if data_min > 0 and data_max > data_min:
        log_range = np.log(data_max / data_min)
        pad = np.exp(log_range * 0.05)
        ax1.set_ylim(bottom=max(data_min / pad, 0.1), top=data_max * pad)
    else:
        ax1.set_ylim(bottom=max(data_min * 0.7, 0.1))

    # Y-axis log ticks with readable labels
    ax1.yaxis.set_major_formatter(FuncFormatter(format_yaxis))

    # ── Bottom panel: Yield (bond) or Drawdown (equity/commodity) ────────
    is_bond = _show_yield_panel(symbol)

    if is_bond:
        # Yield history panel
        used_yield = _get_yield_panel(symbol, ax2, df["date"])
        if not used_yield:
            # Fall back to drawdown if yield data unavailable
            is_bond = False

    if not is_bond:
        ax2.fill_between(df["date"], 0, drawdown, color=COLOR_DRAWDOWN_FILL, alpha=0.7)
        ax2.plot(df["date"], drawdown, color=COLOR_DRAWDOWN, linewidth=1.2)
        ax2.axhline(y=0, color="black", linewidth=0.5)
        ax2.set_ylabel("Drawdown (%)", fontsize=11)

    ax2.set_xlabel("Date", fontsize=11)

    if is_bond:
        pass  # yield panel handles annotations
    else:
        # Drawdown annotations
        min_dd_idx = drawdown.idxmin()
        ax2.annotate(
        f"Max DD: {max_dd:.1f}%",
        xy=(df["date"].iloc[min_dd_idx], drawdown[min_dd_idx]),
        xytext=(0, -25),
        textcoords="offset points",
        fontsize=9,
        color=COLOR_DRAWDOWN,
        arrowprops=dict(arrowstyle="->", color=COLOR_DRAWDOWN, lw=1),
        ha="center",
    )

    if not is_bond:
        ax2.grid(True, alpha=0.3)
        dd_min = drawdown.min()
        dd_max = drawdown.max()
        if dd_max > dd_min:
            dd_range = dd_max - dd_min
            dd_pad = dd_range * 0.05
            ax2.set_ylim(bottom=dd_min - dd_pad, top=dd_max + dd_pad)
        else:
            ax2.set_ylim(bottom=max(drawdown.min() * 1.3, -100), top=5)

    # X-axis date formatting
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    fig.autofmt_xdate()

    # Footer
    ldev_note = f"LDev {ldev_window//250}Y rolling" if rolling_years > 0 else "LDev 10Y global"
    fig.text(
        0.5, 0.01,
        f"7S Logarithm | {ldev_note} | Data: {date_range} | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ha="center", fontsize=8, color="#888888",
    )

    plt.tight_layout(rect=[0, 0.02, 1, 0.98])

    # Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_days = (df['date'].iloc[-1] - df['date'].iloc[0]).days
    print(f"  Data: {date_range} ({data_days} days)")
    plt.savefig(str(output_path), dpi=150, bbox_inches="tight", facecolor="white")
    print(f"[OK] Chart saved → {output_path}")

    if not no_display:
        plt.show()
    else:
        plt.close()

    # Emit push-ready marker for agent pickup
    return output_path


# ════════════════════════════════════════════════════════════════════════════
#  CLI & MAIN
# ════════════════════════════════════════════════════════════════════════════


def load_plan_weighted_series(plan_id: str, years: int | None, all_history: bool) -> pd.DataFrame | None:
    """Load a plan's assets and compute a weighted portfolio total_return series.

    Reads plan JSON from config/plans/<plan_id>/v<ver>.json.
    Loads each asset's total_return via load_data(), aligns dates, and
    computes portfolio_value = Σ(weight_i × total_return_i) / Σ(weights).

    Returns a single DataFrame with 'date', 'total_return', 'close' columns,
    or None if no assets could be loaded.
    """
    plan_dir = WORKSPACE_ROOT / "config" / "plans" / plan_id
    versions = sorted(plan_dir.glob("v*.json"))
    if not versions:
        print(f"[ERROR] Plan '{plan_id}' not found")
        return None
    with open(versions[-1]) as f:
        import json
        plan = json.load(f)

    assets = plan.get("all_assets", [])
    if not assets:
        print(f"[ERROR] Plan '{plan_id}' has no assets")
        return None

    from utils.symbols.state_resolver import detect_region as _detect_region
    region = _detect_region(assets[0]["symbol"], None)

    # Load each asset and track date alignment
    series_list = []
    weight_list = []
    for a in assets:
        sym = a["symbol"]
        w = a.get("target_weight", 0.0)
        print(f"  {sym}: loading (weight={w:.0%})")
        df = load_data(sym, region)
        if df.empty or "total_return" not in df.columns:
            print(f"  [SKIP] {sym}: no data")
            continue
        series_list.append(df.set_index("date")["total_return"])
        weight_list.append(w)

    if not series_list:
        print("[ERROR] No asset data could be loaded for plan")
        return None

    # Align dates — only keep dates where ALL assets have data
    aligned = pd.concat(series_list, axis=1, join="inner")
    if aligned.empty or len(aligned.columns) < 2:
        print("[ERROR] Insufficient overlapping date range")
        return None

    # Normalize weights to sum to 1.0
    w_sum = sum(weight_list)
    norm_weights = [w / w_sum for w in weight_list]

    # Weighted portfolio total_return
    port_vals = aligned.dot(norm_weights)
    port_df = port_vals.reset_index()
    port_df.columns = ["date", "total_return"]
    port_df["close"] = port_df["total_return"]  # synthetic close = total_return
    port_df = port_df.sort_values("date").reset_index(drop=True)

    port_df.attrs["data_source"] = f"Plan '{plan_id}' ({len(assets)} assets, weighted)"
    print(f"  → Portfolio: {len(port_df)} rows, {len(assets)} assets, region={region}")
    return port_df


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Draw semi-log total-return charts with CAGR trend line & drawdown panel.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--symbol", type=str, default=None,
        help="Single symbol or comma-separated (e.g., SPYM,TLT,GLDM)",
    )
    parser.add_argument(
        "--plan", type=str, default=None,
        help="Plan name to chart as weighted portfolio (e.g., cn_bond)",
    )
    parser.add_argument(
        "--active", action="store_true",
        help="Draw charts for active holdings",
    )
    parser.add_argument(
        "--watchlist", action="store_true",
        help="Draw charts for watchlist",
    )
    parser.add_argument(
        "--void", action="store_true",
        help="Draw charts for void assets",
    )
    parser.add_argument(
        "--region", type=str, choices=["cn", "us"], default=None,
        help="Region (auto-detected from symbol if omitted)",
    )
    parser.add_argument(
        "--years", type=int, default=10,
        help="Backward years to display (default: 10; auto-shrink if data shorter)",
    )
    parser.add_argument(
        "--all-history", action="store_true",
        help="Use all available historical data regardless of --years",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Custom output path for the chart PNG",
    )
    parser.add_argument(
        "--no-display", action="store_true",
        help="Save only, suppress interactive display",
    )
    parser.add_argument(
        "--push", action="store_true",
        help="Flag indicating chart should be pushed to Feishu. Prints output path for agent pickup.",
    )
    parser.add_argument(
        "--rolling", type=int, nargs="?", const=5, default=0,
        help="Rolling LDev window in years (default: 10Y global; --rolling = 5Y; --rolling 3 = 3Y)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.plan:
        port_df = load_plan_weighted_series(args.plan, args.years, args.all_history)
        if port_df is None:
            return 1
        port_df = truncate_data(port_df, args.years, args.all_history)
        if port_df.empty:
            print("[ERROR] No data after truncation for plan portfolio")
            return 1
        # Detect region from first asset in plan (already resolved inside load)
        years_str = "全历史" if args.all_history else f"{args.years}Y"
        output_path = OUTPUT_DIR / f"plan_{args.plan}_{years_str}_{datetime.now().strftime('%Y-%m-%d')}.png"
        draw_chart(
            port_df, f"plan:{args.plan}", f"{args.plan} Portfolio",
            years_str, args.region or "cn", output_path, args.no_display,
            rolling_years=args.rolling,
        )
        if args.push:
            print(f"\n[PUSH] {output_path}")
        return 0

    if args.symbol:
        symbols = [s.strip() for s in args.symbol.split(",")]
    elif args.active or args.watchlist or args.void:
        from utils.symbols.state_resolver import resolve_symbols_from_args as _resolve
        syms = _resolve(WORKSPACE_ROOT, region=args.region or "all",
                         use_active_state=args.active,
                         use_watchlist_state=args.watchlist,
                         use_void_state=args.void)
        symbols = list(syms)
    else:
        parser.error("Provide one of --symbol, --plan, --active, --watchlist, --void")

    if not symbols:
        print("[ERROR] No symbols resolved")
        return 1

    region = detect_region(symbols[0], args.region)

    # Determine years label
    if args.all_history:
        years_str = "全历史"
    else:
        years_str = f"{args.years}Y"

    # Build symbol label
    name_map = load_state_names()
    symbol_label = ", ".join(resolve_symbol_label(s, name_map) for s in symbols)
    if len(symbol_label) > 80:
        symbol_label = f"{len(symbols)} assets overlay"

    # Resolve output path
    if args.output:
        output_path = Path(args.output)
    else:
        multi_tag = "-".join(symbols)
        if len(multi_tag) > 40:
            multi_tag = f"{len(symbols)}assets"
        fname = f"{multi_tag}_{years_str}_{datetime.now().strftime('%Y-%m-%d')}.png"
        output_path = OUTPUT_DIR / fname

    # Load data for each symbol
    dfs = []
    for sym in symbols:
        print(f"\n── Resolving data for {sym} ──")
        df = load_data(sym, region)
        if df.empty:
            print(f"[ERROR] No data for {sym}, skipping")
            continue
        df_truncated = truncate_data(df, args.years, args.all_history)
        if df_truncated.empty:
            print(f"[ERROR] No data after truncation for {sym}, skipping")
            continue
        dfs.append((sym, df_truncated))

    if not dfs:
        print("[ERROR] No data available for any symbol")
        return 1

    # Draw chart (use first symbol's data for single, or overlay for multi)
    if len(dfs) == 1:
        single_sym = dfs[0][0]
        df_first = dfs[0][1]
        label = resolve_symbol_label(single_sym, name_map)
        result_path = draw_chart(
            df_first, single_sym, label, years_str, region, output_path, args.no_display,
            rolling_years=args.rolling,
        )
    else:
        if args.trend == "rolling":
            # Rolling mode multi-symbol: separate subplots per symbol
            fig, axes = plt.subplots(len(dfs), 2, figsize=(16, 5 * len(dfs)),
                                      gridspec_kw={"width_ratios": [7, 3]})
            if len(dfs) == 1:
                axes = [axes]
            fig.patch.set_facecolor("white")
            colors = plt.cm.Set1(np.linspace(0, 1, len(dfs)))

            for idx, ((sym, df_sym), color) in enumerate(zip(dfs, colors)):
                ax1 = axes[idx][0] if len(dfs) > 1 else axes[idx][0]
                ax2_ax = axes[idx][1] if len(dfs) > 1 else axes[idx][1]
                sym_label = resolve_symbol_label(sym, name_map)

                cagr_sym = calc_cagr(df_sym)
                drawdown_sym = calc_drawdown(df_sym)
                window_days = args.window * 250
                rolling_trend_sym = calc_rolling_trend_curve(df_sym["total_return"].values, window_days=window_days)

                ax1.set_yscale("log")
                ax1.plot(df_sym["date"], df_sym["total_return"], color=color, linewidth=1.5)
                valid = ~np.isnan(rolling_trend_sym)
                ax1.plot(df_sym["date"][valid], rolling_trend_sym[valid], color=COLOR_TREND, linewidth=1.0, linestyle="--")
                ax1.set_title(f"{sym_label} (Loc CAGR {cagr_sym:.1%})", fontsize=12, fontweight="bold")
                ax1.grid(True, alpha=0.3, which="both")
                ax1.yaxis.set_major_formatter(FuncFormatter(format_yaxis))

                ax2_ax.fill_between(df_sym["date"], 0, drawdown_sym, color=color, alpha=0.15)
                ax2_ax.plot(df_sym["date"], drawdown_sym, color=color, linewidth=0.8)
                ax2_ax.axhline(y=0, color="black", linewidth=0.5)
                ax2_ax.grid(True, alpha=0.3)

            fig.text(
                0.5, 0.01,
                f"7S Logarithm | Trend: rolling ({args.window}Y) | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                ha="center", fontsize=8, color="#888888",
            )
        else:
            # Multi-symbol overlay: plot all on same chart
            fig, (ax1, ax2) = plt.subplots(
                2, 1, figsize=(16, 9),
                gridspec_kw={"height_ratios": [7, 3]},
                sharex=True,
            )
            fig.patch.set_facecolor("white")

            colors = plt.cm.Set1(np.linspace(0, 1, len(dfs)))
            for (sym, df), color in zip(dfs, colors):
                cagr = calc_cagr(df)
                drawdown = calc_drawdown(df)
                ax1.set_yscale("log")
                ax1.plot(df["date"], df["total_return"], color=color, linewidth=1.5, label=f"{sym} ({cagr:.1%})")
                ax2.fill_between(df["date"], 0, drawdown, color=color, alpha=0.15)
                ax2.plot(df["date"], drawdown, color=color, linewidth=1.0, alpha=0.7)

            ax1.set_title(f"{symbol_label} 全收益对数坐标图 ({years_str})", fontsize=16, fontweight="bold", pad=15)
            ax1.set_ylabel("Total Return (log scale)", fontsize=11)
            ax1.legend(loc="upper left", fontsize=10)
            ax1.grid(True, alpha=0.3, which="both")
            ax1.yaxis.set_major_formatter(FuncFormatter(format_yaxis))

            ax2.axhline(y=0, color="black", linewidth=0.5)
            ax2.set_ylabel("Drawdown (%)", fontsize=11)
            ax2.set_xlabel("Date", fontsize=11)
            ax2.grid(True, alpha=0.3)

            fig.text(
                0.5, 0.01,
                f"7S Logarithm | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                ha="center", fontsize=8, color="#888888",
            )

        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax2.xaxis.set_major_locator(mdates.YearLocator())
        fig.autofmt_xdate()

        fig.text(
            0.5, 0.01,
            f"7S Logarithm | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            ha="center", fontsize=8, color="#888888",
        )

        plt.tight_layout(rect=[0, 0.02, 1, 0.98])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(str(output_path), dpi=150, bbox_inches="tight", facecolor="white")
        print(f"[OK] Chart saved → {output_path}")

        if not args.no_display:
            plt.show()
        else:
            plt.close()

    # Push marker: emit output path for agent pickup
    if args.push:
        print(f"\n[PUSH] {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
