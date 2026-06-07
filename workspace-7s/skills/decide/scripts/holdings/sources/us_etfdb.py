"""US ETF holdings via ETF Database (etfdb.com) web scraping.

Primary source for cache refresh. ETFdb returns top holdings
with symbol, name, and % assets for any US-listed ETF.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup

from dao.models import Holding, HoldingsData

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}
_TIMEOUT = 15


def fetch_top_holdings(symbol: str, top_n: int = 10) -> Optional[HoldingsData]:
    """Fetch top-N holdings for a US ETF from etfdb.com.

    Scrapes the holdings table from https://etfdb.com/etf/{symbol}/
    Returns HoldingsData or None on failure.
    """
    url = f"https://etfdb.com/etf/{symbol.upper()}/"
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("%s: etfdb request failed: %s", symbol, e)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    holdings = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 5:
            continue
        header_text = " ".join(th.get_text(strip=True) for th in rows[0].find_all(["th", "td"]))
        if "% Assets" not in header_text:
            continue

        for row in rows[1:]:
            cols = row.find_all(["td", "th"])
            if len(cols) < 3:
                continue
            try:
                weight = float(cols[2].get_text(strip=True).replace("%", ""))
            except (ValueError, TypeError):
                continue
            symbol_text = cols[0].get_text(strip=True)
            name = cols[1].get_text(strip=True) if len(cols) > 1 else ""
            if not symbol_text:
                continue
            holdings.append(Holding(symbol=symbol_text, name=name, weight=weight))

        break

    if not holdings:
        logger.warning("%s: no holdings table found on etfdb", symbol)
        return None

    holdings.sort(key=lambda h: h.weight, reverse=True)
    return HoldingsData(
        etf_symbol=symbol.upper(),
        fetched_date=date.today(),
        holdings=holdings[:top_n],
        source="etfdb",
    )
