"""Symbol type detection and data bootstrap for any symbol.

Exported API:
    classify_symbol(symbol)  → str type code
    resolve(symbol, region, **opts)  → Path to CSV or None

All other functions are internal (prefixed with _).

All external API calls (akshare, yfinance) are delegated to market_service.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from utils.data_service.market_service import (
    fetch_ohlcv,
    fetch_total_return,
    fetch_csi_index,
)

# ── Workspace paths ────────────────────────────────────────────────────────────

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = WORKSPACE_ROOT / "knowledge"
CACHE_DIR = WORKSPACE_ROOT / "adhoc" / "cache"
CONFIG_DIR = WORKSPACE_ROOT / "config"
ASSET_MASTER = CONFIG_DIR / "assets" / "asset-master.json"
TR_MAPPING = CONFIG_DIR / "symbol_resolution" / "tr_mapping.json"
CSI_PATTERNS = CONFIG_DIR / "symbol_resolution" / "csi_patterns.json"


# ── Symbol patterns (order matters: most specific first) ──────────────────────

SYM_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("CSI_TR",      re.compile(r"^H\d{5}$")),                             # H00300
    ("CSI_TR",      re.compile(r"^.*CNY01(0)?$")),                       # 931573CNY01
    ("CSI_TR",      re.compile(r"^92\d{3,}$")),                          # 921468, 921446
    ("HK",          re.compile(r"^\d{4}\.HK$")),                          # 3032.HK
    ("CSI_INDEX",   re.compile(r"^93\d{3,}$")),                           # 931573
    ("CSI_INDEX",   re.compile(r"^000\d{3,}$")),                          # 000300
    ("CSI_INDEX",   re.compile(r"^399\d{3,}$")),                          # 399300
    ("CNI_INDEX",   re.compile(r"^48\d{3,}$")),                           # 480080
    ("CNI_INDEX",   re.compile(r"^98\d{3,}$")),                           # 98xxxx
    ("CN_ETF",      re.compile(r"^159\d{3,}$")),                          # 159307
    ("CN_ETF",      re.compile(r"^51\d{3,}$")),                           # 510310
    ("CN_ETF",      re.compile(r"^56\d{3,}$")),                           # 560700
    ("CN_OTC",      re.compile(r"^007\d{3,}$")),                          # 007751
    ("CN_OTC",      re.compile(r"^012\d{3,}$")),                          # 012708
    ("US",          re.compile(r"^[A-Z]{2,5}$")),                         # SPY, QQQM
]


# ── Public API ────────────────────────────────────────────────────────────────


def classify_symbol(symbol: str) -> str:
    """Detect symbol type from its format.

    Returns one of: CSI_INDEX, CNI_INDEX, CN_ETF, CN_OTC, HK, US, CSI_TR, UNKNOWN.
    """
    for sym_type, pattern in SYM_PATTERNS:
        if pattern.match(symbol):
            return sym_type
    return "UNKNOWN"


def resolve(
    symbol: str,
    region: str,
    *,
    add_to_master: bool = False,
    dry_run: bool = False,
) -> tuple[Optional[str], str]:
    """Resolve a symbol to a CSV, bootstrapping data if needed.

    Returns (csv_path, message):
        csv_path: path to CSV file if successful, None if failed.
        message: human-readable status/error message (always present).
    """
    sym_type = classify_symbol(symbol)
    csv_path = KNOWLEDGE_DIR / region / "3_processed" / f"{symbol}.csv"
    cache_path = CACHE_DIR / f"{symbol}.csv"

    # ── Step 1: Check existing data ───────────────────────────────────────
    # Active assets live in knowledge/; non-active cached in adhoc/cache/.
    if csv_path.exists():
        msg = f"数据已存在 → {csv_path}"
        _report(sym_type, symbol, msg, dry_run)
        return str(csv_path), msg
    if cache_path.exists():
        msg = f"数据已存在(缓存) → {cache_path}"
        _report(sym_type, symbol, msg, dry_run)
        return str(cache_path), msg

    # ── Step 2: Reject unsupported types ──────────────────────────────────
    if sym_type == "CNI_INDEX":
        msg = f"❌ {symbol} 是国证指数 (48xxxx/98xxxx)，无可用价格数据源，不支持"
        _report(sym_type, symbol, "UNSUPPORTED", dry_run)
        return None, msg

    if sym_type == "UNKNOWN":
        msg = (
            f"❌ 无法识别 {symbol} 的符号类型\n"
            f"   6位数字以 93/000/399 开头 → CSI 指数\n"
            f"   以 48/98 开头 → 国证指数（不支持）\n"
            f"   以 159/51/56 开头 → CN ETF\n"
            f"   字母组成 → US ETF\n"
            f"   或直接输入 TR 代码（如 H00300）"
        )
        _report(sym_type, symbol, "无法识别符号类型", dry_run)
        return None, msg

    # ── Step 3: Bootstrap data ────────────────────────────────────────────
    df, source_label = _bootstrap(symbol, sym_type, region, dry_run)
    if df is None or df.empty:
        msg = f"❌ {symbol} 所有数据源尝试均失败"
        _report(sym_type, symbol, f"所有数据源尝试均失败", dry_run)
        return None, msg

    # Warn if data is price-only (not total return)
    price_warn = ""
    if source_label == "price":
        price_warn = f"\n⚠️  使用的是价格指数（非全收益），分红再投资未包含在内"
        print(f"[WARN]{price_warn}")

    if dry_run:
        msg = f"✅ 准备好写入 {cache_path} ({len(df)} 行)"
        _report(sym_type, symbol, f"准备好写入 {cache_path} ({len(df)} 行)", dry_run)
        return str(cache_path), msg

    # ── Step 4: Write CSV to adhoc/cache/ ─────────────────────────────────
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False)
    msg = f"✅ {symbol} 数据引导完成 → {cache_path} ({len(df)} 行){price_warn}"
    print(f"[OK] {msg}")

    # ── Step 5: Register in asset-master (optional) ───────────────────────
    if add_to_master:
        _register_in_master(symbol, sym_type, region)

    return str(csv_path), msg


# ── Internal: Bootstrap by type ──────────────────────────────────────────────


def _bootstrap(symbol: str, sym_type: str, region: str, dry: bool) -> tuple[Optional[pd.DataFrame], str]:
    """Route to data source based on symbol type.

    Returns (DataFrame or None, source_label).
    source_label is 'tr', 'price', 'etf', 'us' etc — used for user-facing messaging.
    """
    from_handlers = {
        "CSI_INDEX":  _bootstrap_csi_index,
        "CSI_TR":     _bootstrap_csi_tr,
        "CN_ETF":     _bootstrap_cn_etf,
        "CN_OTC":     _bootstrap_cn_otc,
        "US":         _bootstrap_us_etf,
        "HK":         _bootstrap_us_etf,    # yfinance covers HK
    }
    handler = from_handlers.get(sym_type)
    if handler is None:
        return None, "unknown"
    return handler(symbol, region, dry)


def _bootstrap_csi_index(symbol: str, region: str, dry: bool) -> tuple[Optional[pd.DataFrame], str]:
    """Try CSI TR API: first check mapping, then pattern-based suffix via market_service."""
    # 1. Check TR mapping
    tr_map = _load_tr_mapping()
    if symbol in tr_map:
        tr_code = tr_map[symbol]["tr_code"]
        _report("CSI_INDEX", symbol, f"tr_mapping 命中 → {tr_code}", dry)
        if not dry:
            csi_data = fetch_csi_index(tr_code, tr_code, "20000101", (date.today() + timedelta(days=1)).strftime("%Y%m%d"))
            if csi_data and "price" in csi_data:
                df = _csi_data_to_df(csi_data["price"])
                if df is not None and len(df) > 0:
                    return df, "tr"

    # 2. Try pattern-based suffix
    patterns = _load_csi_patterns()
    for prefix, info in patterns.items():
        if symbol.startswith(prefix):
            tr_code = f"{symbol}{info['suffix']}"
            _report("CSI_INDEX", symbol, f"尝试 pattern {tr_code} (confidence={info['confidence']})", dry)
            if not dry:
                csi_data = fetch_csi_index(tr_code, tr_code, "20000101", (date.today() + timedelta(days=1)).strftime("%Y%m%d"))
                if csi_data and "price" in csi_data:
                    df = _csi_data_to_df(csi_data["price"])
                    if df is not None and len(df) > 0:
                        return df, "tr"

    # 3. Fallback: try plain symbol as price index
    _report("CSI_INDEX", symbol, "尝试原始符号（价格指数）", dry)
    if not dry:
        csi_data = fetch_csi_index(symbol, symbol, "20000101", (date.today() + timedelta(days=1)).strftime("%Y%m%d"))
        if csi_data and "price" in csi_data:
            df = _csi_data_to_df(csi_data["price"])
            if df is not None:
                return df, "price"  # ⚠ not total return

    # 4. Show diagnostic
    attempted = []
    patterns = _load_csi_patterns()
    for prefix, info in patterns.items():
        if symbol.startswith(prefix):
            attempted.append(f"{symbol}{info['suffix']}")
    print(f"  └─ 无法自动解析 {symbol} 的 TR 代码")
    print(f"     尝试了: tr_mapping(未命中)", end="")
    if attempted:
        print(f" + pattern({', '.join(attempted)})")
    else:
        print()
    print(f"     + price_index(价格指数)")
    print(f"     如需支持，请在 tr_mapping.json 中添加映射:")
    print(f"       \"{symbol}\": {{\"tr_code\": \"<正确TR代码>\", \"name\": \"\"}}")
    return None, "failed"


def _bootstrap_csi_tr(symbol: str, region: str, dry: bool) -> tuple[Optional[pd.DataFrame], str]:
    """Direct CSI TR code (e.g. H00300, 931573CNY01) via market_service."""
    if dry:
        _report("CSI_TR", symbol, f"直输 CSI TR 代码", dry)
        return None, "tr"
    csi_data = fetch_csi_index(symbol, symbol, "20000101", (date.today() + timedelta(days=1)).strftime("%Y%m%d"))
    if csi_data and "price" in csi_data:
        df = _csi_data_to_df(csi_data["price"])
        if df is not None:
            return df, "tr"
    return None, "failed"


def _bootstrap_cn_etf(symbol: str, region: str, dry: bool) -> tuple[Optional[pd.DataFrame], str]:
    """CN ETF: prefer CSI TR via tracks, fallback to Sina → EastMoney via market_service."""
    # 1. Check asset-master for tracks → CSI TR data (longer history)
    try:
        from dao.asset_dao import AssetManifest
        asset = AssetManifest().get(symbol)
        if asset and asset.tracks:
            tr_code = _resolve_tr_code_from_config(asset.tracks)
            if tr_code:
                _report("CN_ETF", symbol, f"通过 tracks={asset.tracks} → CSI TR ({tr_code})", dry)
                if not dry:
                    csi_data = fetch_csi_index(tr_code, tr_code, "20000101", (date.today() + timedelta(days=1)).strftime("%Y%m%d"))
                    if csi_data and "price" in csi_data:
                        df = _csi_data_to_df(csi_data["price"])
                        if df is not None and len(df) > 100:
                            return df, "tr"
    except Exception:
        pass

    # 2. Fallback: Sina/EM via market_service.fetch_ohlcv
    _report("CN_ETF", symbol, "尝试 Sina/EM via market_service...", dry)
    if not dry:
        try:
            ohlcv_df = fetch_ohlcv(symbol, "cn")
            if ohlcv_df is not None and len(ohlcv_df) > 0:
                return _format_ohlcv_df(ohlcv_df), "sina"
        except Exception:
            pass

    return None, "failed"


def _bootstrap_cn_otc(symbol: str, region: str, dry: bool) -> tuple[Optional[pd.DataFrame], str]:
    """CN OTC: try TR mapping first, then fallback."""
    return _bootstrap_csi_index(symbol, region, dry)


def _bootstrap_us_etf(symbol: str, region: str, dry: bool) -> tuple[Optional[pd.DataFrame], str]:
    """US / HK ETF via market_service.fetch_ohlcv (yfinance → Tiingo)."""
    _report("US", symbol, "尝试 yfinance via market_service...", dry)

    if not dry:
        try:
            ohlcv_df = fetch_ohlcv(symbol, "us", adj_close=True)
            if ohlcv_df is not None and not ohlcv_df.empty:
                close_col = "adj_close" if "adj_close" in ohlcv_df.columns else "close"
                close_series = pd.to_numeric(ohlcv_df[close_col], errors="coerce").dropna()
                if len(close_series) >= 30:
                    first_val = close_series.iloc[0]
                    if first_val > 0:
                        total_return = close_series / first_val * 1000
                        df = pd.DataFrame({
                            "date": ohlcv_df["date"].values[:len(total_return)],
                            "total_return": total_return.values,
                            "close": close_series.values,
                        })
                        return df, "yfinance"
        except Exception:
            pass

    return None, "failed"


# ── Internal: Data fetching helpers ──────────────────────────────────────────


def _csi_data_to_df(csi_df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Convert CSI index DataFrame (date, close columns) to standard format."""
    if csi_df is None or csi_df.empty:
        return None
    close = pd.to_numeric(csi_df["close"], errors="coerce").values
    valid = ~np.isnan(close)
    if valid.sum() < 2:
        return None
    close_valid = close[valid]
    total_return = close_valid / close_valid[0] * 1000
    return pd.DataFrame({
        "date": csi_df["date"].values[:len(total_return)],
        "total_return": total_return,
        "close": close_valid,
    })


def _format_ohlcv_df(ohlcv_df: pd.DataFrame) -> pd.DataFrame:
    """Format OHLCV DataFrame into standard bootstrap format."""
    close = pd.to_numeric(ohlcv_df["close"], errors="coerce").values
    valid = ~np.isnan(close)
    if valid.sum() < 2:
        close = np.full_like(close, np.nan)
    close_valid = close[valid]
    if len(close_valid) < 2:
        return pd.DataFrame()
    total_return = close_valid / close_valid[0] * 1000
    return pd.DataFrame({
        "date": ohlcv_df["date"].values[:len(total_return)],
        "total_return": total_return,
        "close": close_valid,
    })


# ── Internal: Config loaders ─────────────────────────────────────────────────


def _load_tr_mapping() -> dict:
    if not TR_MAPPING.exists():
        return {}
    with open(TR_MAPPING) as f:
        return json.load(f)


def _load_csi_patterns() -> dict:
    if not CSI_PATTERNS.exists():
        return {}
    with open(CSI_PATTERNS) as f:
        return json.load(f)


def _resolve_tr_code_from_config(tracks: str) -> Optional[str]:
    """Resolve tracks to TR code: tr_mapping → CSI pattern → as-is.

    Returns None for tracks that cannot be resolved to a CSI TR API call:
    - CNI indices (98xxxx, 48xxxx) — 国证指数, no CSI API support
    - SZSE indices (399006) — 深交所创业板指, not CSI index
    These should fall through to ETF close price proxy via Sina/EM.
    """
    # CNI indices (国证) — no CSI TR API, use ETF proxy
    if tracks.startswith(("98", "48")):
        return None
    # SZSE indices — not CSI, use ETF proxy
    if tracks == "399006":
        return None
    tr_map = _load_tr_mapping()
    if tracks in tr_map:
        return tr_map[tracks]["tr_code"]
    if tracks.startswith(("H", "92")) or "CNY" in tracks:
        return tracks
    for prefix, info in _load_csi_patterns().items():
        if tracks.startswith(prefix):
            return f"{tracks}{info['suffix']}"
    return tracks


# ── Internal: Asset-master registration ──────────────────────────────────────


def _register_in_master(symbol: str, sym_type: str, region: str):
    """Add symbol to asset-master.json for analyze/routing use."""
    if not ASSET_MASTER.exists():
        return

    with open(ASSET_MASTER) as f:
        data = json.load(f)

    # Check if already present
    if any(a["symbol"] == symbol for a in data.get("assets", [])):
        return

    type_map = {
        "CSI_INDEX": "CN_ETF",
        "CNI_INDEX": "CN_ETF",
        "CN_ETF": "CN_ETF",
        "CN_OTC": "CN_OTC",
        "US": "US_ETF",
        "HK": "HK_ETF",
        "CSI_TR": "CN_ETF",
    }
    entry = {
        "symbol": symbol,
        "name": f"Auto-resolved {symbol}",
        "type": type_map.get(sym_type, "CN_ETF"),
        "region": region.upper(),
    }
    data.setdefault("assets", []).append(entry)
    with open(ASSET_MASTER, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[OK] Registered {symbol} in asset-master.json")


# ── Internal: Reporting ──────────────────────────────────────────────────────


def _report(sym_type: str, symbol: str, message: str, dry: bool):
    prefix = "[DRY-RUN]" if dry else "[INFO]"
    print(f"{prefix} [{sym_type}] {symbol}: {message}")
