"""
US region daily update — incremental updates via market_service (primary) with Finnhub/Tiingo fallbacks.

Supported Asset Types:
- US_ETF: US Exchange Traded Funds (e.g., QQQM, USMV)
- HK_ETF: Hong Kong traded stocks/ETFs (e.g., 3110.HK)
"""

import os
import random
import time
import sys
from pathlib import Path
from dotenv import load_dotenv
import requests
import pandas as pd
from typing import Dict, Optional
from datetime import datetime

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_WORKSPACE_ROOT / ".env")
sys.path.insert(0, str(_WORKSPACE_ROOT))

from base.daily_update import DailyUpdateBase
from utils.constants import AssetType


class DailyUpdateUS(DailyUpdateBase):
    """Daily update service for US assets — market_service (primary) + Finnhub/Tiingo fallbacks."""

    def __init__(self, base_path: Optional[str] = None):
        if not base_path:
            runtime_root = os.getenv("SEVENS_RUNTIME_ROOT")
            base_path = str(Path(runtime_root).expanduser() / "knowledge" / "us") if runtime_root else str(_WORKSPACE_ROOT / "knowledge" / "us")
        super().__init__(region='US', base_path=base_path)

        self._finnhub_client = None
        self.yf = None  # kept for test compatibility

    def _load_finnhub_client(self):
        """Lazy-load Finnhub client from FINNHUB_API_KEY env var."""
        if self._finnhub_client is not None:
            return self._finnhub_client
        key = os.getenv("FINNHUB_API_KEY")
        if not key:
            self.logger.warning("FINNHUB_API_KEY not set — Finnhub fallback disabled")
            return None
        try:
            import finnhub
            self._finnhub_client = finnhub.Client(api_key=key)
            self.logger.info("Finnhub client loaded")
            return self._finnhub_client
        except ImportError:
            self.logger.warning("finnhub package not installed — fallback disabled")
            return None

    def _fetch_from_finnhub(self, symbol: str, last_date: str) -> pd.DataFrame:
        """Fetch latest price via Finnhub quote endpoint (free tier compatible)."""
        client = self._load_finnhub_client()
        if not client:
            return pd.DataFrame()
        try:
            from datetime import datetime, date
            csv_path = os.path.join(self.processed_dir, f"{symbol}.csv")
            has_existing_csv = os.path.exists(csv_path)
            
            # quote endpoint only provides current price, not historical
            # Use it to update the latest trading day for existing CSVs
            if not has_existing_csv:
                self.logger.warning(f"{symbol}: Finnhub quote cannot bootstrap history — requires yfinance/Yahoo Direct")
                return pd.DataFrame()
            
            # Read existing data to get baseline
            df_ex = pd.read_csv(csv_path)
            if df_ex.empty or 'total_return' not in df_ex.columns:
                return pd.DataFrame()
            last_return = float(df_ex['total_return'].iloc[-1])
            last_date_pd = pd.to_datetime(df_ex['date'].iloc[-1])
            
            # Get latest quote
            quote = client.quote(symbol)
            if not quote or quote.get('c') == 0:
                self.logger.warning(f"{symbol}: Finnhub quote returned no data")
                return pd.DataFrame()
            
            # Convert timestamp to date
            quote_date = pd.to_datetime(quote['t'], unit='s').normalize()
            current_price = quote['c']  # current price
            prev_close = quote['pc']    # previous close price
            
            # Only return if we have new data after last_date
            if quote_date <= last_date_pd:
                return pd.DataFrame()
            
            # Calculate pct_change from actual prices (current vs previous close)
            if prev_close == 0:
                return pd.DataFrame()
            pct_change = (current_price - prev_close) / prev_close
            new_total_return = last_return * (1 + pct_change)
            
            # Return prev_close as 'price' so _calculate_incremental_returns computes pct_change correctly
            # It will calculate: pct_change = (current_price - prev_close) / prev_close = correct!
            df_result = pd.DataFrame({
                'date': [quote_date],
                'price': [prev_close],    # previous close as price for pct_change calculation
                'close': [current_price],  # current price as close for refresh
            })
            self.logger.info(f"{symbol}: Finnhub updated — {quote_date.date()}: pct={pct_change*100:.2f}%, return={new_total_return:.4f}")
            return df_result
        except Exception as e:
            self.logger.warning(f"{symbol}: Finnhub fetch failed: {e}")
            return pd.DataFrame()

    def _fetch_from_yahoo_direct(self, symbol: str, start_date: str) -> pd.DataFrame:
        """Fetch daily adjusted-close history directly from Yahoo Finance chart API."""
        try:
            start_ts = int(pd.Timestamp(start_date).timestamp())
            end_ts = int(datetime.now().timestamp())
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            params = {
                'period1': start_ts, 'period2': end_ts, 'interval': '1d',
                'includePrePost': 'false', 'events': 'div,splits',
            }
            headers = {
                'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json,text/plain,*/*',
                'Referer': f'https://finance.yahoo.com/quote/{symbol}/history',
            }
            response = requests.get(url, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            payload = response.json()
            result = (payload.get('chart') or {}).get('result') or []
            if not result:
                return pd.DataFrame()
            node = result[0]
            timestamps = node.get('timestamp') or []
            indicators = node.get('indicators') or {}
            adjclose_list = (indicators.get('adjclose') or [{}])[0].get('adjclose') or []
            close_list = (indicators.get('quote') or [{}])[0].get('close') or []
            volume_list = (indicators.get('quote') or [{}])[0].get('volume') or []
            prices = adjclose_list if any(v is not None for v in adjclose_list) else close_list
            if not timestamps or not prices:
                return pd.DataFrame()
            limit = min(len(timestamps), len(prices), len(close_list), len(volume_list))
            close_prices = pd.to_numeric(close_list[:limit], errors='coerce')
            volumes = pd.to_numeric(volume_list[:limit], errors='coerce').clip(0)
            df_std = pd.DataFrame({
                'date': pd.to_datetime(timestamps[:limit], unit='s').normalize(),
                'price': pd.to_numeric(prices[:limit], errors='coerce'),
                'close': close_prices,
                'volume': [int(v) if v and not (isinstance(v, float) and v != v) else 0 for v in volumes],
            }).dropna(subset=['date', 'price', 'close'])
            if df_std.empty:
                return pd.DataFrame()
            df_std = self._filter_intraday_data(df_std)
            df_std = df_std[df_std['date'] >= pd.Timestamp(start_date)]
            return df_std[['date', 'price', 'close']].reset_index(drop=True)
        except Exception as e:
            self.logger.warning(f"{symbol}: direct Yahoo fetch failed: {e}")
            return pd.DataFrame()

    def _fetch_from_tiingo(self, symbol: str, start_date: str) -> pd.DataFrame:
        """Fetch price data from Tiingo as fallback when yfinance rate-limited.

        Returns adjClose as price (for total_return pct_change) and raw close
        as close (for position valuation).

        Tiingo free tier: 10,000 calls/day, 500 symbols — sufficient for ~12
        US assets with daily updates.
        """
        api_key = os.environ.get("TIINGO_API_KEY", "")
        if not api_key:
            return pd.DataFrame()

        end_date = datetime.now().strftime("%Y-%m-%d")
        url = (
            f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
            f"?startDate={start_date}&endDate={end_date}&format=json"
        )
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
                timeout=30,
            )
            if resp.status_code != 200:
                self.logger.warning(f"{symbol}: Tiingo HTTP {resp.status_code}")
                return pd.DataFrame()

            data = resp.json()
            if not data:
                return pd.DataFrame()

            df = pd.DataFrame({
                "date": pd.to_datetime([d["date"][:10] for d in data]),
                "price": pd.to_numeric([d["adjClose"] for d in data], errors="coerce"),
                "close": pd.to_numeric([d["close"] for d in data], errors="coerce"),
                "volume": [d.get("volume", 0) or 0 for d in data],
            }).sort_values("date").dropna(subset=["date", "price", "close"])

            df = df[df["date"] >= pd.Timestamp(start_date)]
            print(f"  ✅ Tiingo: {len(df)} rows for {symbol}")
            return df

        except Exception as e:
            self.logger.warning(f"{symbol}: Tiingo fetch failed: {e}")
            return pd.DataFrame()

    def _pre_execute_hook(self, asset_db: Dict) -> None:
        """Pre-load batch data from market_service (uses yfinance/Tiingo internally)."""
        from datetime import timedelta
        from utils.data_service.market_service import fetch_ohlcv
        self._yf_batch_cache: Dict[str, pd.DataFrame] = {}
        if not asset_db:
            return
        symbol_start_dates: Dict[str, str] = {}
        for symbol, asset_info in asset_db.items():
            csv_path = os.path.join(self.processed_dir, f"{symbol}.csv")
            if os.path.exists(csv_path):
                try:
                    df_ex = pd.read_csv(csv_path)
                    if not df_ex.empty:
                        last_date = pd.to_datetime(df_ex['date']).iloc[-1]
                        symbol_start_dates[symbol] = str(last_date.date())
                        continue
                except Exception:
                    pass
            bootstrap_start = asset_info.get('bootstrap_start_date')
            if not bootstrap_start:
                lookback_days = int(asset_info.get('bootstrap_lookback_days', 3650))
                bootstrap_start = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
            symbol_start_dates[symbol] = bootstrap_start
        if not symbol_start_dates:
            return
        # market_service handles batch internally, we just use per-symbol calls
        # (market_service already caches within a session)
        print(f"  Using market_service for {len(symbol_start_dates)} symbol(s)\n")

    def _fetch_from_yfinance(self, symbol: str, start_date: str,
                             attempt: int = 0, max_attempts: int = 3) -> pd.DataFrame:
        """Return price data for symbol from market_service (primary) with retry."""
        from utils.data_service.market_service import fetch_ohlcv
        try:
            df = fetch_ohlcv(symbol, "us", adj_close=True)
            if df is not None and not df.empty:
                close_col = "adj_close" if "adj_close" in df.columns else "close"
                df_std = pd.DataFrame({
                    'date': df['date'],
                    'price': pd.to_numeric(df[close_col], errors='coerce'),
                    'close': pd.to_numeric(df['close'], errors='coerce'),
                    'volume': pd.to_numeric(df.get('volume', 0), errors='coerce').fillna(0).astype(int).values,
                }).dropna(subset=['date', 'price', 'close'])
                if df_std.empty:
                    return pd.DataFrame()
                df_std = self._filter_intraday_data(df_std)
                df_std = df_std[df_std['date'] >= pd.Timestamp(start_date)]
                return df_std[['date', 'price', 'close', 'volume']]
        except Exception as e:
            error_msg = str(e)
            if attempt >= max_attempts - 1:
                raise RuntimeError(
                    f"market_service fetch failed after {attempt + 1} attempt(s): {error_msg[:120]}"
                )
            print(f"  ⚠️  API Error (attempt {attempt + 1}): {error_msg[:100]}")
            return self._fetch_from_yfinance(symbol, start_date, attempt + 1, max_attempts)
        return pd.DataFrame()

    def fetch_incremental_data(self, symbol: str, asset_info: Dict,
                               last_date: str) -> pd.DataFrame:
        """Fetch incremental data for US asset via yfinance."""
        from datetime import datetime, timedelta
        asset_type = asset_info.get('type', AssetType.US_ETF.value)
        bootstrap_mode = bool(asset_info.get('bootstrap_mode'))
        if asset_type not in (AssetType.US_ETF.value, AssetType.HK_ETF.value):
            print(f"  ⚠️  Unsupported asset type: {asset_type}")
            return pd.DataFrame()
        last_dt = datetime.strptime(last_date, '%Y-%m-%d')
        adjusted_start = (last_dt - timedelta(days=7)).strftime('%Y-%m-%d')
        if bootstrap_mode:
            df_boot = self._fetch_from_yahoo_direct(symbol, adjusted_start)
            if not df_boot.empty:
                return df_boot
            df_boot = self._fetch_from_finnhub(symbol, adjusted_start)
            if not df_boot.empty:
                return df_boot
        try:
            df = self._fetch_from_yfinance(symbol, adjusted_start)
            if not df.empty:
                return df
            # yfinance empty → try Tiingo
            self.logger.info(f"{symbol}: yfinance empty, falling back to Tiingo...")
            print(f"  ↩️  Tiingo fallback for {symbol}...")
            df = self._fetch_from_tiingo(symbol, adjusted_start)
            if not df.empty:
                return df
            # Tiingo empty → try existing fallbacks
            self.logger.warning(f"{symbol}: Tiingo empty — trying direct Yahoo...")
            df = self._fetch_from_yahoo_direct(symbol, adjusted_start)
            if not df.empty:
                return df
            return self._fetch_from_finnhub(symbol, adjusted_start)
        except RuntimeError as e:
            msg = str(e)
            if any(kw in msg for kw in ('Rate limit', 'Too Many Requests', '429', 'RateLimit')):
                self.logger.warning(f"{symbol}: yfinance rate-limited, falling back to Tiingo...")
                print(f"  ↩️  Tiingo fallback for {symbol} (rate limited)...")
                df = self._fetch_from_tiingo(symbol, adjusted_start)
                if not df.empty:
                    return df
                # Tiingo also failed → deeper fallbacks
                self.logger.warning(f"{symbol}: Tiingo also failed, trying direct Yahoo...")
                df = self._fetch_from_yahoo_direct(symbol, adjusted_start)
                if not df.empty:
                    return df
                return self._fetch_from_finnhub(symbol, adjusted_start)
            raise
