"""CN ETF holdings via akshare.

Three sources, tried in priority order:
  1. index_stock_cons_weight_csindex — current index constituents with weights (CSI indices only)
  2. fund_portfolio_hold_em — quarterly fund report with weights (all CN ETFs)
  3. index_stock_cons — current index constituents without weights (for diff detection)

Priority 1 is best (current + weighted). If no weights, falls back to 2.
Priority 3 is for non-CSI indices where fund data is also unavailable.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from dao.models import Holding, HoldingsData

logger = logging.getLogger(__name__)


def _lookup_index(symbol: str) -> str | None:
    """Look up an ETF's tracked index code from asset-master.json."""
    try:
        path = Path.cwd() / "config" / "assets" / "asset-master.json"
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            for a in data.get("assets", []):
                if a.get("symbol") == symbol:
                    return a.get("index") or a.get("benchmark") or a.get("tracking")
    except Exception:
        pass
    return None


def _fetch_index_cons(index_symbol: str, top_n: int) -> list[Holding] | None:
    """Fetch current index constituents, with weight merge from csindex."""
    import akshare as ak

    # Step 1: Current stock list
    try:
        df_cons = ak.index_stock_cons(symbol=index_symbol)
    except Exception as e:
        logger.debug("index_stock_cons(%s) failed: %s", index_symbol, e)
        return None

    if df_cons is None or df_cons.empty:
        return None

    code_col = next((c for c in df_cons.columns if "代码" in c), df_cons.columns[0])
    name_col = next((c for c in df_cons.columns if "名称" in c), df_cons.columns[1])

    current_stocks = {}
    for _, row in df_cons.iterrows():
        code = str(row.get(code_col, "")).strip()
        name = str(row.get(name_col, "")).strip()
        if code and code not in ("nan", "None", ""):
            current_stocks[code] = name

    if not current_stocks:
        return None

    # Step 2: Merge weights from csindex
    weights = {}
    try:
        df_wt = ak.index_stock_cons_weight_csindex(symbol=index_symbol)
        if df_wt is not None and not df_wt.empty:
            wt_code_col = next((c for c in df_wt.columns if c.strip() == "成分券代码"), None)
            wt_wt_col = next((c for c in df_wt.columns if c.strip() == "权重"), None)
            if not wt_code_col:
                wt_code_col = next((c for c in df_wt.columns if "代码" in c and "指数" not in c), df_wt.columns[4])
            if wt_wt_col:
                for _, row in df_wt.iterrows():
                    wc = str(row.get(wt_code_col, "")).strip()
                    if wc and wc not in ("nan", "None", ""):
                        try:
                            weights[wc] = float(row.get(wt_wt_col, 0) or 0)
                        except (ValueError, TypeError):
                            weights[wc] = 0.0
    except Exception as e:
        logger.debug("csindex weight merge failed for %s: %s", index_symbol, e)

    holdings = [Holding(symbol=code, name=name, weight=weights.get(code, 0.0), sector="")
                for code, name in current_stocks.items()]
    holdings.sort(key=lambda h: h.weight, reverse=True)
    return holdings[:top_n]


def _fetch_fund_holdings(symbol: str, top_n: int) -> list[Holding] | None:
    """Fetch quarterly fund report holdings (lagged ~3mo, has weights)."""
    import akshare as ak
    import datetime as dt

    for year in (str(dt.date.today().year), str(dt.date.today().year - 1)):
        try:
            df = ak.fund_portfolio_hold_em(symbol=symbol, date=year)
            if df is not None and not df.empty:
                break
        except Exception:
            continue
    else:
        return None

    quarter_col = next((c for c in df.columns if "季度" in c), None)
    if quarter_col:
        df = df[df[quarter_col] == df[quarter_col].iloc[0]]

    code_col = next((c for c in df.columns if "代码" in c), df.columns[1])
    name_col = next((c for c in df.columns if "名称" in c), df.columns[2])
    weight_col = next((c for c in df.columns if "比例" in c or "净值" in c), df.columns[3])

    holdings = []
    for _, row in df.iterrows():
        try:
            w = float(row.get(weight_col, 0) or 0)
            code = str(row.get(code_col, "")).strip()
            name = str(row.get(name_col, "")).strip()
            if code and code not in ("nan", "None", ""):
                holdings.append(Holding(symbol=code, name=name, weight=w, sector=""))
        except (ValueError, TypeError):
            continue

    if not holdings:
        return None
    holdings.sort(key=lambda h: h.weight, reverse=True)
    return holdings[:top_n]


def fetch_top_holdings(symbol: str, top_n: int = 10) -> Optional[HoldingsData]:
    """
    Fetch current top holdings for a CN ETF.

    Priority:
      1. Index constituents with csindex weights (current + weighted)
      2. Fund quarterly report (lagged but has weights) — replaces index if no weights
      3. Plain index constituents (current, no weights) — only for diff detection

    Returns HoldingsData or None on failure.
    """
    index_symbol = _lookup_index(symbol)
    holdings = None
    source = "akshare-index"

    if index_symbol:
        holdings = _fetch_index_cons(index_symbol, top_n)

    if holdings:
        has_weights = any(h.weight > 0 for h in holdings)
        if has_weights:
            logger.info("%s: %d holdings from index %s (with weights)",
                        symbol, len(holdings), index_symbol)
        else:
            # No weights from csindex — try fund report
            fund = _fetch_fund_holdings(symbol, top_n)
            if fund and any(h.weight > 0 for h in fund):
                holdings = fund
                source = "akshare-fund"
                logger.info("%s: %d holdings from fund report (with weights)",
                            symbol, len(holdings))
            else:
                source = "akshare-index"
                logger.info("%s: %d holdings from index %s (no weights)",
                            symbol, len(holdings), index_symbol)
    else:
        # No index data — try fund report
        holdings = _fetch_fund_holdings(symbol, top_n)
        if holdings:
            source = "akshare-fund"
            logger.info("%s: %d holdings from fund report (with weights)",
                        symbol, len(holdings))

    # Last resort: plain index constituents (no weights, for diff only)
    if not holdings and index_symbol:
        holdings = _fetch_index_cons(index_symbol, top_n)
        if holdings:
            source = "akshare-index"
            logger.info("%s: %d holdings from index %s (no weights, final fallback)",
                        symbol, len(holdings), index_symbol)

    if not holdings:
        logger.warning("%s: no holdings from any source", symbol)
        return None

    return HoldingsData(
        etf_symbol=symbol,
        fetched_date=date.today(),
        holdings=holdings,
        source=source,
    )
