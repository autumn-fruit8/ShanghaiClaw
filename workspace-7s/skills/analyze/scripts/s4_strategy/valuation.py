"""
valuation.py \u2014 PE & Dividend Yield percentile for 7S S2 enhancement.

All external API calls (akshare, yfinance) are delegated to market_service.
Calculations (percentiles, zones) are performed locally at skill level.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from utils.data_service.market_service import fetch_csi_pe, fetch_csi_index, fetch_ticker_info

WORKSPACE_ROOT = Path(__file__).resolve().parents[4]


# ─── Asset registry helpers ───────────────────────────────────────────────


def _get_asset_info(symbol: str) -> Optional[dict[str, Any]]:
    """Look up asset metadata from asset-master.json."""
    path = WORKSPACE_ROOT / "config" / "assets" / "asset-master.json"
    if not path.exists():
        return None
    with path.open() as f:
        master = json.load(f)
    for a in master.get("assets", []):
        if a["symbol"] == symbol:
            return a
    return None


def _resolve_price_index(tracks: str) -> Optional[str]:
    """Map from tracks to price index code. Returns the tracks code itself if
    it looks like a price index (6-digit, not TR prefix)."""
    tracks = tracks.strip()
    # TR codes start with 92 (total return) → map back to price
    # tr_mapping.json maps tracks→tr_code, we need reverse: tr_code→tracks
    res_dir = WORKSPACE_ROOT / "config" / "symbol_resolution"
    tr_map_path = res_dir / "tr_mapping.json"
    if tr_map_path.exists():
        with tr_map_path.open() as f:
            tr_map = json.load(f)
        # For each mapping, check if any tr_code matches tracks
        for price_code, entry in tr_map.items():
            if entry.get("tr_code") == tracks:
                return price_code
    # If tracks starts with 921/922 → it's a TR code; can't reverse-map
    # 932/93x are price indices, return as-is
    if tracks.startswith("921") or tracks.startswith("922"):
        return None  # Can't reverse-map
    # otherwise assume tracks IS the price index
    return tracks


def fetch_pe_percentile(symbol: str) -> Optional[dict[str, Any]]:
    """Get PE ratio and historical percentile for a CN asset via market_service.

    Raw PE data fetched via market_service.fetch_csi_pe().
    Percentile computed locally at skill level.
    """
    asset = _get_asset_info(symbol)
    if not asset:
        return None
    tracks = asset.get("tracks", "")
    region = asset.get("region", "CN")
    strategy_type = asset.get("strategy_type", "")
    if region != "CN":
        return None
    if strategy_type not in ("STEADY", "BOND"):
        return None
    price_index = _resolve_price_index(tracks) or tracks
    try:
        raw = fetch_csi_pe(price_index)
        if not raw or not raw.get("pe_values"):
            return None
        pe_vals = np.array(raw["pe_values"], dtype=float)
        current_pe = float(raw["current_pe"])
        lookback = min(2500, len(pe_vals))
        window = pe_vals[-lookback:]
        pctile = (window < current_pe).sum() / len(window) * 100
        years = round((pd.Timestamp(raw["dates"][-1]) - pd.Timestamp(raw["dates"][0])).days / 365.25, 1)
        return {
            "pe": round(current_pe, 1),
            "pe_pctile": round(pctile, 0),
            "pe_min": round(float(pe_vals.min()), 1),
            "pe_max": round(float(pe_vals.max()), 1),
            "pe_n": len(pe_vals),
            "pe_years": years,
        }
    except Exception:
        return None


def fetch_dividend_percentile(symbol: str) -> Optional[dict[str, Any]]:
    """Get dividend yield and historical percentile for a CN asset.

    Derives dividend yield from TR/Price divergence via market_service.
    """
    asset = _get_asset_info(symbol)
    if not asset:
        return None

    tracks = asset.get("tracks", "")
    region = asset.get("region", "CN")
    strategy_type = asset.get("strategy_type", "")

    if region != "CN":
        return None
    if strategy_type not in ("STEADY", "BOND"):
        return None

    # Find TR code for this symbol
    res_dir = WORKSPACE_ROOT / "config" / "symbol_resolution"
    tr_map_path = res_dir / "tr_mapping.json"
    csi_path = res_dir / "csi_patterns.json"
    tr_code = None
    price_code = None

    if tr_map_path.exists():
        with tr_map_path.open() as f:
            tr_map = json.load(f)
        if tracks in tr_map:
            tr_code = tr_map[tracks]["tr_code"]
            price_code = tracks

    if not tr_code and csi_path.exists():
        with csi_path.open() as f:
            csi_patterns = json.load(f)
        for prefix, info in csi_patterns.items():
            if tracks.startswith(prefix):
                tr_code = f"{tracks}{info['suffix']}"
                price_code = tracks
                break

    if not tr_code or not price_code:
        return None

    # Fetch both TR and price indices via market_service
    try:
        csi_data = fetch_csi_index(price_code, tr_code)
    except Exception:
        return None

    if csi_data is None or "price" not in csi_data or "tr" not in csi_data:
        return None

    pr_df = csi_data["price"].rename(columns={"close": "close_pr"})
    tr_df = csi_data["tr"].rename(columns={"close": "close_tr"})
    pr_df["日期"] = pd.to_datetime(pr_df["date"])
    tr_df["日期"] = pd.to_datetime(tr_df["date"])

    merged = pd.merge(tr_df[["日期", "close_tr"]], pr_df[["日期", "close_pr"]],
                      on="日期", how="inner").sort_values("日期")
    merged["div_ratio"] = merged["close_tr"].astype(float) / merged["close_pr"].astype(float)

    div_yields = []
    for i in range(252, len(merged)):
        r = merged["div_ratio"].iloc[i] / merged["div_ratio"].iloc[i - 252]
        div_yields.append((r - 1) * 100)

    if len(div_yields) < 20:
        return None

    arr = np.array(div_yields)
    lookback = min(2500, len(arr))
    window_arr = arr[-lookback:]
    pctile = (window_arr < float(arr[-1])).sum() / len(window_arr) * 100
    dates_arr = merged["日期"].values[252:]
    years = round((pd.Timestamp(dates_arr[-1]) - pd.Timestamp(dates_arr[0])).days / 365.25, 1)

    return {
        "div_yield": round(float(arr[-1]), 2),
        "div_pctile": round(pctile, 0),
        "div_min": round(float(arr.min()), 2),
        "div_max": round(float(arr.max()), 2),
        "div_n": len(arr),
        "div_years": years,
    }


def fetch_us_valuation(symbol: str) -> Optional[dict[str, Any]]:
    """Get PE ratio and dividend yield for US assets via market_service.

    Raw data fetched via market_service.fetch_ticker_info().
    Extraction/computation done locally at skill level.
    """
    import math
    asset = _get_asset_info(symbol)
    if not asset:
        return None
    region = asset.get("region", "")
    strategy_type = asset.get("strategy_type", "")
    if region != "US":
        return None
    if strategy_type not in ("STEADY", "BOND"):
        return None
    try:
        info = fetch_ticker_info(symbol)
        if not info:
            return None
        result: dict[str, Any] = {}
        pe = info.get("trailingPE") or info.get("forwardPE")
        if pe and math.isfinite(pe):
            result["pe"] = round(float(pe), 1)
        div = info.get("dividendYield")
        if div and math.isfinite(div):
            result["div_yield"] = round(float(div) * 100, 2)
        return result if result else None
    except Exception:
        return None


# ─── Unified entry point ──────────────────────────────────────────────────


def get_valuation(symbol: str) -> Optional[dict[str, Any]]:
    """Unified valuation fetch for any asset.

    Returns {
      "pe": float|None,
      "pe_pctile": float|None,     # percentile rank (0-100)
      "pe_years": float|None,      # history span
      "div_yield": float|None,     # percentage
      "div_pctile": float|None,
      "div_years": float|None,
    }
    """
    result = {}
    asset = _get_asset_info(symbol)
    if not asset:
        return None

    region = asset.get("region", "")

    if region == "CN":
        pe_data = fetch_pe_percentile(symbol)
        if pe_data:
            result["pe"] = pe_data["pe"]
            result["pe_pctile"] = pe_data["pe_pctile"]
            result["pe_years"] = pe_data["pe_years"]
        div_data = fetch_dividend_percentile(symbol)
        if div_data:
            result["div_yield"] = div_data["div_yield"]
            result["div_pctile"] = div_data["div_pctile"]
            result["div_years"] = div_data["div_years"]
    elif region == "US":
        us_data = fetch_us_valuation(symbol)
        if us_data:
            result.update(us_data)

    return result if result else None
