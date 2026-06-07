"""
Tests for strategy engine compute_signals() + pipeline integration.

Covers:
- compute_signals() returns signals without backtest (transitional old engine)
- Pipeline (run_strategy_pipeline) for backtest + metrics (unified new pipeline)
- Edge cases (empty data, NaN, short history)
"""

from __future__ import annotations

import pandas as pd
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skills.analyze.scripts.strategy import StrategyEngine
from skills.analyze.scripts.s4_strategy.pipeline import run_strategy_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(values: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2020-01-01", periods=len(values), freq="D")
    return pd.DataFrame({
        "date": dates,
        "val": values,
        "total_return": values,
    })


def _make_long_df(n=500) -> pd.DataFrame:
    import numpy as np
    np.random.seed(42)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + np.random.normal(0.0005, 0.01)))
    return _make_df(prices)


_META = {"symbol": "TEST", "name": "Test", "strategy_class": "STEADY"}


# ═══════════════════════════════════════════════════════════════════════════
# compute_signals() tests (transitional old engine path)
# ═══════════════════════════════════════════════════════════════════════════

def test_compute_signals_returns_signal_fields():
    params = {"strategy": {"GLOBAL": {"MA_BASELINE": 50}}}
    engine = StrategyEngine(params=params)
    df = _make_long_df(500)
    result = engine.compute_signals(df, _META)
    assert result is not None
    meta = result["meta"]
    assert "Type" in meta
    assert "Signal" in meta
    assert "Signal_Action" in meta
    assert meta["Type"] == "STEADY"
    assert "[" in meta["Signal"] and "]" in meta["Signal"]


def test_compute_signals_no_backtest_fields():
    params = {"strategy": {"GLOBAL": {"MA_BASELINE": 50}}}
    engine = StrategyEngine(params=params)
    df = _make_long_df(500)
    result = engine.compute_signals(df, _META)
    assert result is not None
    meta = result["meta"]
    assert "Strategy_Ret" not in meta
    assert "BuyHold_Ret" not in meta


def test_compute_signals_has_signal_columns_on_df():
    params = {"strategy": {"GLOBAL": {"MA_BASELINE": 50}}}
    engine = StrategyEngine(params=params)
    df = _make_long_df(500)
    result = engine.compute_signals(df, _META)
    assert result is not None
    data = result["data"]
    for col in ("signal_type", "signal_desc", "sim_action", "sim_param", "sim_desc"):
        assert col in data.columns, f"Missing column: {col}"


def test_compute_signals_no_trades():
    params = {"strategy": {"GLOBAL": {"MA_BASELINE": 50}}}
    engine = StrategyEngine(params=params)
    df = _make_long_df(500)
    result = engine.compute_signals(df, _META)
    assert result is not None
    assert "trades" not in result


def test_compute_signals_none_for_short_history():
    params = {"strategy": {"GLOBAL": {"MA_BASELINE": 250}}}
    engine = StrategyEngine(params=params)
    df = _make_df([1.0, 1.01, 1.02])
    result = engine.compute_signals(df, _META)
    assert result is None


def test_compute_signals_none_for_empty_df():
    params = {"strategy": {"GLOBAL": {"MA_BASELINE": 10}}}
    engine = StrategyEngine(params=params)
    result = engine.compute_signals(pd.DataFrame(), _META)
    assert result is None


def test_compute_signals_none_for_none_df():
    params = {"strategy": {"GLOBAL": {"MA_BASELINE": 10}}}
    engine = StrategyEngine(params=params)
    result = engine.compute_signals(None, _META)
    assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# Pipeline backtest tests (unified new pipeline)
# ═══════════════════════════════════════════════════════════════════════════

def test_pipeline_returns_metrics():
    df = _make_long_df(500)
    result = run_strategy_pipeline(df, _META, strategy_name="dca-7s", backtest_years=3)
    assert result is not None
    meta = result["meta"]
    for field in ("Strategy_Ret", "Strategy_DD", "BuyHold_Ret", "BuyHold_DD",
                  "Strat_Sharpe", "BuyHold_Sharpe"):
        assert field in meta, f"Missing field: {field}"
    assert isinstance(meta["Strategy_Ret"], (int, float))
    assert "trades" in result


def test_pipeline_deterministic():
    df = _make_long_df(500)
    r1 = run_strategy_pipeline(df, _META, strategy_name="dca-7s", backtest_years=3)
    r2 = run_strategy_pipeline(df, _META, strategy_name="dca-7s", backtest_years=3)
    assert r1["meta"]["Strategy_Ret"] == r2["meta"]["Strategy_Ret"]
    assert r1["meta"]["BuyHold_Ret"] == r2["meta"]["BuyHold_Ret"]
    assert len(r1["trades"]) == len(r2["trades"])


def test_pipeline_no_nan_in_metrics():
    df = _make_long_df(500)
    result = run_strategy_pipeline(df, _META, strategy_name="dca-7s", backtest_years=3)
    for key, val in result["meta"].items():
        assert not (isinstance(val, float) and pd.isna(val)), f"NaN in {key}: {val}"


def test_pipeline_signal_columns_on_df():
    df = _make_long_df(500)
    result = run_strategy_pipeline(df, _META, strategy_name="dca-7s", backtest_years=3)
    assert result is not None
    data = result["data"]
    assert "pulse_type" in data.columns, "Missing pulse_type in pipeline output"
    assert "pulse_desc" in data.columns, "Missing pulse_desc in pipeline output"


def test_pipeline_returns_none_when_history_too_short():
    df = _make_df([1.0, 1.01, 1.02, 1.03])
    result = run_strategy_pipeline(df, _META, strategy_name="dca-7s", backtest_years=3)
    assert result is None


def test_pipeline_returns_none_when_dataframe_is_empty():
    result = run_strategy_pipeline(pd.DataFrame(), _META, strategy_name="dca-7s", backtest_years=3)
    assert result is None


def test_pipeline_returns_none_when_all_values_are_nan():
    dates = pd.date_range("2020-01-01", periods=300, freq="D")
    df = pd.DataFrame({"date": dates, "val": [float("nan")] * 300,
                       "total_return": [float("nan")] * 300})
    result = run_strategy_pipeline(df, _META, strategy_name="dca-7s", backtest_years=3)
    assert result is None
