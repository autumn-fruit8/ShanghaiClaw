"""
Integration tests for the backtest pipeline.

Tests cover:
- Full backtest pipeline: signals → simulation → metrics
- Monthly JSON output format (series data, sim_actions)
- Report generation (Markdown + PNG charts)
- Single-symbol combined PNG
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skills.analyze.scripts.s4_strategy.pipeline import run_strategy_pipeline


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INITIAL_CASH = 100_000.0
EXPECTED_SERIES_KEYS = {"dates", "log_price", "strategy_equity", "buyhold_value",
                        "cash_array", "shares_array", "roll_trend", "roll_sigma"}
EXPECTED_BACKTEST_KEYS = {"symbol", "name", "type", "backtest_date",
                          "strategy_ret", "strategy_dd", "strat_vol", "strat_sharpe",
                          "buyhold_ret", "buyhold_dd", "buyhold_vol", "buyhold_sharpe",
                          "trades_count", "period_start", "period_end", "period_years"}


def _make_long_df(n=600) -> pd.DataFrame:
    np.random.seed(42)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + np.random.normal(0.0003, 0.015)))
    dates = pd.date_range("2018-01-01", periods=n, freq="D")
    return pd.DataFrame({"date": dates, "val": prices, "total_return": prices})


def _run_pipeline(df, symbol="TEST", strategy="dca-7s") -> dict | None:
    return run_strategy_pipeline(
        df,
        {"symbol": symbol, "name": "Test Asset", "strategy_class": "STEADY"},
        strategy_name=strategy,
        backtest_years=3,
    )


_META = {"symbol": "TEST", "name": "Test", "strategy_class": "STEADY"}


# ═══════════════════════════════════════════════════════════════════════════
# 1. Core pipeline: signals → simulation → metrics
# ═══════════════════════════════════════════════════════════════════════════

def test_backtest_pipeline_returns_all_expected_fields():
    df = _make_long_df(600)
    result = _run_pipeline(df)

    assert result is not None
    assert "meta" in result
    assert "data" in result
    assert "trades" in result

    m = result["meta"]
    for key in ["Strategy_Ret", "BuyHold_Ret", "Strat_Vol", "Strat_Sharpe",
                "Strategy_DD", "BuyHold_DD"]:
        assert key in m, f"Missing meta key: {key}"
        assert isinstance(m[key], float), f"{key} should be float"

    assert isinstance(result["trades"], list)


def test_backtest_vol_and_sharpe_nonzero():
    df = _make_long_df(600)
    result = _run_pipeline(df)
    m = result["meta"]

    vol = m.get("Strat_Vol", 0)
    shrp = m.get("Strat_Sharpe", 0)
    b_vol = m.get("BuyHold_Vol", 0)
    b_shrp = m.get("BuyHold_Sharpe", 0)

    assert vol > 0, f"Strategy Vol should be positive, got {vol}"
    assert b_vol > 0, f"BuyHold Vol should be positive, got {b_vol}"
    assert shrp != 0, f"Strategy Sharpe should be non-zero, got {shrp}"
    assert b_shrp != 0, f"BuyHold Sharpe should be non-zero, got {b_shrp}"


def test_backtest_ret_is_plausible():
    df = _make_long_df(600)
    result = _run_pipeline(df)
    m = result["meta"]

    s_ret = m["Strategy_Ret"]
    b_ret = m["BuyHold_Ret"]
    assert -1.0 < s_ret < 10.0, f"Strategy Ret out of range: {s_ret}"
    assert -1.0 < b_ret < 10.0, f"BuyHold Ret out of range: {b_ret}"


def test_backtest_series_data_present():
    df = _make_long_df(600)
    result = _run_pipeline(df)

    data = result.get("data")
    assert data is not None
    assert "strategy_equity" in data.columns
    assert len(data["strategy_equity"]) == len(data)


def test_backtest_returns_all_series_keys():
    df = _make_long_df(600)
    result = run_strategy_pipeline(
        df, _META, strategy_name="dca-7s", backtest_years=3,
    )

    series = result.get("series", {})
    for key in ["dates", "log_price", "strategy_equity", "buyhold_value",
                "cash_array", "shares_array"]:
        assert key in series, f"Missing series key: {key}"
    assert len(series.get("dates", [])) > 0
    assert len(series.get("cash_array", [])) > 0
    assert len(series.get("shares_array", [])) > 0
    assert len(series["dates"]) == len(series.get("strategy_equity", []))


def test_sim_actions_contains_trade_details():
    df = _make_long_df(600)
    result = _run_pipeline(df)

    for t in result.get("trades", []):
        assert "type" in t
        assert "shares_delta" in t, f"Missing shares_delta in trade: {t}"
        assert "amt_delta" in t, f"Missing amt_delta in trade: {t}"
        assert isinstance(t.get("shares_delta"), (int, float))
        assert isinstance(t.get("amt_delta"), (int, float))


# ═══════════════════════════════════════════════════════════════════════════
# 2. Monthly JSON output format
# ═══════════════════════════════════════════════════════════════════════════

def test_asset_entry_structure():
    df = _make_long_df(600)
    result = _run_pipeline(df)
    m = result["meta"]
    trades = result.get("trades", [])

    entry = {
        "symbol": "TEST",
        "name": "Test Asset",
        "backtest": {
            "symbol": "TEST", "name": "Test Asset",
            "type": m["Type"], "backtest_date": "2026-05-22",
            "strategy_ret": m["Strategy_Ret"], "strategy_dd": m["Strategy_DD"],
            "strat_vol": m["Strat_Vol"], "strat_sharpe": m["Strat_Sharpe"],
            "buyhold_ret": m["BuyHold_Ret"], "buyhold_dd": m["BuyHold_DD"],
            "buyhold_vol": m["BuyHold_Vol"], "buyhold_sharpe": m["BuyHold_Sharpe"],
            "trades_count": len(trades),
            "period_start": m.get("Backtest_Period_Start", "?"),
            "period_end": m.get("Backtest_Period_End", "?"),
            "period_years": m.get("Backtest_Years", 0),
        },
        "signal": m["Signal"],
    }

    for key in EXPECTED_BACKTEST_KEYS:
        assert key in entry["backtest"], f"Missing backtest key: {key}"


def test_asset_entry_json_serializable():
    df = _make_long_df(600)
    result = _run_pipeline(df)
    m = result["meta"]

    entry = {
        "symbol": "TEST",
        "name": "Test",
        "backtest": {
            "symbol": "TEST", "name": "Test",
            "type": m["Type"], "backtest_date": "2026-05-22",
            "strategy_ret": m["Strategy_Ret"], "strategy_dd": m["Strategy_DD"],
            "strat_vol": m["Strat_Vol"], "strat_sharpe": m["Strat_Sharpe"],
            "buyhold_ret": m["BuyHold_Ret"], "buyhold_dd": m["BuyHold_DD"],
            "buyhold_vol": m["BuyHold_Vol"], "buyhold_sharpe": m["BuyHold_Sharpe"],
            "trades_count": len(result.get("trades", [])),
            "period_start": m.get("Backtest_Period_Start", "?"),
            "period_end": m.get("Backtest_Period_End", "?"),
            "period_years": m.get("Backtest_Years", 0),
        },
        "signal": m["Signal"],
    }

    json_str = json.dumps(entry, ensure_ascii=False)
    restored = json.loads(json_str)
    assert restored["symbol"] == "TEST"
    for key in EXPECTED_BACKTEST_KEYS:
        assert key in restored["backtest"], f"Missing after JSON: {key}"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Report generation
# ═══════════════════════════════════════════════════════════════════════════

def test_report_generates_markdown():
    from skills.backtest.scripts.run_backtest import _generate_report

    df = _make_long_df(600)
    result = _run_pipeline(df)
    m = result["meta"]

    asset = {
        "symbol": "TEST", "name": "Test Asset",
        "backtest": {
            "symbol": "TEST", "name": "Test Asset",
            "type": m["Type"], "backtest_date": "2026-05-22",
            "strategy_ret": m["Strategy_Ret"], "strategy_dd": m["Strategy_DD"],
            "strat_vol": m["Strat_Vol"], "strat_sharpe": m["Strat_Sharpe"],
            "buyhold_ret": m["BuyHold_Ret"], "buyhold_dd": m["BuyHold_DD"],
            "buyhold_vol": m["BuyHold_Vol"], "buyhold_sharpe": m["BuyHold_Sharpe"],
            "trades_count": len(result.get("trades", [])),
            "period_start": m.get("Backtest_Period_Start", "?"),
            "period_end": m.get("Backtest_Period_End", "?"),
            "period_years": m.get("Backtest_Years", 0),
        },
    }

    report = _generate_report([asset], "cn", "2026-05-22")
    assert "# Backtest Universe Report" in report
    assert "Test Asset" in report


def test_combined_chart_generates_png():
    from skills.backtest.scripts.run_backtest import _build_asset_entry, _generate_combined_chart

    df = _make_long_df(600)
    result = _run_pipeline(df)
    entry = _build_asset_entry(result, "TEST", "Test Asset", "2026-05-22")

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "test_chart.png"
        _generate_combined_chart(entry, out_path)
        assert out_path.exists(), "PNG file was not created"
        assert out_path.stat().st_size > 5000, f"PNG too small: {out_path.stat().st_size} bytes"


# ═══════════════════════════════════════════════════════════════════════════
# 4. File path conventions
# ═══════════════════════════════════════════════════════════════════════════

def test_backtest_dir_structure():
    from config import BACKTEST_DIR
    assert BACKTEST_DIR.exists()
    assert not (BACKTEST_DIR / "monthly").exists()
    assert not (BACKTEST_DIR / "reports").exists()
    assert (BACKTEST_DIR / "archive").exists()


def test_latest_json_at_root():
    from config import BACKTEST_DIR
    path = BACKTEST_DIR / "latest_cn.json"
    if path.exists():
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) > 0
        assert "symbol" in data[0]
        assert "backtest" in data[0]
