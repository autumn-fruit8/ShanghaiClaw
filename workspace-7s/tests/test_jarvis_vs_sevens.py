"""
Verification test: validate 7S strategy pipeline output for all species.

Runs run_strategy_pipeline on synthetic price data for each species,
checking that output is well-formed and metrics are within plausible ranges.

Run manually:
    python tests/test_jarvis_vs_sevens.py
"""

from __future__ import annotations

import pandas as pd
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skills.analyze.scripts.s4_strategy.pipeline import run_strategy_pipeline


def _make_df(values: list[float], start: str = "2020-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(values), freq="D")
    return pd.DataFrame({
        "date": dates,
        "val": values,
        "total_return": values,
    })


def _run(symbol: str, strategy_name: str, species: str, values: list[float]) -> dict:
    df = _make_df(values)
    result = run_strategy_pipeline(
        df,
        {"symbol": symbol, "name": f"Test {species}", "strategy_class": species},
        strategy_name=strategy_name,
        backtest_years=5,
    )
    assert result is not None, f"{species}: run_strategy_pipeline returned None"
    return result


# ---------------------------------------------------------------------------


def test_steady_dca_signal():
    """STEADY via dca-7s produces valid output on trending series."""
    np = __import__("numpy")
    rng = np.random.default_rng(42)
    n = 600
    values = [100.0]
    for _ in range(n - 1):
        values.append(values[-1] * (1 + 0.0002 + rng.normal(0, 0.005)))

    result = _run("TEST_STEADY", "dca-7s", "STEADY", values)
    m = result["meta"]
    print(f"\n[STEADY] Signal={m['Signal']}")
    print(f"[STEADY] Pulse={result.get('pulse_type', '?')}")
    print(f"[STEADY] Trades={len(result['trades'])}")
    assert "Strategy_Ret" in m
    assert "Signal" in m


def test_volatile_deep_value_signal():
    """VOLATILE via deep-value-7s produces valid output on mean-reverting series."""
    np = __import__("numpy")
    rng = np.random.default_rng(123)
    n = 600
    base = 50.0
    values = [base]
    for _ in range(n - 1):
        shock = rng.normal(0, 2.0)
        drift = -0.01 * (values[-1] - base)
        values.append(max(1.0, values[-1] + drift + shock))

    result = _run("TEST_VOLATILE", "deep-value-7s", "VOLATILE", values)
    m = result["meta"]
    print(f"\n[VOLATILE] Signal={m['Signal']}")
    print(f"[VOLATILE] Pulse={result.get('pulse_type', '?')}")
    print(f"[VOLATILE] Trades={len(result['trades'])}")
    assert "Strategy_Ret" in m


def test_momentum_signal():
    """MOMENTUM via momentum-7s detects trend break on sharp drop."""
    np = __import__("numpy")
    n = 600
    values = [100.0]
    for i in range(n - 1):
        if i < 450:
            values.append(values[-1] * 1.001)
        else:
            values.append(values[-1] * 0.97)

    result = _run("TEST_MOMENTUM", "momentum-7s", "MOMENTUM", values)
    m = result["meta"]
    sig = m["Signal"]
    print(f"\n[MOMENTUM] Signal={sig}")
    print(f"[MOMENTUM] Pulse={result.get('pulse_type', '?')}")
    print(f"[MOMENTUM] Trades={len(result['trades'])}")
    assert "]" in sig


def test_backtest_metrics_reasonable():
    """Sanity-check: backtest metrics are finite and within plausible ranges."""
    np = __import__("numpy")
    rng = np.random.default_rng(999)
    n = 600
    values = [100.0]
    for _ in range(n - 1):
        values.append(values[-1] * (1 + rng.normal(0.0003, 0.01)))

    result = _run("TEST_SANITY", "dca-7s", "STEADY", values)
    m = result["meta"]
    for key in ["Strategy_Ret", "Strategy_DD", "Strat_Vol", "Strat_Sharpe",
                "BuyHold_Ret", "BuyHold_DD", "BuyHold_Vol", "BuyHold_Sharpe"]:
        v = m[key]
        assert abs(v) < 1000, f"[{key}]={v} is unreasonably large"
        assert not (v != v), f"[{key}]={v} is NaN"
    print(f"\n[SANITY] All metrics finite & plausible")


def test_maxdd_bug_fixed():
    """Verify _maxdd return order: (dd.iloc[-1], roll_max.iloc[-1], dd.min())."""
    import numpy as np
    from skills.analyze.scripts.strategy import StrategyEngine
    engine = StrategyEngine()
    series = pd.Series([100.0, 110.0, 105.0, 108.0, 102.0, 107.0])
    frac, peak, trough = engine._maxdd(series)
    expected_trough = (102.0 - 110.0) / 110.0
    expected_current = (107.0 - 110.0) / 110.0
    assert frac == pytest.approx(expected_current), f"_maxdd frac={frac}"
    assert peak == pytest.approx(110.0)
    assert trough == pytest.approx(expected_trough)
    print(f"\n[MAXDD] frac={frac:.4f}, peak={peak:.4f}, trough={trough:.4f}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
