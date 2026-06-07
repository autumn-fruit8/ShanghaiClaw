"""
CN region daily update — incremental updates via market_service (primary) with CSI TR + Sina/EM fallbacks.

Supported Asset Types:
- CN_ETF: Chinese ETFs (e.g. 159925 = 创业板ETF)
- CN_OTC: OTC mutual funds (e.g. 007751, 012708)
"""

import os
import sys
from pathlib import Path
from typing import Dict, Optional
import pandas as pd

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_WORKSPACE_ROOT))

from base.daily_update import DailyUpdateBase
from utils.constants import AssetType


class DailyUpdateCN(DailyUpdateBase):
    """Daily update service for CN assets — market_service (primary) + CSI TR + Sina/EM fallbacks."""

    def __init__(self, base_path: Optional[str] = None):
        if not base_path:
            runtime_root = os.getenv("SEVENS_RUNTIME_ROOT")
            base_path = str(Path(runtime_root).expanduser() / "knowledge" / "cn") if runtime_root else str(_WORKSPACE_ROOT / "knowledge" / "cn")
        super().__init__(region='CN', base_path=base_path)

    def _sina_etf_symbol(self, symbol: str) -> str:
        """Convert a bare CN ETF/index symbol to Sina Finance format."""
        s = symbol.strip()
        if s.startswith(('159', '562', '1')):
            return f"sz{s}"
        if s.startswith(('560', '561', '51', '6', '000', '399')):
            return f"sh{s}"
        return f"sz{s}"

    def _fetch_from_api(self, symbol: str, asset_type: str, start_date: str,
                        attempt: int = 0, max_attempts: int = 3) -> pd.DataFrame:
        """Fetch price data from market_service with retry logic."""
        from utils.data_service.market_service import fetch_ohlcv, fetch_total_return
        try:
            self._apply_anti_ban_delay(attempt)

            if asset_type in (AssetType.CN_ETF.value, 'CN_INDEX'):
                print(f"  🔍 Fetching {asset_type} data for {symbol} via market_service...")
                ohlcv_df = fetch_ohlcv(symbol, "cn")
                if ohlcv_df is not None and not ohlcv_df.empty:
                    df_std = pd.DataFrame({
                        'date': ohlcv_df['date'],
                        'price': ohlcv_df['close'],
                        'close': ohlcv_df['close'],
                        'volume': ohlcv_df.get('volume', 0).fillna(0).astype(int).values,
                    }).dropna(subset=['date', 'price'])
                    start_ts = pd.Timestamp(start_date)
                    df_std = df_std[df_std['date'] >= start_ts]
                    df_std = self._filter_intraday_data(df_std)
                    return df_std[['date', 'price', 'close']]

            elif asset_type == 'CN_OTC':
                print(f"  🔍 Fetching CN_OTC NAV for {symbol} via market_service...")
                tr_series = fetch_total_return(symbol, "cn")
                if tr_series is not None and not tr_series.empty:
                    df_std = pd.DataFrame({
                        'date': [str(d) for d in tr_series.index],
                        'price': tr_series.values,
                        'close': tr_series.values,
                    }).dropna(subset=['date', 'price'])
                    return df_std[['date', 'price', 'close']]
            else:
                return pd.DataFrame()

        except Exception as e:
            error_msg = str(e)
            if attempt < max_attempts - 1:
                print(f"  ⚠️  API Error (attempt {attempt + 1}): {error_msg[:100]}")
                return self._fetch_from_api(symbol, asset_type, start_date,
                                            attempt + 1, max_attempts)
            else:
                raise RuntimeError(
                    f"CN {asset_type} fetch failed after {max_attempts} attempts: {error_msg[:120]}"
                )

    def fetch_incremental_data(self, symbol: str, asset_info: Dict,
                               last_date: str) -> pd.DataFrame:
        """Fetch incremental data for CN asset, with CSI TR + ETF-first fallback."""
        from datetime import datetime, timedelta

        last_dt = datetime.strptime(last_date, '%Y-%m-%d')
        adjusted_start = (last_dt - timedelta(days=7)).strftime('%Y-%m-%d')

        asset_type = asset_info.get('type', 'CN_ETF')
        fallback_index = asset_info.get('fallback_index')
        
        # ── CSI Total Return API (preferred) ─────────────────────────────
        tr_index = asset_info.get('tr_index')
        provider = asset_info.get('cal_source', {}).get('provider', '')
        use_csi = tr_index and '中证指数' in provider
        
        if use_csi:
            try:
                print(f"  🔍 Fetching CSI TR data for {symbol} (index: {tr_index}) via market_service...")
                from utils.data_service.market_service import fetch_csi_index, fetch_ohlcv, fetch_total_return
                csi_data = fetch_csi_index(tr_index, tr_index, adjusted_start.replace('-', ''), datetime.now().strftime('%Y%m%d'))
                if csi_data and "price" in csi_data:
                    cs = csi_data["price"].copy()
                    cs['price'] = pd.to_numeric(cs['close'], errors='coerce')
                    cs = cs.dropna(subset=['price']).sort_values('date')
                    
                    # Get close from Sina/EM via market_service
                    if asset_type == 'CN_OTC':
                        tr = fetch_total_return(symbol, "cn")
                        close_map = {str(d): float(v) for d, v in zip(tr.index, tr.values)} if tr is not None else {}
                    else:
                        close_map = {}
                        try:
                            ohlcv = fetch_ohlcv(symbol, "cn")
                            if ohlcv is not None and not ohlcv.empty:
                                close_map = {str(d): float(c) for d, c in zip(ohlcv['date'], ohlcv['close'])}
                        except Exception:
                            pass
                    
                    cs['close'] = cs['date'].astype(str).map(close_map)
                    cs = cs[cs['date'] >= pd.Timestamp(adjusted_start)].reset_index(drop=True)
                    cs = self._filter_intraday_data(cs)
                    
                    if not cs.empty:
                        cols = ['date', 'price', 'close']
                        if 'volume' in cs.columns:
                            cols.append('volume')
                        return cs[cols]
                    print(f"  ⚠️  CSI TR returned no rows after start date")
            except Exception as e:
                print(f"  ⚠️  CSI TR API failed, falling back to market_service OHLCV: {str(e)[:80]}")

        # ── Fallback to market_service ──
        if asset_type == 'CN_ETF':
            df = self._fetch_from_api(symbol, 'CN_ETF', adjusted_start)
            if df.empty and fallback_index:
                print(f"  ↩️  Falling back to CN_INDEX: {fallback_index}")
                df = self._fetch_from_api(fallback_index, 'CN_INDEX', adjusted_start)
            return df

        elif asset_type == 'CN_INDEX':
            return self._fetch_from_api(symbol, 'CN_INDEX', adjusted_start)

        elif asset_type == 'CN_OTC':
            return self._fetch_from_api(symbol, 'CN_OTC', adjusted_start)

        else:
            print(f"  ⚠️  Unsupported asset type: {asset_type}")
            return pd.DataFrame()
