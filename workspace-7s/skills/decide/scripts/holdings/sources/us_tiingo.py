"""US ETF holdings via Tiingo (stub).

Tiingo's ETF top-holdings endpoint requires a paid plan upgrade.
The adapter is in place so the fallback chain is complete;
it always returns None until API access changes.
"""
from __future__ import annotations

import logging
from typing import Optional

from dao.models import HoldingsData

logger = logging.getLogger(__name__)


def fetch_top_holdings(symbol: str, top_n: int = 10) -> Optional[HoldingsData]:
    """Stub — Tiingo ETF holdings require paid plan upgrade.

    When API access is upgraded, implement:
      GET https://api.tiingo.com/tiingo/etf/{symbol}/top-holdings?token={key}
    """
    logger.debug("%s: Tiingo source not available on current plan", symbol)
    return None
