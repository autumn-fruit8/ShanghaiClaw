"""
monitor_news.py — ETF holdings change monitor + news scanner.

Two sources:
  1. Holdings diff (from _meta.json) — structured rebalance summary
  2. East Money news API (stock_news_em) — financial news for each ETF

Usage:
    python3 skills/analyze/scripts/monitor_news.py
    python3 skills/analyze/scripts/monitor_news.py --etf 159207
"""

import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_INDEX_NAMES = {
    "932305": "智选高股息策略指数",
    "980092": "自由现金流指数",
    "980080": "成长100指数",
    "980081": "价值100指数",
    "930955": "红利低波100指数",
    "H30269": "中证红利低波指数",
    "000922": "中证红利指数",
    "000300": "沪深300指数",
    "000066": "上证商品指数",
    "932039": "上证国企红利指数",
    "931157": "SHS高股息低波增长指数",
    "931446": "东证红利低波指数",
}

_KEYWORDS = ["调样", "调仓", "样本股", "剔除", "纳入", "指数成分", "生效"]

_STOCK_EXCHANGE = {
    "600": "上海主板", "601": "上海主板", "603": "上海主板", "605": "上海主板",
    "000": "深圳主板", "001": "深圳主板", "002": "中小板",
    "300": "创业板", "301": "创业板", "688": "科创板",
}


def _exchange(code: str) -> str:
    return _STOCK_EXCHANGE.get(code[:3], "其他")


def _lookup_index_name(symbol: str) -> str | None:
    """Look up human-readable index name from asset-master.json."""
    try:
        path = Path.cwd() / "config" / "assets" / "asset-master.json"
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            for a in data.get("assets", []):
                if a.get("symbol") == symbol:
                    idx = a.get("index")
                    if idx and idx in _INDEX_NAMES:
                        return _INDEX_NAMES[idx]
                    return idx
    except Exception:
        pass
    return None


def _search_eastmoney(keyword: str) -> list[dict]:
    """Search East Money news via akshare for a keyword.

    Returns list of {title, content, date, source, keywords} dicts
    filtered to rebalance-related articles.
    """
    try:
        import akshare as ak
        import pandas as pd
    except ImportError:
        logger.error("akshare not installed")
        return []

    try:
        df = ak.stock_news_em(symbol=keyword)
    except Exception as e:
        logger.debug("eastmoney news failed for %s: %s", keyword, e)
        return []

    if df is None or df.empty:
        return []

    hits = []
    seen_titles = set()

    for _, row in df.iterrows():
        title = str(row.get("新闻标题", ""))
        content = str(row.get("新闻内容", ""))
        combined = title + content

        if title in seen_titles:
            continue
        seen_titles.add(title)

        matched = [kw for kw in _KEYWORDS if kw in combined]
        if matched:
            hits.append({
                "title": title,
                "content": content[:150],
                "date": str(row.get("发布时间", "")),
                "source": str(row.get("文章来源", "")),
                "keywords": matched,
                "url": str(row.get("新闻链接", "")),
            })

    return hits


def _generate_rebalance_summary(symbol: str) -> dict | None:
    """Generate a structured rebalance summary from holdings diff."""
    _workspace = Path.cwd()
    sys.path.insert(0, str(_workspace))
    sys.path.insert(0, str(_workspace / "skills" / "decide" / "scripts"))

    from dao.holdings_dao import load_holdings
    from holdings.sources.cn_akshare import fetch_top_holdings
    from config import HOLDINGS_DIR

    old = load_holdings(symbol, HOLDINGS_DIR)
    if not old:
        return None

    result = fetch_top_holdings(symbol, top_n=50)
    if not result or not result.holdings:
        return None

    current = result.holdings
    old_by_code = {h.symbol: h for h in old}
    cur_by_code = {h.symbol: h for h in current}
    old_codes = set(old_by_code.keys())
    cur_codes = set(cur_by_code.keys())

    removed = old_codes - cur_codes
    added = cur_codes - old_codes

    removed_detail = []
    for code in sorted(removed):
        h = old_by_code[code]
        removed_detail.append({"code": code, "name": h.name, "old_weight": h.weight})

    added_detail = []
    for code in sorted(added):
        h = cur_by_code[code]
        added_detail.append({"code": code, "name": h.name})

    old_ex = {}
    for c in removed:
        ex = _exchange(c)
        old_ex[ex] = old_ex.get(ex, 0) + 1
    new_ex = {}
    for c in added:
        ex = _exchange(c)
        new_ex[ex] = new_ex.get(ex, 0) + 1

    total = len(old_codes | cur_codes)
    turnover = round((len(removed) + len(added)) / total * 100, 1) if total > 0 else 0

    return {
        "symbol": symbol,
        "turnover": turnover,
        "old_count": len(old),
        "new_count": len(current),
        "removed": removed_detail[:10],
        "removed_total": len(removed),
        "added": added_detail[:10],
        "added_total": len(added),
        "exchange_shifts": {ex: {"removed": old_ex.get(ex, 0), "added": new_ex.get(ex, 0)}
                           for ex in sorted(set(list(old_ex.keys()) + list(new_ex.keys())))},
    }


def run(etf_filter: str | None = None):
    """Scan for holdings changes + news for all cached ETFs."""
    meta_path = Path.cwd() / "data" / "holdings" / "_meta.json"
    if not meta_path.exists():
        logger.error("_meta.json not found")
        return

    with open(meta_path) as f:
        meta = json.load(f)

    for symbol, entry in sorted(meta.items()):
        if etf_filter and symbol != etf_filter:
            continue

        turnover = entry.get("turnover_pct", 0)
        if turnover == 0:
            continue

        index_name = _lookup_index_name(symbol)

        # Part 1: Holdings diff summary
        summary = _generate_rebalance_summary(symbol)
        if summary:
            print(f"\n📡 {symbol} 调仓检测")
            print(f"   指数: {index_name}")
            print(f"   换手率: {summary['turnover']}% ({summary['old_count']}→{summary['new_count']})")
            if summary["removed"]:
                print(f"   剔除 ({summary['removed_total']}):")
                for r in summary["removed"][:8]:
                    print(f"     ❌ {r['code']} {r['name']} (旧权重 {r['old_weight']:.2f}%)")
            if summary["added"]:
                print(f"   新增 ({summary['added_total']}):")
                for a in summary["added"][:8]:
                    print(f"     🆕 {a['code']} {a['name']}")

        # Part 2: News scan via East Money
        search_keywords = [symbol]
        idx = _lookup_index_name(symbol)
        if isinstance(idx, str):
            search_keywords.append(idx)
            # Also try without prefix
            for k, v in _INDEX_NAMES.items():
                if v == idx:
                    search_keywords.append(k)

        all_hits = []
        seen_titles = set()
        for kw in search_keywords:
            hits = _search_eastmoney(kw)
            for h in hits:
                if h["title"] not in seen_titles:
                    seen_titles.add(h["title"])
                    all_hits.append(h)

        if all_hits:
            print(f"   新闻: {len(all_hits)} 条")
            for h in sorted(all_hits, key=lambda x: x["date"], reverse=True)[:5]:
                kw = ", ".join(h["keywords"])
                print(f"     📰 [{h['date']}] {h['source']}: {h['title'][:80]}")
            meta[symbol]["news_hits"] = all_hits
        else:
            print(f"   新闻: 无")

    # Save updated meta
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print("\n结果已保存到 logs/holdings/_meta.json")


if __name__ == "__main__":
    etf = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--etf" else None
    run(etf_filter=etf)
