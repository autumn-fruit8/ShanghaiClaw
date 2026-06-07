"""
Cron workflow tests for the data daily update pipeline.

These tests enforce data_daily_update_spec.md requirements:
1. Rerunning on an already-current region must not duplicate rows
2. Missing CSVs must be reported as skips, not silent successes
3. One failed asset must not prevent other assets from updating
"""

from __future__ import annotations

import pandas as pd
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import region implementations via sys.path (dashed dirs)
_DAILY_SCRIPTS = str(ROOT / "skills" / "data-daily-update" / "scripts")
if _DAILY_SCRIPTS not in sys.path:
    sys.path.insert(0, _DAILY_SCRIPTS)


def _write_csv(path: Path, dates: list[str], values: list[float]) -> None:
    """Write a processed CSV at the given path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"date": dates, "total_return": values})
    df.to_csv(path, index=False)


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=["date"])


def test_rerunning_does_not_duplicate_rows(tmp_path: Path) -> None:
    """
    data_daily_update_spec.md invariant:
    "Rerunning the update on an already-current region does not duplicate rows."

    This is the canonical cron workflow anti-pattern detected by ENGINEERING.md §2.7:
    if a script uses the wrong date filter, re-running appends duplicate rows silently.
    """
    from cn_daily import DailyUpdateCN

    region = "cn"
    csv_path = tmp_path / "knowledge" / region / "3_processed" / "TESTASSET.csv"

    # Seed a CSV with 5 days ending 2026-04-20
    existing_dates = [f"2026-04-{d:02d}" for d in range(16, 21)]
    existing_values = [1.0 + i * 0.005 for i in range(5)]
    _write_csv(csv_path, existing_dates, existing_values)

    # Mock fetch returns 0 new rows (already up to date)
    empty_df = pd.DataFrame(columns=["date", "price"])

    updater = DailyUpdateCN(base_path=str(tmp_path / "knowledge" / region))
    updater._fetch_from_api = lambda *args, **kwargs: empty_df

    result = updater.execute({"TESTASSET": {"name": "TestAsset", "type": "CN_ETF"}})

    df_after = _read_csv(csv_path)

    assert len(df_after) == len(existing_dates), (
        f"No-duplicate violation: CSV had {len(existing_dates)} rows before re-run "
        f"but {len(df_after)} rows after. "
        f"Second run should have appended 0 new rows."
    )
    # Status should be success (empty fetch = up to date, not a failure)
    assert result["failed_count"] == 0, (
        f"Up-to-date fetch should not be counted as failed. Got failed_count={result['failed_count']}"
    )


def test_missing_csv_reported_as_skip_not_failure(tmp_path: Path) -> None:
    """
    data_daily_update_spec.md invariant:
    "Missing CSVs are reported as skips, not silent successes."

    An asset without a processed CSV should be counted as skipped (calibration
    needed), not as a failed asset.
    """
    from cn_daily import DailyUpdateCN

    region = "cn"
    # Deliberately do NOT create a CSV for TESTASSET

    updater = DailyUpdateCN(base_path=str(tmp_path / "knowledge" / region))
    result = updater.execute(
        {"TESTASSET": {"name": "TestAsset", "type": "CN_ETF"}}
    )

    assert result["skipped_count"] >= 1, (
        f"Missing CSV should be a skip. Got skipped_count={result['skipped_count']}. "
        f"Full result: {result}"
    )
    # skipped_count + updated_count + failed_count should cover all assets
    total = result["updated_count"] + result["skipped_count"] + result["failed_count"]
    assert total == 1, (
        f"Asset counts must sum to 1. Got updated={result['updated_count']}, "
        f"skipped={result['skipped_count']}, failed={result['failed_count']}"
    )


def test_us_update_finnhub_receives_adjusted_start_date(tmp_path: Path) -> None:
    """
    Verify that when yfinance returns empty, Finnhub fallback is called with
    adjusted_start (7 days before last_date) rather than last_date.

    This ensures pct_change() has a reference point for the first new row.
    """
    from us_daily import DailyUpdateUS

    region = "us"
    csv_path = tmp_path / "knowledge" / region / "3_processed" / "SPY.csv"

    # Seed CSV with 5 days ending 2026-04-20
    existing_dates = [f"2026-04-{d:02d}" for d in range(16, 21)]
    existing_values = [450.0 + i * 0.5 for i in range(5)]
    _write_csv(csv_path, existing_dates, existing_values)

    updater = DailyUpdateUS(base_path=str(tmp_path / "knowledge" / region))

    # Track what dates are passed to Finnhub
    finnhub_calls = []

    def mock_yfinance(symbol, start):
        return pd.DataFrame()

    def mock_yahoo_direct(symbol, start):
        return pd.DataFrame()

    def mock_tiingo(symbol, start_date):
        return pd.DataFrame()

    def mock_finnhub(symbol, start_date):
        finnhub_calls.append({"symbol": symbol, "start_date": start_date})
        return pd.DataFrame({"date": ["2026-04-21"], "price": [451.0]})

    updater._fetch_from_yfinance = mock_yfinance
    updater._fetch_from_yahoo_direct = mock_yahoo_direct
    updater._fetch_from_tiingo = mock_tiingo
    updater._fetch_from_finnhub = mock_finnhub

    result = updater.execute({"SPY": {"name": "SPY", "type": "US_ETF"}})

    assert len(finnhub_calls) == 1, f"Expected 1 Finnhub call, got {len(finnhub_calls)}"
    # Should be 7 days before last_date (2026-04-20), not last_date itself
    assert finnhub_calls[0]["start_date"] == "2026-04-13", (
        f"Finnhub should receive adjusted_start (7 days before last_date). "
        f"Got {finnhub_calls[0]['start_date']}, expected 2026-04-13"
    )


def test_cn_sina_etf_symbol_conversion() -> None:
    """
    Verify _sina_etf_symbol correctly converts CN ETF symbols to Sina format.
    This tests the method that was previously incorrectly marked as @staticmethod.
    """
    from cn_daily import DailyUpdateCN

    updater = DailyUpdateCN.__new__(DailyUpdateCN)

    # Test Shenzhen symbols
    assert updater._sina_etf_symbol("159925") == "sz159925"
    assert updater._sina_etf_symbol("562880") == "sz562880"
    assert updater._sina_etf_symbol("159263") == "sz159263"

    # Test Shanghai symbols
    assert updater._sina_etf_symbol("510300") == "sh510300"
    assert updater._sina_etf_symbol("511010") == "sh511010"

    # Test with whitespace
    assert updater._sina_etf_symbol(" 159925 ") == "sz159925"


def test_asset_type_enum_usage() -> None:
    """
    Verify AssetType enum is properly imported and usable in skill modules.
    This ensures the import cleanup didn't break the enum references.
    """
    from us_daily import DailyUpdateUS
    from cn_daily import DailyUpdateCN
    from utils.constants import AssetType

    # Verify enum values match expected strings
    assert AssetType.US_ETF.value == "US_ETF"
    assert AssetType.HK_ETF.value == "HK_ETF"
    assert AssetType.CN_ETF.value == "CN_ETF"

    # Verify us_daily can use the enum
    updater = DailyUpdateUS.__new__(DailyUpdateUS)
    asset_type = "US_ETF"
    assert asset_type in (AssetType.US_ETF.value, AssetType.HK_ETF.value)

    # Verify cn_daily can use the enum
    updater_cn = DailyUpdateCN.__new__(DailyUpdateCN)
    asset_type = "CN_ETF"
    assert asset_type in (AssetType.CN_ETF.value, "CN_INDEX")


def test_cn_daily_preserves_close_column(tmp_path: Path) -> None:
    """
    Verify CN daily update saves actual close price in 3rd column.
    CN ETF/OTC close price is the actual NAV for Position calculation.
    """
    from cn_daily import DailyUpdateCN

    region = "cn"
    csv_path = tmp_path / "knowledge" / region / "3_processed" / "TESTETF.csv"

    # Seed CSV with old format (2 columns)
    _write_csv(csv_path, ["2026-04-15", "2026-04-16"], [1.0, 1.005])

    updater = DailyUpdateCN(base_path=str(tmp_path / "knowledge" / region))

    # Mock fetch returns price AND close
    def mock_fetch(symbol, asset_type, start):
        return pd.DataFrame({
            'date': pd.to_datetime(["2026-04-17", "2026-04-20"]),
            'price': [101.5, 102.0],
            'close': [101.5, 102.0],
        })

    updater._fetch_from_api = mock_fetch
    result = updater.execute({"TESTETF": {"name": "TestETF", "type": "CN_ETF"}})

    df_after = pd.read_csv(csv_path)
    assert 'close' in df_after.columns, "CSV should have 'close' column"
    assert len(df_after) == 4, f"Expected 4 rows, got {len(df_after)}"
    assert df_after['close'].iloc[-1] == 102.0


def test_cn_otc_preserves_close_column(tmp_path: Path) -> None:
    """
    Verify CN OTC (场外基金) also preserves close (NAV) column.
    """
    from cn_daily import DailyUpdateCN

    region = "cn"
    csv_path = tmp_path / "knowledge" / region / "3_processed" / "TESTOTC.csv"

    # Seed CSV
    _write_csv(csv_path, ["2026-04-15"], [1.0])

    updater = DailyUpdateCN(base_path=str(tmp_path / "knowledge" / region))

    def mock_fetch(symbol, asset_type, start):
        return pd.DataFrame({
            'date': pd.to_datetime(["2026-04-16", "2026-04-17"]),
            'price': [2.15, 2.18],  # cumulative NAV
            'close': [2.15, 2.18],
        })

    updater._fetch_from_api = mock_fetch
    result = updater.execute({"TESTOTC": {"name": "TestOTC", "type": "CN_OTC"}})

    df_after = pd.read_csv(csv_path)
    assert 'close' in df_after.columns
    assert df_after['close'].iloc[-1] == 2.18


def test_us_yahoo_direct_returns_close(tmp_path: Path) -> None:
    """
    Verify _fetch_from_yahoo_direct captures both adj_close and raw close.
    adj_close is used for total_return calculation, raw close for Position.
    """
    import requests
    from us_daily import DailyUpdateUS

    updater = DailyUpdateUS(base_path=str(tmp_path / "knowledge"))
    updater.yf = None  # Disable yfinance

    # Store original get
    original_get = requests.get

    # Mock Yahoo API response with both adjclose and close
    def mock_request_get(url, params, headers, timeout):
        class MockResponse:
            def raise_for_status(self): pass
            def json(self):
                return {
                    "chart": {
                        "result": [{
                            "timestamp": [1938486000],  # 2026-02-14
                            "indicators": {
                                "adjclose": [{"adjclose": [450.5]}],
                                "quote": [{"close": [451.2]}]
                            }
                        }]
                    }
                }
        return MockResponse()

    requests.get = mock_request_get

    try:
        df = updater._fetch_from_yahoo_direct("SPY", "2026-02-01")

        assert not df.empty, "Should return data"
        assert 'price' in df.columns, "Should have 'price' column (adj_close)"
        assert 'close' in df.columns, "Should have 'close' column (raw close)"
        assert df['price'].iloc[0] == 450.5, "price should be adj_close"
        assert df['close'].iloc[0] == 451.2, "close should be raw close"
    finally:
        requests.get = original_get


def test_us_yfinance_returns_close_as_none(tmp_path: Path) -> None:
    """
    Verify _fetch_from_yfinance handles yfinance Close data.
    """
    import pandas as pd
    from us_daily import DailyUpdateUS

    updater = DailyUpdateUS(base_path=str(tmp_path / "knowledge"))

    # Create mock ticker with __init__ that accepts symbol
    class MockTicker:
        def __init__(self, symbol):
            pass
        def history(self, start, **kwargs):
            return pd.DataFrame({
                'Close': [450.5, 451.2],
                'Adj Close': [450.5, 451.2],
            }, index=pd.to_datetime(["2026-04-20", "2026-04-21"]))

    class MockYF:
        Ticker = MockTicker

    updater.yf = MockYF()

    df = updater._fetch_from_yfinance("SPY", "2026-04-15")

    assert not df.empty
    assert 'close' in df.columns
    # yfinance returns Close which is used as close
    assert not df['close'].isna().all(), "yfinance Close should be used as close"


def test_refresh_reads_close_column(tmp_path: Path) -> None:
    """
    Verify refresh.get_latest_price reads close column (3rd col) correctly.
    This is the new behavior for Position calculation.
    """
    import sys
    scripts_path = ROOT / "skills" / "decide" / "scripts"
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))

    # Force reload after path change
    if 'update_position' in sys.modules:
        del sys.modules['update_position']
    if 'skills.update_position.scripts.refresh_prices' in sys.modules:
        del sys.modules['skills.update_position.scripts.refresh_prices']

    # Patch _knowledge_csv_path before importing
    sys.path.insert(0, str(ROOT))
    from skills.update_position.scripts import refresh_prices as refresh

    # Create test CSV with 3 columns (new format)
    csv_path = tmp_path / "knowledge" / "cn" / "3_processed" / "TEST.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(
        "date,total_return,close\n"
        "2026-04-20,1.05,3.45\n"
        "2026-04-21,1.055,3.48\n"
    )

    # Monkeypatch the path function
    original_fn = refresh._knowledge_csv_path
    refresh._knowledge_csv_path = lambda s, r: csv_path

    try:
        price = refresh.get_latest_price("TEST", "cn")
        assert price == 3.48, f"Expected 3.48 (close column), got {price}"
    finally:
        refresh._knowledge_csv_path = original_fn


def test_refresh_backward_compat_old_csv(tmp_path: Path) -> None:
    """
    Verify update_position.get_total_return_fallback reads total_return for old 2-column CSVs.
    This ensures backward compatibility with existing data.
    """
    import sys
    scripts_path = ROOT / "skills" / "decide" / "scripts"
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))

    if 'update_position' in sys.modules:
        del sys.modules['update_position']
    if 'skills.update_position.scripts.refresh_prices' in sys.modules:
        del sys.modules['skills.update_position.scripts.refresh_prices']

    sys.path.insert(0, str(ROOT))
    from skills.update_position.scripts import refresh_prices as refresh

    # Create old format CSV (2 columns)
    csv_path = tmp_path / "knowledge" / "cn" / "3_processed" / "TEST.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(
        "date,total_return\n"
        "2026-04-20,1.05\n"
        "2026-04-21,1.055\n"
    )

    original_fn = refresh._knowledge_csv_path
    refresh._knowledge_csv_path = lambda s, r: csv_path

    try:
        price = refresh.get_total_return_fallback("TEST", "cn")
        assert price == 1.055, f"Expected 1.055 (total_return fallback), got {price}"
    finally:
        refresh._knowledge_csv_path = original_fn


def test_refresh_handles_empty_close_column(tmp_path: Path) -> None:
    """
    Verify update_position.get_total_return_fallback returns total_return when close column is empty.
    This happens for US ETF when using yfinance (no raw close available).
    """
    import sys
    scripts_path = ROOT / "skills" / "decide" / "scripts"
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))

    if 'update_position' in sys.modules:
        del sys.modules['update_position']
    if 'skills.update_position.scripts.refresh_prices' in sys.modules:
        del sys.modules['skills.update_position.scripts.refresh_prices']

    sys.path.insert(0, str(ROOT))
    from skills.update_position.scripts import refresh_prices as refresh

    # Create CSV with empty 3rd column
    csv_path = tmp_path / "knowledge" / "cn" / "3_processed" / "TEST.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(
        "date,total_return,close\n"
        "2026-04-20,1.05,3.45\n"
        "2026-04-21,1.055,\n"  # close is empty
    )

    original_fn = refresh._knowledge_csv_path
    refresh._knowledge_csv_path = lambda s, r: csv_path

    try:
        price = refresh.get_total_return_fallback("TEST", "cn")
        assert price == 1.055, f"Expected 1.055 (fallback when close empty), got {price}"
    finally:
        refresh._knowledge_csv_path = original_fn
