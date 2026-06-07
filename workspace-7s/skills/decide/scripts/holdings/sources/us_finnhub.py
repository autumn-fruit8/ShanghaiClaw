"""US ETF holdings via finnhub (stub).

Finnhub's ETF holdings endpoint requires FINNHUB_API_KEY.
Free tier allows 60 req/min. Deferred — not yet implemented.
"""
from __future__ import annotations

import logging
from typing import Optional

from dao.models import HoldingsData

logger = logging.getLogger(__name__)


def fetch_top_holdings(symbol: str, top_n: int = 10) -> Optional[HoldingsData]:
    """Stub — finnhub ETF holdings not yet implemented.

    When implemented:
      GET https://finnhub.io/api/v1/etf/holdings?symbol={symbol}&token={key}
    """
    logger.debug("%s: finnhub source not yet implemented", symbol)
    return None
