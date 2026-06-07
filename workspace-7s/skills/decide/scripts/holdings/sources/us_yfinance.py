"""US ETF holdings via yfinance.

Primary source for US ETFs.
Uses yfinance.Ticker(symbol).funds_data.top_holdings.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Optional

import yfinance as yf

from dao.models import Holding, HoldingsData

logger = logging.getLogger(__name__)

_MAX_RETRIES = 1
_RETRY_DELAY_S = 2


def fetch_top_holdings(symbol: str, top_n: int = 10) -> Optional[HoldingsData]:
    """Fetch top-N holdings for a US ETF via yfinance.

    Retries once on rate-limit (YFRateLimitError).
    Returns None if data is unavailable (commodity ETF, non-fund, rate-limited).
    """
    for attempt in range(_MAX_RETRIES + 1):
        try:
            ticker = yf.Ticker(symbol)
            fd = ticker.funds_data
            if fd is None:
                logger.info("%s: funds_data unavailable (may not be a fund)", symbol)
                return None

            # Try top_holdings first (preferred), fall back to equity_holdings
            try:
                raw = fd.top_holdings
            except Exception:
                raw = None

            if raw is None:
                try:
                    raw = fd.equity_holdings
                except Exception:
                    raw = None

            if raw is None:
                logger.info("%s: no holdings data from yfinance", symbol)
                return None

            # yfinance returns a DataFrame with columns like ['Symbol', 'Name', '% Assets'] etc.
            holdings = _parse_yfinance_df(symbol, raw, top_n)
            return holdings

        except Exception as e:
            err_name = type(e).__name__
            logger.warning("%s: yfinance attempt %d failed: %s", symbol, attempt + 1, err_name)
            if "RateLimited" in err_name or "rate" in str(e).lower():
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY_S)
                    continue
            return None

    return None


def _parse_yfinance_df(etf_symbol: str, df, top_n: int) -> HoldingsData:
    """Parse yfinance DataFrame into HoldingsData, then keep top N.

    yfinance returns a DataFrame where rows are holdings and columns vary.
    Common column names: 'Symbol', 'Name', '% Assets', 'Weight', etc.
    """
    import pandas as pd

    if not isinstance(df, pd.DataFrame) or df.empty:
        return HoldingsData(etf_symbol=etf_symbol, fetched_date=date.today(), holdings=[], source="yfinance")

    # Detect columns: symbol, name, weight
    symbol_col = None
    name_col = None
    weight_col = None

    for c in df.columns:
        cl = str(c).lower()
        if "symbol" in cl or "ticker" in cl or "holding" in cl:
            symbol_col = c
        elif "name" in cl or "security" in cl:
            name_col = c
        elif "weight" in cl or "assets" in cl or "%" in cl:
            weight_col = c

    # Fallback: positional guess
    if symbol_col is None and len(df.columns) >= 1:
        symbol_col = df.columns[0]
    if weight_col is None and len(df.columns) >= 3:
        weight_col = df.columns[2]

    holdings = []
    for _, row in df.iterrows():
        sym = str(row.get(symbol_col, "")).strip() if symbol_col else ""
        name = str(row.get(name_col, "")).strip() if name_col else ""
        try:
            w = float(row.get(weight_col, 0) or 0)
        except (ValueError, TypeError):
            w = 0.0

        if not sym or sym == "nan":
            continue

        holdings.append(Holding(symbol=sym, name=name, weight=w))

    # Sort by weight descending, keep top N
    holdings.sort(key=lambda h: h.weight, reverse=True)

    return HoldingsData(
        etf_symbol=etf_symbol,
        fetched_date=date.today(),
        holdings=holdings[:top_n],
        source="yfinance",
    )
