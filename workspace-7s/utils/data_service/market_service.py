"""
Market service — centralized data acquisition layer for all external APIs.

Provides:
  - fetch_ohlcv(symbol, region, adj_close)      → pd.DataFrame
  - fetch_total_return(symbol, region)          → pd.Series
  - fetch_close(symbol, region)                 → pd.Series
  - fetch_macro(series_id, region)              → pd.Series
  - fetch_csi_index(price_code, tr_code, ...)   → dict[str, pd.DataFrame]
  - fetch_csi_pe(price_code)                    → dict (raw PE time series)
  - fetch_ticker_info(symbol)                   → dict (raw yfinance.info)
  - bond yields delegated to bond_service.py

All external API calls (yfinance, akshare, FRED, Tiingo, requests)
reside here. Callers (data_resolver, s4_strategy, s3_system) delegate
to this module.  NO OTHER file should import yfinance, akshare, or
requests for market data.

All signal/domain calculations (percentiles, zones, ERP, valuation) are
performed at the skill level, NOT here.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

# Load .env for API keys (TIINGO, FINNHUB, etc.)
_env_path = Path(__file__).resolve().parents[2] / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        pass

# ── bond yields delegated to bond_service ──────────────────────────────
from .bond_service import get_yield_series, get_yield_percentiles, get_yield_signal


# =========================================================================
# Helper: resolve CN market prefix
# =========================================================================
def _cn_prefix(symbol: str) -> str:
    """Determine Shanghai/Shenzhen prefix for a CN symbol."""
    return "sh" if symbol.startswith(('56', '51', '6')) else "sz"


# =========================================================================
# OHLCV (date, open, high, low, close, volume[, adj_close])
# =========================================================================
def fetch_ohlcv(
    symbol: str,
    region: str,
    adj_close: bool = False,
) -> Optional[pd.DataFrame]:
    """Fetch daily OHLCV data.

    For CN: uses AkShare (Sina → EM fallback).
    For US: uses yfinance.

    When adj_close=True, adds an 'adj_close' column (yfinance Adj Close).
    Returns DataFrame with columns: date, open, high, low, close, volume[, adj_close].
    """
    region = region.lower()
    if region == "cn":
        import akshare as ak
        prefix = _cn_prefix(symbol)
        try:
            df = ak.fund_etf_hist_sina(symbol=f"{prefix}{symbol}")
        except Exception:
            df = ak.fund_etf_hist_em(
                symbol=symbol, period="daily",
                start_date="20000101",
                end_date=(date.today() + timedelta(days=1)).strftime("%Y%m%d"),
                adjust="qfq",
            )
            df = df.rename(columns={
                '日期': 'date', '开盘': 'open', '最高': 'high',
                '最低': 'low', '收盘': 'close', '成交量': 'volume',
            })
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
            cols = ['date', 'open', 'high', 'low', 'close', 'volume']
            return df[cols].dropna().sort_values('date').reset_index(drop=True)

        df = df.rename(columns={
            'date': 'date', 'open': 'open', 'high': 'high',
            'low': 'low', 'close': 'close', 'volume': 'volume',
        })
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        return df[cols].dropna().sort_values('date').reset_index(drop=True)

    elif region == "us":
        # Primary: yfinance
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="max", auto_adjust=not adj_close)
            if hist is not None and not hist.empty:
                df = hist.reset_index()
                df['date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
                cols = ['date', 'open', 'high', 'low', 'close', 'volume']
                if adj_close and 'Adj Close' in df.columns:
                    df = df.rename(columns={'Adj Close': 'adj_close'})
                    cols.append('adj_close')
                return df[cols].sort_values('date').reset_index(drop=True)
        except Exception:
            pass

        # Fallback: Tiingo (adj_close=adjClose, close=close, volume=volume)
        try:
            import requests, os
            api_key = os.environ.get("TIINGO_API_KEY", "")
            if api_key:
                end = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
                url = f"https://api.tiingo.com/tiingo/daily/{symbol}/prices?startDate=2000-01-01&endDate={end}&format=json"
                resp = requests.get(url, headers={
                    "Authorization": f"Token {api_key}", "Content-Type": "application/json",
                }, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        import numpy as np
                        df = pd.DataFrame({
                            "date": [d["date"][:10] for d in data],
                            "adj_close": pd.to_numeric([d.get("adjClose", d["close"]) for d in data], errors="coerce"),
                            "close": pd.to_numeric([d["close"] for d in data], errors="coerce"),
                            "volume": [d.get("volume", 0) or 0 for d in data],
                        }).sort_values("date").dropna(subset=["date", "adj_close", "close"])
                        cols = ['date', 'open', 'high', 'low', 'close', 'volume']
                        df['open'] = 0; df['high'] = 0; df['low'] = 0
                        if adj_close:
                            cols.append('adj_close')
                        return df[cols].reset_index(drop=True)
        except Exception:
            pass

        return None


# =========================================================================
# Total return (dividend-adjusted, normalized)
# =========================================================================
def fetch_total_return(symbol: str, region: str) -> Optional[pd.Series]:
    """Fetch dividend-adjusted total return series.

    CN chain: CSI TR index → OTC cumulative NAV → Sina/EM normalized.
    US:        yfinance Adj Close.
    Returns pd.Series with date index, name='total_return'.
    """
    region = region.lower()
    if region == "cn":
        return _fetch_cn_total_return(symbol)
    elif region == "us":
        # Primary: yfinance Adj Close
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="max")
            if hist is not None and not hist.empty and 'Adj Close' in hist.columns:
                series = hist['Adj Close'].dropna()
                series.name = 'total_return'
                return series.sort_index()
        except Exception:
            pass

        # Fallback: Tiingo adjClose
        try:
            import requests, os
            api_key = os.environ.get("TIINGO_API_KEY", "")
            if api_key:
                end = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
                url = f"https://api.tiingo.com/tiingo/daily/{symbol}/prices?startDate=2000-01-01&endDate={end}&format=json"
                resp = requests.get(url, headers={
                    "Authorization": f"Token {api_key}", "Content-Type": "application/json",
                }, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        first_close = float(data[0]["close"])
                        vals = []
                        dates = []
                        for d in data:
                            adj = d.get("adjClose", d["close"])
                            dates.append(d["date"][:10])
                            vals.append(float(adj))
                        import numpy as np
                        series = pd.Series(vals, index=dates, name='total_return')
                        return series.sort_index()
        except Exception:
            pass

        return None


def _fetch_cn_total_return(symbol: str) -> Optional[pd.Series]:
    """CN total return: CSI TR → OTC cum NAV → Sina normalized."""
    import akshare as ak
    from utils.normalize import close_to_total_return

    # Tier 1: try CSI TR through asset master
    try:
        from dao.asset_dao import AssetManifest
        am = AssetManifest()
        asset = next((a for a in am.get_all() if a.symbol == symbol), None)
        if asset:
            tracks = getattr(asset, 'tracks', None)
            provider = getattr(asset, 'cal_source', None)
            prov_name = provider.provider if provider else ""
            if tracks and '中证指数' in prov_name:
                tr_code = _resolve_tr_code(tracks)
                if tr_code:
                    cs = ak.stock_zh_index_hist_csindex(
                        symbol=tr_code, start_date='20000101',
                        end_date=(date.today() + timedelta(days=1)).strftime("%Y%m%d"),
                    )
                    if cs is not None and len(cs) > 0:
                        dates = [str(pd.Timestamp(d).strftime('%Y-%m-%d')) for d in cs['日期']]
                        vals = [round(float(v), 6) for v in cs['收盘']]
                        return pd.Series(vals, index=dates, name='total_return')
    except Exception:
        pass

    # Tier 2: OTC cumulative NAV
    try:
        nav = ak.fund_open_fund_info_em(symbol=symbol, indicator='累计净值走势')
        if nav is not None and len(nav) > 0:
            dates = [str(pd.Timestamp(d).strftime('%Y-%m-%d')) for d in nav['净值日期']]
            vals = [round(float(v), 6) for v in nav['累计净值']]
            return pd.Series(vals, index=dates, name='total_return')
    except Exception:
        pass

    # Tier 3: Sina/EM normalized close
    try:
        prefix = _cn_prefix(symbol)
        sina = ak.fund_etf_hist_sina(symbol=f"{prefix}{symbol}")
        closes = np.array([float(v) for v in sina['close'] if not np.isnan(float(v))])
        normalized = close_to_total_return(closes)
        dates = [str(d) for d in sina['date']]
        return pd.Series(normalized, index=dates, name='total_return')
    except Exception:
        pass

    return None


def _resolve_tr_code(tracks: str) -> Optional[str]:
    """Resolve tracks to CSI TR code via tr_mapping.json / csi_patterns.json."""
    try:
        res_dir = Path(__file__).resolve().parents[2] / "config" / "symbol_resolution"
        tr_map = json.loads((res_dir / "tr_mapping.json").read_text()) if (res_dir / "tr_mapping.json").exists() else {}
        csi_patterns = json.loads((res_dir / "csi_patterns.json").read_text()) if (res_dir / "csi_patterns.json").exists() else {}
        import json
        if tracks in tr_map:
            return tr_map[tracks]["tr_code"]
        if tracks.startswith(("H", "92")) or "CNY" in tracks:
            return tracks
        for prefix, info in csi_patterns.items():
            if tracks.startswith(prefix):
                return f"{tracks}{info['suffix']}"
        return tracks
    except Exception:
        return tracks


# =========================================================================
# Close price (raw, unadjusted)
# =========================================================================
def fetch_close(symbol: str, region: str) -> Optional[pd.Series]:
    """Fetch raw close price series.

    CN: Sina/EM → OTC unit NAV fallback.
    US: yfinance Close.
    Returns pd.Series with date index, name='close'.
    """
    region = region.lower()
    if region == "cn":
        import akshare as ak
        # Sina/EM
        try:
            prefix = _cn_prefix(symbol)
            df = ak.fund_etf_hist_sina(symbol=f"{prefix}{symbol}")
            series = pd.Series(df['close'].values, index=pd.to_datetime(df['date']), name='close')
            return series.sort_index()
        except Exception:
            pass
        # OTC unit NAV
        try:
            nav = ak.fund_open_fund_info_em(symbol=symbol, indicator='单位净值走势')
            if nav is not None and len(nav) > 0:
                series = pd.Series(nav['单位净值'].values, index=pd.to_datetime(nav['净值日期']), name='close')
                return series.sort_index()
        except Exception:
            pass
        return None
    elif region == "us":
        # Primary: yfinance Close
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="max")
            if hist is not None and not hist.empty and 'Close' in hist.columns:
                series = hist['Close'].dropna()
                series.name = 'close'
                return series.sort_index()
        except Exception:
            pass

        # Fallback: Tiingo close
        try:
            import requests, os
            api_key = os.environ.get("TIINGO_API_KEY", "")
            if api_key:
                end = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
                url = f"https://api.tiingo.com/tiingo/daily/{symbol}/prices?startDate=2000-01-01&endDate={end}&format=json"
                resp = requests.get(url, headers={
                    "Authorization": f"Token {api_key}", "Content-Type": "application/json",
                }, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        dates = [d["date"][:10] for d in data]
                        closes = [float(d["close"]) for d in data]
                        series = pd.Series(closes, index=dates, name='close')
                        return series.sort_index()
        except Exception:
            pass

        return None


# =========================================================================
# Macro data
# =========================================================================
def fetch_macro(series_id: str, region: str = "us") -> Optional[pd.Series]:
    """Fetch a macro-economic time series.

    For region='us': uses FRED API via fred_client.
    For region='cn': uses AkShare (if supported).
    """
    region = region.lower()
    if region == "us":
        try:
            from .fred_client_proxy import get_macro_series
            return get_macro_series(series_id)
        except Exception:
            return None
    elif region == "cn":
        try:
            import akshare as ak
            if series_id == "GDP":
                return ak.macro_china_gdp()
        except Exception:
            return None
    return None



# =========================================================================
# CSI index — price + total-return divergence data
# =========================================================================


def fetch_csi_index(
    price_code: str,
    tr_code: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Optional[dict[str, pd.DataFrame]]:
    """Fetch CSI price index and optionally total-return index.

    Returns dict with keys:
      - 'price': DataFrame(date, close) — price index history
      - 'tr'  : DataFrame(date, close)  — total-return index history (if tr_code provided)

    Used for dividend yield derivation via TR/Price divergence.
    """
    import akshare as ak

    result: dict[str, pd.DataFrame] = {}
    end = end_date or (date.today() + timedelta(days=1)).strftime("%Y%m%d")
    start = start_date or "20000101"

    # Price index
    try:
        df = ak.stock_zh_index_hist_csindex(
            symbol=price_code, start_date=start, end_date=end,
        )
        if df is not None and not df.empty:
            result["price"] = pd.DataFrame({
                "date": pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d"),
                "close": pd.to_numeric(df["收盘"], errors="coerce"),
            }).dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    except Exception:
        pass

    # TR index (optional)
    if tr_code and tr_code != price_code:
        try:
            df = ak.stock_zh_index_hist_csindex(
                symbol=tr_code, start_date=start, end_date=end,
            )
            if df is not None and not df.empty:
                result["tr"] = pd.DataFrame({
                    "date": pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d"),
                    "close": pd.to_numeric(df["收盘"], errors="coerce"),
                }).dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
        except Exception:
            pass

    return result if result else None


# =========================================================================
# File-level cache helpers
# =========================================================================

def _cache_dir() -> Path:
    """Resolve the shared system cache directory."""
    from config import CACHE_ROOT
    return CACHE_ROOT


_CACHE_TTL = timedelta(hours=4)


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < _CACHE_TTL


def _load_json_cache(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_json_cache(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, default=str))


# =========================================================================
# CSI PE — raw PE time series (CN indices)
# =========================================================================

def fetch_csi_pe(price_code: str) -> Optional[dict[str, Any]]:
    """Fetch raw PE time series for a CSI index.

    Returns dict with keys:
      - dates: list of date strings
      - pe_values: list of PE floats
      - current_pe: latest PE value
    or None if data unavailable.

    Cached to {CACHE_ROOT}/csi_pe_{price_code}.json with 4-hour TTL.
    """
    cache_path = _cache_dir() / f"csi_pe_{price_code}.json"

    if _is_cache_fresh(cache_path):
        cached = _load_json_cache(cache_path)
        if cached and cached.get("pe_values"):
            return cached

    import akshare as ak
    try:
        df = ak.stock_zh_index_hist_csindex(
            symbol=price_code,
            start_date="20040101",
            end_date=(date.today() + timedelta(days=1)).strftime("%Y%m%d"),
        )
    except Exception:
        if cache_path.exists():
            cached = _load_json_cache(cache_path)
            if cached and cached.get("pe_values"):
                return cached
        return None

    if df is None or df.empty or "滚动市盈率" not in df.columns:
        return None

    pe_series = df["滚动市盈率"].dropna()
    if len(pe_series) < 20:
        return None

    result: dict[str, Any] = {
        "dates": [str(d.date()) for d in pd.to_datetime(df["日期"].values[-len(pe_series):])],
        "pe_values": [float(v) for v in pe_series.values],
        "current_pe": float(pe_series.values[-1]),
    }
    _write_json_cache(cache_path, result)
    return result


# =========================================================================
# Ticker info — raw yfinance.info dict
# =========================================================================

def fetch_ticker_info(symbol: str) -> Optional[dict[str, Any]]:
    """Fetch raw ticker.info dict from yfinance.

    Returns the info dict as-is (trailingPE, forwardPE, dividendYield, etc.)
    or None if the fetch fails.

    Cached to {CACHE_ROOT}/ticker_{symbol}.json with 4-hour TTL.
    """
    cache_path = _cache_dir() / f"ticker_{symbol}.json"

    if _is_cache_fresh(cache_path):
        cached = _load_json_cache(cache_path)
        if cached:
            return cached

    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
    except Exception:
        if cache_path.exists():
            cached = _load_json_cache(cache_path)
            if cached:
                return cached
        return None

    if not info:
        return None

    result = dict(info)
    _write_json_cache(cache_path, result)
    return result


# ── Public API ──────────────────────────────────────────────────────────
__all__ = [
    'fetch_total_return',
    'fetch_close',
    'fetch_macro',
    'fetch_ohlcv',
    'fetch_csi_index',
    'fetch_csi_pe',
    'fetch_ticker_info',
    'get_yield_series',
    'get_yield_percentiles',
    'get_yield_signal',
]
