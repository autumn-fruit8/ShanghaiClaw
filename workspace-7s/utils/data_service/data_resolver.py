"""Data resolution — 2-tier cascade for price CSV resolution.

Single source of truth for:
    - adhoc/cache → knowledge/3_processed → bootstrap
    - CSI TR bootstrap (CN)
    - Sina/EM close fallback (CN)
    - Cache append-only refresh

Usage:
   from utils.data_service.data_resolver import resolve_price_data, bootstrap_csv

   df = resolve_price_data(workspace_root, "159207", "cn")
   if df is None:
       asset = AssetManifest().get("159207")
       bootstrap_csv(workspace_root, "159207", asset, "cn")
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import date, timedelta, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.normalize import close_to_total_return


# ---------------------------------------------------------------------------
# Simple resolution (read-only, no API calls)
# ---------------------------------------------------------------------------

CANDIDATE_PATHS = [
    "adhoc/cache/{symbol}.csv",
    "knowledge/{region}/3_processed/{symbol}.csv",
]


def resolve_price_data(workspace_root: Path, symbol: str, region: str) -> pd.DataFrame | None:
    """2-tier cascade: 1) adhoc/cache 2) knowledge/3_processed.

    Cache-first resolution per Rule 1: adhoc/cache/ is the SSOT for all symbols.
    knowledge/{region}/3_processed/ is fallback (mainly for active assets).

    Returns DataFrame with at least 'date' and 'total_return' columns, or None.
    No API calls — read-only.
    Ensures standard 4-column format (date, total_return, close, volume).
    """
    for pattern in CANDIDATE_PATHS:
        path = workspace_root / pattern.format(region=region, symbol=symbol)
        if path.exists():
            try:
                df = pd.read_csv(path)
                if "date" not in df.columns:
                    df.columns = df.columns.str.lower()
                if "date" in df.columns:
                    # Ensure standard 4 columns
                    for col in ['total_return', 'close', 'volume']:
                        if col not in df.columns:
                            df[col] = 0
                    return df
            except Exception:
                pass
    return None


def resolve_data_for_symbols(
    workspace_root: Path,
    symbols: list[str],
    region: str,
) -> dict[str, pd.DataFrame]:
    """Batch resolve CSVs for multiple symbols."""
    result: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = resolve_price_data(workspace_root, sym, region)
        if df is not None:
            result[sym] = df
    return result


# ---------------------------------------------------------------------------
# Bootstrap (full history, first-time fetch via API)
# ---------------------------------------------------------------------------

def _fetch_close(symbol: str) -> pd.DataFrame | None:
    """Fetch close price via market_service."""
    from .market_service import fetch_close, fetch_ohlcv
    # Try Sina/EM first
    try:
        ohlcv = fetch_ohlcv(symbol, 'cn')
        if ohlcv is not None and not ohlcv.empty:
            return ohlcv[['date', 'close']].dropna()
    except Exception:
        pass
    # Fallback: close-specific fetch
    cs = fetch_close(symbol, 'cn')
    if cs is not None and not cs.empty:
        return cs.reset_index().rename(columns={'index': 'date'})
    return None


def _resolve_tr_code(asset: Any) -> str | None:
    """Resolve CSI TR code for an asset."""
    tracks = getattr(asset, 'tracks', None)
    provider = getattr(asset, 'cal_source', None)
    prov_name = provider.provider if provider else ""
    if not tracks or '中证指数' not in prov_name:
        return None
    res_dir = Path(__file__).resolve().parents[2] / "config" / "symbol_resolution"
    tr_map = json.loads((res_dir / "tr_mapping.json").read_text()) if (res_dir / "tr_mapping.json").exists() else {}
    csi_patterns = json.loads((res_dir / "csi_patterns.json").read_text()) if (res_dir / "csi_patterns.json").exists() else {}
    if tracks in tr_map:
        return tr_map[tracks]["tr_code"]
    if tracks.startswith(("H", "92")) or "CNY" in tracks:
        return tracks
    for prefix, info in csi_patterns.items():
        if tracks.startswith(prefix):
            return f"{tracks}{info['suffix']}"
    return tracks


def bootstrap_csv(
    workspace_root: Path,
    symbol: str,
    asset: Any | None,
    region: str,
    cache_dir: Path | None = None,
) -> Path | None:
    """Bootstrap a CSV for a non-active asset using CSI TR or Sina/EM chain.

    Resolution order:
      1. CSI TR index for total_return, plus Sina/EM for close column
      2. CN_OTC (累计净值, provides both columns)
      3. Sina/EM chain (raw close for both columns, fallback)

    Returns path to the cached CSV, or None on failure.
    """
    from dao.asset_dao import AssetManifest

    cache_dir = cache_dir or (workspace_root / "adhoc" / "cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{symbol}.csv"

    end_date_str = (date.today() + timedelta(days=1)).strftime("%Y%m%d")

    if asset is None:
        am = AssetManifest()
        matches = [a for a in am.get_all() if a.symbol.upper() == symbol.upper()]
        asset = matches[0] if matches else None

    asset_type = getattr(asset, 'asset_type', None)
    if asset_type is not None:
        asset_type = getattr(asset_type, 'value', str(asset_type))
    provider = getattr(asset, 'cal_source', None)
    prov_name = provider.provider if provider else ""

    # Tier 1: CSI TR index (delegated to market_service.fetch_total_return)
    tr_series = None
    try:
        from .market_service import fetch_total_return
        tr_series = fetch_total_return(symbol, 'cn')
    except Exception as e:
        print(f"  [Bootstrap] {symbol}: total_return fetch failed ({e})")

    if tr_series is not None and len(tr_series) > 0:
        tr_dates = list(tr_series.index)
        tr_vals = [round(float(v), 6) for v in tr_series.values]

        df_tr = pd.DataFrame({'date': tr_dates, 'total_return': tr_vals})
        df_close = _fetch_close(symbol)
        if df_close is not None:
            df_close['date'] = df_close['date'].astype(str).str.strip()
            df_tr['date'] = df_tr['date'].astype(str).str.strip()
            merged = pd.merge(df_tr, df_close, on='date', how='left')
        else:
            merged = df_tr.copy()
            merged['close'] = merged['total_return']
        if merged['close'].isna().any():
            merged['close'] = merged['close'].fillna(merged['total_return'])
        merged = merged.dropna(subset=['total_return']).drop_duplicates(subset=['date'])
        merged = merged.sort_values('date').reset_index(drop=True)
        if len(merged) < 10:
            if cache_path.exists():
                return cache_path
            return None
        merged['volume'] = 0
        merged = merged[['date', 'total_return', 'close', 'volume']]
        merged.to_csv(cache_path, index=False)
        return cache_path

    # Tier 2: CN_OTC (累计净值 via market_service.fetch_total_return)
    if asset_type == 'CN_OTC' and tr_series is None:
        try:
            from .market_service import fetch_total_return
            tr_series = fetch_total_return(symbol, 'cn')
        except Exception:
            pass
    if tr_series is not None:
        out = pd.DataFrame({'date': list(tr_series.index), 'total_return': [round(float(v), 6) for v in tr_series.values]})
        out['close'] = out['total_return']
        out['volume'] = 0
        out = out.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
        out = out[['date', 'total_return', 'close', 'volume']]
        out.to_csv(cache_path, index=False)
        return cache_path

    # ── US/HK ROUTE: yfinance only ──
    if asset_type in ('US_ETF', 'HK_ETF'):
        try:
            from .market_service import fetch_ohlcv
            df = fetch_ohlcv(symbol, 'us', adj_close=True)
            if df is not None and not df.empty and 'adj_close' in df.columns:
                dates = list(df['date'])
                adj_prices = pd.to_numeric(df['adj_close'], errors='coerce').values
                raw_prices = pd.to_numeric(df['close'], errors='coerce').values
                volumes = pd.to_numeric(df['volume'], errors='coerce').fillna(0).astype(int).values
                valid_mask = ~np.isnan(adj_prices) & ~np.isnan(raw_prices)
                if valid_mask.any():
                    first_valid = adj_prices[valid_mask][0]
                    if first_valid > 0:
                        total_returns = adj_prices / first_valid
                        rows = [{
                            'date': d,
                            'total_return': round(float(tr), 6),
                            'close': round(float(c), 6),
                            'volume': int(v),
                        } for d, tr, c, v in zip(dates, total_returns, raw_prices, volumes)
                          if not np.isnan(tr) and not np.isnan(c)]
                        out = pd.DataFrame(rows).dropna(subset=['date', 'total_return'])
                        out = out.drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
                        out = out[['date', 'total_return', 'close', 'volume']]
                        if len(out) >= 10:
                            out.to_csv(cache_path, index=False)
                            return cache_path
        except Exception as e:
            print(f"  [Bootstrap] {symbol}: fetch_ohlcv(adj_close=True) failed ({e})")
        # US/HK route: no fallback to CN data sources
        if cache_path.exists():
            return cache_path
        return None

    # ── CN ROUTE below ──
    # (CSI TR handled above; CN_OTC handled above; this is Sina/EM fallback)
    # Tier 3: Sina/EM chain (CN ETF fallback)
    from .market_service import fetch_ohlcv

    try:
        ohlcv = fetch_ohlcv(symbol, 'cn')
        if ohlcv is None or ohlcv.empty:
            raise ValueError("fetch_ohlcv returned no data")
        dates = list(ohlcv['date'])
        vals = list(ohlcv['close'])
        vol_list = list(ohlcv['volume'].fillna(0).astype(int)) if 'volume' in ohlcv.columns else None
    except Exception:
        try:
            # EM fallback (fetch_ohlcv also covers EM)
            from .market_service import fetch_close
            cs = fetch_close(symbol, 'cn')
            if cs is None or cs.empty:
                if cache_path.exists():
                    return cache_path
                return None
            # fetch_close returns Series; convert to DataFrame
            dates = [str(d.date()) for d in cs.index]
            vals = list(cs.values)
            vol_list = [0] * len(vals)
        except Exception:
            if cache_path.exists():
                return cache_path
            return None
    closes = np.array([float(v) for v in vals if not np.isnan(float(v))])
    normalized = close_to_total_return(closes)
    rows = [{'date': str(pd.Timestamp(d).strftime('%Y-%m-%d')), 'total_return': round(float(tr), 6),
             'close': float(c), 'volume': int(vol) if vol_list else 0}
            for d, tr, c, vol in zip(dates, normalized, closes, vol_list or [0]*len(dates))]
    out = pd.DataFrame(rows).drop_duplicates(subset=['date']).sort_values('date').reset_index(drop=True)
    out = out[['date', 'total_return', 'close', 'volume']]
    out.to_csv(cache_path, index=False)
    return cache_path


# ---------------------------------------------------------------------------
# Cache refresh (append-only, no history overwrite)
# ---------------------------------------------------------------------------

def refresh_cache(symbol: str, asset: Any | None, cache_path: Path) -> None:
    """Append new trading days to an existing cache CSV. Never overwrites history.

    Exclusive routing by asset type:
      - US/HK ETFs → yfinance only
      - CN assets → CSI TR → Sina/EM
    """
    existing = pd.read_csv(cache_path)
    if existing.empty or "date" not in existing.columns:
        return

    existing["date"] = pd.to_datetime(existing["date"])
    iso_date = existing["date"].max()
    last_date_ymd = iso_date.strftime("%Y%m%d")
    last_date_str = iso_date.strftime("%Y-%m-%d")
    today_str = (date.today() + timedelta(days=1)).strftime("%Y%m%d")

    # Determine asset type for routing
    asset_type = getattr(asset, 'asset_type', None)
    if asset_type is not None:
        asset_type = getattr(asset_type, 'value', str(asset_type))

    new_rows = None

    # ── US/HK ROUTE: yfinance only ──
    if asset_type in ('US_ETF', 'HK_ETF'):
        try:
            from .market_service import fetch_ohlcv
            df = fetch_ohlcv(symbol, 'us', adj_close=True)
            if df is not None and not df.empty and 'adj_close' in df.columns:
                dates = pd.to_datetime(df['date'])
                adj_closes = pd.to_numeric(df['adj_close'], errors='coerce')
                raw_closes = pd.to_numeric(df['close'], errors='coerce')
                new_data = []
                last_tr = float(existing['total_return'].iloc[-1])
                last_adj = float(existing['close'].iloc[-1]) if not existing['close'].isna().iloc[-1] else None
                for d, adj, close in zip(dates, adj_closes, raw_closes):
                    if np.isnan(adj) or np.isnan(close):
                        continue
                    d_ts = pd.Timestamp(d)
                    if d_ts <= iso_date:
                        continue
                    if last_adj is not None and last_adj > 0:
                        pct = (adj - last_adj) / last_adj
                        tr = last_tr * (1 + pct)
                    else:
                        tr = adj
                    new_data.append({
                        'date': d_ts.strftime("%Y-%m-%d"),
                        'total_return': round(float(tr), 6),
                        'close': round(float(close), 6),
                    })
                    last_tr = tr
                    last_adj = adj
                if new_data:
                    new_rows = pd.DataFrame(new_data)
        except Exception as e:
            print(f"  [Refresh] {symbol}: yfinance failed ({e})")
    # ── CN ROUTE: CSI TR → Sina/EM ──
    else:
        tr_code = _resolve_tr_code(asset) if asset else None
        if tr_code:
            try:
                from .market_service import fetch_total_return
                tr = fetch_total_return(symbol, 'cn')
                if tr is not None and len(tr) > 1:
                    tr_df = tr.reset_index()
                    tr_df.columns = ['date', 'total_return']
                    tr_df['date'] = pd.to_datetime(tr_df['date'])
                    new_df = tr_df[tr_df['date'] > iso_date][['date', 'total_return']].copy()
                    if not new_df.empty:
                        new_df["close"] = new_df["total_return"]
                        new_df["date"] = new_df["date"].dt.strftime("%Y-%m-%d")
                        new_rows = new_df[["date", "total_return", "close"]]
            except Exception:
                pass

        if new_rows is None or new_rows.empty:
            try:
                from .market_service import fetch_ohlcv
                ohlcv = fetch_ohlcv(symbol, 'cn')
                if ohlcv is not None and not ohlcv.empty:
                    ohlcv["date"] = pd.to_datetime(ohlcv["date"])
                    new_df = ohlcv[ohlcv["date"] > iso_date][["date", "close"]].copy()
                    if not new_df.empty:
                        new_df["total_return"] = new_df["close"]
                        new_df["date"] = new_df["date"].dt.strftime("%Y-%m-%d")
                        new_rows = new_df[["date", "total_return", "close"]]
            except Exception:
                pass

    if new_rows is not None and not new_rows.empty:
        combined = pd.concat([existing, new_rows], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
        combined.to_csv(cache_path, index=False)
        print(f"  [Refresh] {symbol}: cache now has {len(combined)} rows")


# ---------------------------------------------------------------------------
# Batch resolve for pipeline (adhoc mode)
# ---------------------------------------------------------------------------

def batch_resolve_for_adhoc(
    workspace_root: Path,
    runtime_root: Path,
    region: str,
    expected_symbols: set[str] | None = None,
) -> int:
    """Resolve CSVs for adhoc analyze — cache-first with knowledge fallback.

    Resolution order (cache-first, per Rule 1):
      1. adhoc/cache/{sym}.csv          ← SSOT for ALL symbols
         - exists & fresh (≤3 trading days) → use directly
         - exists & stale → refresh_cache (API append-only)
         - missing → continue to tier 2
      2. knowledge/{region}/3_processed/{sym}.csv  ← active-only fallback
         - if active & exists → copy to adhoc/cache, then use
         - if non-active → skip (shouldn't be here after cleanup)
      3. bootstrap via API → adhoc/cache/{sym}.csv  ← last resort
    """
    from dao.asset_dao import AssetManifest
    from utils.constants import Region

    proc_dir = workspace_root / "knowledge" / region.lower() / "3_processed"
    cache_dir = workspace_root / "adhoc" / "cache"
    adhoc_proc = runtime_root / "knowledge" / region.lower() / "3_processed"

    if adhoc_proc.exists():
        shutil.rmtree(adhoc_proc)
    adhoc_proc.mkdir(parents=True, exist_ok=True)

    am = AssetManifest()
    all_assets = {a.symbol: a for a in am.get_by_region(Region[region.upper()])}
    target_symbols = expected_symbols if expected_symbols else set(all_assets.keys())

    # Load active symbol set for Tier 2 gate
    active_symbols: set[str] = set()
    try:
        states_dir = workspace_root / "config" / "states"
        active_path = states_dir / "active.json"
        if active_path.exists():
            active_data = json.loads(active_path.read_text())
            active_symbols = {a["symbol"] for a in active_data.get("assets", [])}
    except Exception:
        pass

    resolved = 0
    for sym in target_symbols:
        asset = all_assets.get(sym)
        found = None

        # ── Tier 1: adhoc/cache/ (SSOT for ALL symbols) ──
        cache_path = cache_dir / f"{sym}.csv"
        if cache_path.exists():
            try:
                existing = pd.read_csv(cache_path)
                if not existing.empty and "date" in existing.columns:
                    existing["date"] = pd.to_datetime(existing["date"])
                    last_date = existing["date"].max()
                    # Stale threshold: > 3 trading days old
                    days_stale = (pd.Timestamp.now() - last_date).days
                    if days_stale <= 3:
                        found = cache_path
                    else:
                        print(f"  [Refresh] {sym}: cache stale ({last_date.date()}), refreshing...")
                        try:
                            refresh_cache(sym, asset, cache_path)
                        except Exception as e:
                            print(f"  [WARN] {sym}: cache refresh failed ({e}), using stale data")
                        found = cache_path
            except Exception as e:
                print(f"  [WARN] {sym}: cache read failed ({e}), falling back")
                # Fall through to Tier 2

        # ── Tier 2: knowledge/3_processed/ (active-only fallback) ──
        if not found and sym in active_symbols:
            active_path = proc_dir / f"{sym}.csv"
            if active_path.exists():
                found = active_path
                # Copy to cache so future resolutions use cache-first
                if not cache_path.exists():
                    shutil.copy2(found, cache_path)
                    print(f"  [Copy] {sym}: knowledge → adhoc/cache")
        elif not found and sym not in active_symbols:
            print(f"  [Tier2] {sym}: not active, skipping knowledge/ (will bootstrap)")

        # ── Tier 3: bootstrap via API ──
        if not found and asset:
            print(f"  [Bootstrap] {sym}: no cached CSV, bootstrapping...")
            try:
                result = bootstrap_csv(workspace_root, sym, asset, region.lower(), cache_dir)
                if result:
                    found = result
            except Exception as e:
                print(f"  [SKIP] {sym}: bootstrap failed: {e}")
                continue

        if found:
            shutil.copy2(found, adhoc_proc / f"{sym}.csv")
            resolved += 1
        elif sym not in all_assets:
            print(f"  [SKIP] {sym}: not found in asset master")

    print(f"Resolved {resolved} CSVs for adhoc analyze -> {adhoc_proc}")
    return resolved
