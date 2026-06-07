"""Holdings fetcher — two modes: cache-only read + offline refresh.

- get_top_holdings(): cache-only, called by decide --concentration. Never makes live API calls.
- refresh_holdings(): offline, called by refresh script. Makes live API calls, writes cache.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from dao.holdings_dao import (
    load_holdings,
    save_holdings,
    is_cache_stale,
)
from dao.models import HoldingsData, Holding
from config import HOLDINGS_DIR

logger = logging.getLogger(__name__)

_TOP_N_DEFAULT = 10


def get_top_holdings(
    symbol: str,
    top_n: int = _TOP_N_DEFAULT,
    max_age_days: int = 30,
) -> Optional[HoldingsData]:
    """Cache-only read. Returns cached holdings if fresh, else None.

    NEVER makes live API calls. Use refresh_holdings() to populate cache.
    """
    symbol_upper = symbol.upper()
    cached_list = load_holdings(symbol_upper, HOLDINGS_DIR)
    if cached_list is None:
        logger.info("%s: no cached holdings", symbol_upper)
        return None
    if is_cache_stale(symbol_upper, HOLDINGS_DIR, max_age_days):
        logger.warning("%s: cached holdings stale (age > %d days)", symbol_upper, max_age_days)
    return HoldingsData(
        etf_symbol=symbol_upper,
        fetched_date=date.today(),
        holdings=cached_list[:top_n],
        source="cache",
    )


def refresh_holdings(
    symbol: str,
    top_n: int = _TOP_N_DEFAULT,
) -> Optional[HoldingsData]:
    """Offline refresh. Tries live sources, writes to cache if successful.

    Source chain: yfinance → tiingo → finnhub → akshare.
    Designed to run on the server where yfinance is not blocked.
    """
    from holdings.sources.us_etfdb import fetch_top_holdings as etfdb_fetch
    from holdings.sources.us_yfinance import fetch_top_holdings as yfinance_fetch
    from holdings.sources.us_tiingo import fetch_top_holdings as tiingo_fetch
    from holdings.sources.us_finnhub import fetch_top_holdings as finnhub_fetch
    from holdings.sources.cn_akshare import fetch_top_holdings as akshare_fetch

    symbol_upper = symbol.upper()

    source_chain: list[tuple[str, callable]] = []

    if _is_us_etf(symbol_upper):
        source_chain = [
            ("etfdb", etfdb_fetch),
            ("yfinance", yfinance_fetch),
            ("tiingo", tiingo_fetch),
            ("finnhub", finnhub_fetch),
        ]
    elif _is_cn_etf(symbol_upper):
        source_chain = [("akshare", akshare_fetch)]
    else:
        source_chain = [
            ("etfdb", etfdb_fetch),
            ("yfinance", yfinance_fetch),
            ("tiingo", tiingo_fetch),
            ("finnhub", finnhub_fetch),
        ]

    for source_name, fetch_fn in source_chain:
        try:
            result = fetch_fn(symbol_upper, top_n)
            if result is not None and result.holdings:
                from dao.holdings_dao import save_holdings_with_diff
                diff = save_holdings_with_diff(symbol_upper, result.holdings, HOLDINGS_DIR)
                log_parts = [f"refreshed {len(result.holdings)} holdings from {source_name}"]
                if diff and diff["turnover_pct"] > 0:
                    log_parts.append(f"turnover {diff['turnover_pct']}%")
                    if diff["new_stocks"]:
                        log_parts.append(f"+{len(diff['new_stocks'])}")
                    if diff["removed_stocks"]:
                        log_parts.append(f"-{len(diff['removed_stocks'])}")
                logger.info("%s: %s", symbol_upper, ", ".join(log_parts))
                return result
        except Exception as e:
            logger.warning("%s: %s source failed: %s", symbol_upper, source_name, e)
            continue

    logger.error("%s: no data from any source", symbol_upper)
    return None


def _is_us_etf(symbol: str) -> bool:
    return symbol.isascii() and symbol.isupper() and len(symbol) <= 5


def _is_cn_etf(symbol: str) -> bool:
    return symbol.isdigit() and len(symbol) == 6
