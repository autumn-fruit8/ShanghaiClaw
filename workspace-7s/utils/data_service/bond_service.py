"""
Bond yield service — unified interface for government bond yields across regions.

Supports:
  - China (cn): 10Y and 30Y government bond yields via akshare.bond_zh_us_rate()
  - United States (us): 10Y, 2Y, etc. via FRED (DGS10, DGS2, ...)

Data is cached in {CACHE_ROOT}/bond_yield/{region}_{tenor}.csv with a TTL (default 4 hours).

Provides:
  - get_yield_series(region, tenor) -> pd.Series (date index, yield values)
  - get_yield_percentiles(region, tenor, current_val=None) -> dict
  - get_yield_signal(region, tenor) -> dict (signal classification based on percentiles)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    import akshare as ak
except ImportError:
    ak = None

try:
    from fredapi import Fred
except ImportError:
    Fred = None

# Cache directory
from config import CACHE_ROOT
_CACHE_DIR = CACHE_ROOT / "bond_yield"
_CACHE_TTL_HOURS = 4

# Mapping for China bond yield tenors (akshare.bond_zh_us_rate() columns)
_CN_TENOR_COLUMNS = {
    "10年": "中国国债收益率10年",
    "30年": "中国国债收益率30年",
}

# Mapping for US bond yield tenors (FRED series IDs)
_US_TENOR_SERIES = {
    "10Y": "DGS10",
    "30Y": "DGS30",
    "2Y": "DGS2",
    "5Y": "DGS5",
    "1Y": "DGS1",
    # Add more as needed
}

# Mapping for US bond yield tenors (akshare.bond_zh_us_rate() columns, fallback)
_US_AKSHARE_COLUMNS = {
    "10Y": "美国国债收益率10年",
    "30Y": "美国国债收益率30年",
    "2Y": "美国国债收益率2年",
    "5Y": "美国国债收益率5年",
}


def _ensure_cache_dir():
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(region: str, tenor: str) -> Path:
    # Normalize tenor for filename (remove non-alphanumeric)
    safe_tenor = "".join(c if c.isalnum() else "_" for c in tenor)
    return _CACHE_DIR / f"bond_yield_{region}_{safe_tenor}.csv"


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age.total_seconds() < _CACHE_TTL_HOURS * 3600


def _fetch_cn_yield(tenor: str) -> pd.Series:
    """Fetch China government bond yield history from akshare."""
    if ak is None:
        raise ImportError("akshare not installed")
    df = ak.bond_zh_us_rate()
    if df is None or df.empty:
        raise ValueError("No data returned from akshare.bond_zh_us_rate()")
    col = _CN_TENOR_COLUMNS.get(tenor)
    if not col or col not in df.columns:
        raise ValueError(f"Tenor '{tenor}' not available in China bond yield data")
    # akshare returns columns: 日期, 中国国债收益率10年, 中国国债收益率30年, ...
    df["日期"] = pd.to_datetime(df["日期"])
    df = df.set_index("日期")[col]
    df = pd.to_numeric(df, errors="coerce").dropna()
    df.name = "yield"
    return df.sort_index()


def _fetch_us_yield_akshare(tenor: str) -> pd.Series:
    """Fetch US government bond yield history from akshare (FRED fallback)."""
    if ak is None:
        raise ImportError("akshare not installed")
    df = ak.bond_zh_us_rate()
    if df is None or df.empty:
        raise ValueError("No data returned from akshare.bond_zh_us_rate()")
    col = _US_AKSHARE_COLUMNS.get(tenor)
    if not col or col not in df.columns:
        raise ValueError(f"Tenor '{tenor}' not available in akshare US yield data")
    df["日期"] = pd.to_datetime(df["日期"])
    series = df.set_index("日期")[col]
    series = pd.to_numeric(series, errors="coerce").dropna()
    series.name = "yield"
    return series.sort_index()


def _fetch_us_yield(tenor: str) -> pd.Series:
    """Fetch US government bond yield history — FRED first, akshare fallback."""
    # Try FRED first
    if Fred is not None:
        api_key = os.environ.get("FRED_API_KEY")
        if api_key:
            try:
                fred = Fred(api_key=api_key)
                series_id = _US_TENOR_SERIES.get(tenor)
                if series_id:
                    series = fred.get_series(series_id)
                    if series is not None and not series.empty:
                        series = pd.to_numeric(series, errors="coerce").dropna()
                        series.name = "yield"
                        return series.sort_index()
            except Exception:
                pass
    # Fallback to akshare
    return _fetch_us_yield_akshare(tenor)


def get_yield_series(region: str, tenor: str) -> pd.Series:
    """
    Get historical yield series for a given region and tenor.
    Uses cache if fresh, otherwise fetches from source and updates cache.
    """
    region = region.lower()
    _ensure_cache_dir()
    cache_path = _cache_path(region, tenor)
    if _is_cache_fresh(cache_path):
        try:
            df = pd.read_csv(cache_path, parse_dates=["date"])
            df = df.set_index("date")["yield"]
            return df
        except Exception:
            # If cache corrupted, fall through to fetch
            pass

    # Fetch from source
    if region == "cn":
        series = _fetch_cn_yield(tenor)
    elif region == "us":
        series = _fetch_us_yield(tenor)
    else:
        raise ValueError(f"Unsupported region: {region}")

    # Cache
    out = pd.DataFrame({"date": series.index, "yield": series.values})
    out.to_csv(cache_path, index=False)
    return series


def get_yield_percentiles(
    region: str,
    tenor: str,
    current_val: Optional[float] = None,
) -> dict:
    """
    Compute yield percentiles over multiple windows (similar to utils/bond_yield.get_yield_percentiles).
    Returns dict with current value, windows (list of dicts), z_score, and metadata.
    """
    series = get_yield_series(region, tenor)
    if current_val is None:
        current_val = float(series.iloc[-1])

    from scipy.stats import percentileofscore

    windows = []
    for label, days in [("1Y", 252), ("3Y", 756), ("5Y", 1260), ("10Y", 2520)]:
        if days < len(series):
            w = series.iloc[-days:]
        else:
            w = series
        windows.append({
            "label": label,
            "p50": round(float(w.median()), 4),
            "pctile": round(float(percentileofscore(w, current_val)), 1),
        })
    # Full history
    windows.append({
        "label": "Full",
        "p50": round(float(series.median()), 4),
        "pctile": round(float(percentileofscore(series, current_val)), 1),
    })

    mean = float(series.mean())
    std = float(series.std()) or 1e-6
    z_score = round((current_val - mean) / std, 2)

    return {
        "region": region,
        "tenor": tenor,
        "current": current_val,
        "windows": windows,
        "z_score": z_score,
        "data_points": len(series),
        "date_range": {
            "start": str(series.index[0].date()),
            "end": str(series.index[-1].date()),
        },
    }


def get_yield_signal(region: str, tenor: str) -> dict:
    """
    Generate combined bond signal: yield percentile + z-score (similar to utils/bond_yield.get_yield_signal).
    Returns dict with signal classification.
    """
    p = get_yield_percentiles(region, tenor)
    full_pctile = p["windows"][-1]["pctile"]
    z = p["z_score"]

    if full_pctile > 90:
        signal = "BULLISH"
        reason = f"Yield at {full_pctile:.0f}th percentile — cheap"
    elif full_pctile > 75:
        signal = "ATTRACTIVE"
        reason = f"Yield at {full_pctile:.0f}th percentile — reasonably cheap"
    elif full_pctile > 25:
        signal = "NEUTRAL"
        reason = f"Yield at {full_pctile:.0f}th percentile — mid-range"
    elif full_pctile > 10:
        signal = "CAUTION"
        reason = f"Yield at {full_pctile:.0f}th percentile — below average"
    else:
        signal = "DANGER"
        reason = f"Yield at {full_pctile:.0f}th percentile — historically extreme"

    return {
        "signal": signal,
        "reason": reason,
        "yield_pct": p,
    }