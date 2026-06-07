"""
File Parser
===========

Parses calibration source files (CSV, XLSX, XLS, MHTML) into a standardised
two-column DataFrame: ``(date, total_return)``.

Ported from the legacy calibrate3.py (V4.1) with minimal changes.
Used by CalibrationCN and CalibrationUS.
"""

import email
import io
import os
import re
import warnings
from typing import Optional

import pandas as pd

warnings.filterwarnings("ignore")


# ──────────────────────────────────────────────────────────────────────────────
# Column-name keywords (priority-ordered)
# ──────────────────────────────────────────────────────────────────────────────

_KEY_DATE = ["date", "日期", "时间", "time", "effective date", "nav date"]

_KEY_VAL_P1 = [
    "total return", "gross return", "全收益", "nav", "net asset value",
    "index level", "(tr)",
]
_KEY_VAL_P2 = ["adj close", "close", "price", "value", "净值", "收盘"]
_KEY_VAL_P3 = ["index"]

_BLACKLIST = ["code", "代码", "ticker", "symbol", "name", "名称",
               "简称", "全称", "english", "chinese"]
# 'id' is kept separate: it must match as a standalone word only, because it
# appears as a substring in legitimate column names like 'dividend'.
_ID_WORD_RE = re.compile(r"\bid\b")


def _is_clean_col(col_name: str) -> bool:
    if _ID_WORD_RE.search(col_name):
        return False
    return not any(b in col_name for b in _BLACKLIST)


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def _parse_yahoo_html_content(html_bytes: bytes) -> Optional[pd.DataFrame]:
    """
    Shared core: parse raw HTML bytes from a Yahoo Finance table.

    Finds the first table with an ``Adj Close`` column, cleans it, and
    returns a ``(date, total_return)`` DataFrame sorted by date, or ``None``.
    """
    dfs = pd.read_html(io.BytesIO(html_bytes))

    target_df = None
    adj_col_name = None
    for df in dfs:
        cols = [str(c) for c in df.columns]
        matches = [c for c in cols if "Adj Close" in c]
        if matches:
            target_df = df
            adj_col_name = matches[0]
            break

    if target_df is None:
        print("  ❌ 没找到包含 Adj Close 的表格。")
        return None

    df = target_df[["Date", adj_col_name]].copy()
    df.columns = ["date", "total_return"]

    # Filter dividend/split annotation rows
    for keyword in ("Dividend", "Split"):
        df = df[~df["total_return"].astype(str).str.contains(keyword, case=False, na=False)]

    df["total_return"] = pd.to_numeric(df["total_return"], errors="coerce")
    # Parse dates with explicit format for Yahoo Finance format (e.g., "May 1, 2026")
    df["date"] = pd.to_datetime(df["date"], format="%b %d, %Y", errors="coerce")
    df = df.dropna().sort_values("date")

    if len(df) == 0:
        print("  ❌ 清洗后数据为空")
        return None

    return df


def load_mhtml_calibration_file(file_path: str) -> Optional[pd.DataFrame]:
    """
    Parse a Yahoo Finance MHTML export file.

    Extracts the ``Adj Close`` column and returns a clean
    ``(date, total_return)`` DataFrame sorted by date, or ``None`` on failure.
    """
    print(f"  🔍 正在解析 (MHTML): {os.path.basename(file_path)}")
    try:
        with open(file_path, "rb") as f:
            msg = email.message_from_bytes(f.read())

        html_content = None
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html_content = part.get_payload(decode=True)
                break

        if not html_content:
            print("  ❌ 错误: 文件中没找到 HTML 内容。")
            return None

        return _parse_yahoo_html_content(html_content)

    except Exception as e:
        print(f"  ❌ MHTML 解析失败: {e}")
        return None


def load_html_calibration_file(file_path: str) -> Optional[pd.DataFrame]:
    """
    Parse a plain Yahoo Finance HTML export file (no MIME envelope).

    Reads the file bytes directly and delegates to the shared HTML parser.
    Returns a ``(date, total_return)`` DataFrame sorted by date, or ``None``.
    """
    print(f"  🔍 正在解析 (HTML): {os.path.basename(file_path)}")
    try:
        with open(file_path, "rb") as f:
            html_content = f.read()
        return _parse_yahoo_html_content(html_content)
    except Exception as e:
        print(f"  ❌ HTML 解析失败: {e}")
        return None


def smart_load_calibration_file(file_path: str) -> Optional[pd.DataFrame]:
    """
    Parse a CSV or Excel calibration file.

    Handles:
    - Auto encoding detection (UTF-8 / GBK)
    - Smart header-row detection (up to row 30)
    - Column-name keyword mapping (date + value)
    - Excel serial dates and YYYYMMDD integer dates
    - ``=value`` Excel formula prefix
    - Currency symbols / thousand-separator commas

    Returns a clean ``(date, total_return)`` DataFrame sorted by date,
    or ``None`` on failure.
    """
    file_ext = os.path.splitext(file_path)[1].lower()
    print(f"  🔍 正在解析: {os.path.basename(file_path)}")

    # ── Phase 1: Smart header detection ───────────────────────────────────────
    try:
        lines_preview = []
        if file_ext == ".csv":
            for enc in ("utf-8", "gbk"):
                try:
                    with open(file_path, "r", encoding=enc, errors="replace") as f:
                        lines_preview = [f.readline() for _ in range(30)]
                    break
                except Exception:
                    continue
        else:
            df_tmp = pd.read_excel(file_path, header=None, nrows=30)
            lines_preview = [",".join(str(x) for x in row) for row in df_tmp.values]

        header_row_idx = 0
        for idx, line in enumerate(lines_preview):
            line_lower = line.lower()
            if any(k in line_lower for k in _KEY_DATE) and any(
                k in line_lower for k in _KEY_VAL_P1 + _KEY_VAL_P2 + _KEY_VAL_P3
            ):
                header_row_idx = idx
                print(f"  👀 锁定表头在第 {idx + 1} 行")
                break

        if file_ext == ".csv":
            df_raw = None
            for enc in ("utf-8", "gbk"):
                try:
                    df_raw = pd.read_csv(file_path, header=header_row_idx,
                                         encoding=enc, on_bad_lines="skip")
                    break
                except Exception:
                    continue
            if df_raw is None:
                print("  ❌ CSV 读取失败")
                return None
        else:
            df_raw = pd.read_excel(file_path, header=header_row_idx)

    except Exception as e:
        print(f"  ❌ 文件读取错误: {e}")
        return None

    # ── Phase 2: Column mapping ────────────────────────────────────────────────
    df_raw.columns = [str(c).strip().lower() for c in df_raw.columns]

    date_col = next((c for c in df_raw.columns if any(k in c for k in _KEY_DATE)), None)

    val_col = None
    for k in _KEY_VAL_P1:
        found = next((c for c in df_raw.columns if k in c and _is_clean_col(c)), None)
        if found:
            val_col = found
            break
    if not val_col:
        for k in _KEY_VAL_P2:
            found = next((c for c in df_raw.columns if k in c and _is_clean_col(c)), None)
            if found:
                val_col = found
                break
    if not val_col:
        val_col = next(
            (c for c in df_raw.columns if "index" in c and _is_clean_col(c)), None
        )

    if not date_col or not val_col:
        print(f"  ❌ 列名识别失败. 现有列: {list(df_raw.columns)}")
        return None

    print(f"  ✅ 映射: [{date_col}] -> Date | [{val_col}] -> Value")

    # ── Phase 3: Cleaning ──────────────────────────────────────────────────────
    try:
        df = df_raw[[date_col, val_col]].copy()
        df.columns = ["date", "total_return"]

        # Numeric cleaning: strip =, ,, $, ￥, spaces
        if df["total_return"].dtype == object:
            df["total_return"] = (
                df["total_return"]
                .astype(str)
                .str.replace("=", "", regex=False)
                .str.replace(",", "", regex=False)
                .str.replace("$", "", regex=False)
                .str.replace("￥", "", regex=False)
                .str.replace(" ", "", regex=False)
            )
        df["total_return"] = pd.to_numeric(df["total_return"], errors="coerce")

        # Date-format detection
        date_numeric = pd.to_numeric(df["date"], errors="coerce")
        valid_nums = date_numeric.dropna()

        is_serial = is_yyyymmdd = False
        if len(valid_nums) > 0:
            mean_val = valid_nums.mean()
            if 20_000 < mean_val < 100_000:
                is_serial = True
                print("  💡 检测到 Excel 序列日期 (如 42338)")
            elif 19_900_000 < mean_val < 21_000_000:
                is_yyyymmdd = True
                print("  💡 检测到 YYYYMMDD 格式日期 (如 20251212)")

        if is_serial:
            df["date"] = pd.to_datetime(date_numeric, unit="D",
                                         origin="1899-12-30", errors="coerce")
        elif is_yyyymmdd:
            df["date"] = pd.to_datetime(
                date_numeric.fillna(0).astype(int).astype(str),
                format="%Y%m%d", errors="coerce",
            )
        else:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")

        df = df.dropna(subset=["date", "total_return"]).sort_values("date")

        if len(df) == 0:
            print("  ❌ 清洗后数据为空")
            return None

        return df

    except Exception as e:
        print(f"  ❌ 数据清洗失败: {e}")
        return None


def parse_calibration_file(file_path: str) -> Optional[pd.DataFrame]:
    """
    Dispatch to the correct parser based on file extension.

    Returns a ``(date, total_return)`` DataFrame or ``None``.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".mhtml":
        return load_mhtml_calibration_file(file_path)
    elif ext == ".html":
        return load_html_calibration_file(file_path)
    elif ext in (".csv", ".xlsx", ".xls"):
        return smart_load_calibration_file(file_path)
    else:
        print(f"  ⚠️ 不支持的文件格式: {ext}")
        return None
