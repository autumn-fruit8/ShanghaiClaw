"""
Unit tests for decide skill — S5 (self-portrait) + S6 (stake) decision layer.

Covers:
- P0: drift formula, stake action, weight normalization, metrics calculation
- P1: Plan CRUD, CSV loading, edge cases
- P2: parser, validators
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Add decide scripts to path for direct imports
_DECIDE_SCRIPTS = str(ROOT / "skills" / "decide" / "scripts")
if _DECIDE_SCRIPTS not in sys.path:
    sys.path.insert(0, _DECIDE_SCRIPTS)

# ---------------------------------------------------------------------------
# P0: Core calculation tests
# ---------------------------------------------------------------------------

class TestDriftFormula:
    """drift = position_weight - target_weight"""

    def test_drift_inside_action(self) -> None:
        """recommend_action correctly handles known drift values"""
        from stake import recommend_action
        # drift=0 → hold
        assert recommend_action(0.0, 0.05) == "hold"
        # drift > threshold → over-weight → sell
        assert recommend_action(0.06, 0.05) == "sell"
        # drift < -threshold → under-weight → buy
        assert recommend_action(-0.06, 0.05) == "buy"

    def test_drift_value_computation(self) -> None:
        """Raw drift = current_weight - target_weight"""
        # market_value=180000, total=500000, target=0.30
        # current_weight = 0.36, drift = 0.36 - 0.30 = 0.06
        current_weight = 180000 / 500000
        target_weight = 0.30
        drift = current_weight - target_weight
        assert abs(drift - 0.06) < 1e-9

    def test_drift_at_threshold(self) -> None:
        """Exactly at threshold boundary"""
        from stake import recommend_action
        # At threshold → hold
        assert recommend_action(0.05, 0.05) == "hold"
        assert recommend_action(-0.05, 0.05) == "hold"


class TestStakeAction:
    """recommend_action: hold / buy / sell based on drift and threshold"""

    def test_within_threshold_hold(self) -> None:
        """|drift| <= threshold → hold"""
        from stake import recommend_action
        assert recommend_action(0.03, 0.05) == "hold"
        assert recommend_action(-0.03, 0.05) == "hold"

    def test_over_threshold_buy(self) -> None:
        """drift < -threshold → under-weight → buy"""
        from stake import recommend_action
        assert recommend_action(-0.051, 0.05) == "buy"
        assert recommend_action(-0.10, 0.05) == "buy"

    def test_over_threshold_sell(self) -> None:
        """drift > threshold → over-weight → sell"""
        from stake import recommend_action
        assert recommend_action(0.051, 0.05) == "sell"
        assert recommend_action(0.10, 0.05) == "sell"

    def test_just_over_threshold(self) -> None:
        """Just over threshold: should trade"""
        from stake import recommend_action
        assert recommend_action(-0.0501, 0.05) == "buy"
        assert recommend_action(0.0501, 0.05) == "sell"


class TestWeightNormalization:
    """_normalize_weights: sum to 1.0, negatives → 0"""

    def test_valid_weights(self) -> None:
        """Already normalized weights unchanged"""
        from skills.review_plan.scripts.review_plan import _normalize_weights
        result = _normalize_weights({"A": 0.4, "B": 0.3, "C": 0.3})
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_unnormalized(self) -> None:
        """Proportional rescaling"""
        from skills.review_plan.scripts.review_plan import _normalize_weights
        result = _normalize_weights({"A": 80, "B": 20})
        assert abs(result["A"] - 0.8) < 1e-9
        assert abs(result["B"] - 0.2) < 1e-9

    def test_negative_weight_clamped(self) -> None:
        """Negative weights → 0"""
        from skills.review_plan.scripts.review_plan import _normalize_weights
        result = _normalize_weights({"A": 1.0, "B": -0.5})
        assert result["A"] == 1.0
        assert result["B"] == 0.0

    def test_empty_weights(self) -> None:
        """Empty dict → empty dict"""
        from skills.review_plan.scripts.review_plan import _normalize_weights
        result = _normalize_weights({})
        assert result == {}

    def test_all_zero_weights(self) -> None:
        """All zeros → empty dict"""
        from skills.review_plan.scripts.review_plan import _normalize_weights
        result = _normalize_weights({"A": 0, "B": 0})
        assert result == {}


class TestMetricsCalculation:
    """Portfolio metrics: total_return, max_drawdown, sharpe, sortino, calmar"""

    def test_total_return(self) -> None:
        """total_return = (final / initial) - 1"""
        from skills.review_plan.scripts.review_plan import total_return
        series = pd.Series([100.0, 110.0, 90.0, 120.0])
        result = total_return(series)
        assert abs(result - 0.20) < 1e-9

    def test_total_return_single_point(self) -> None:
        """Less than 2 points → 0"""
        from skills.review_plan.scripts.review_plan import total_return
        assert total_return(pd.Series([100.0])) == 0.0

    def test_max_drawdown(self) -> None:
        """Max peak-to-trough decline as positive percentage"""
        from skills.review_plan.scripts.review_plan import max_drawdown
        # 100 → 110 → 80 → 105: peak=110, trough=80, dd=(80-110)/110=-0.273
        series = pd.Series([100.0, 110.0, 80.0, 105.0])
        result = max_drawdown(series)
        assert abs(result - 0.2727) < 0.01

    def test_max_drawdown_no_drawdown(self) -> None:
        """Always increasing → 0"""
        from skills.review_plan.scripts.review_plan import max_drawdown
        series = pd.Series([100.0, 105.0, 110.0, 120.0])
        assert max_drawdown(series) == 0.0

    def test_annualized_return(self) -> None:
        """Annualized geometric return"""
        from skills.review_plan.scripts.review_plan import annualized_return
        # 252 days, 20% total return → ~20% annualized
        series = pd.Series([100.0] * 252 + [120.0])
        result = annualized_return(series)
        assert abs(result - 0.20) < 0.01

    def test_sharpe_ratio(self) -> None:
        """Sharpe = (ann_ret - rf) / ann_vol * sqrt(252)"""
        from skills.review_plan.scripts.review_plan import sharpe_ratio
        # Positive trend → positive Sharpe
        series = pd.Series([100.0 + i for i in range(253)])
        result = sharpe_ratio(series, risk_free_rate=0.04)
        assert result > 0

    def test_sharpe_zero_volatility(self) -> None:
        """Zero volatility → 0 (no division by zero)"""
        from skills.review_plan.scripts.review_plan import sharpe_ratio
        series = pd.Series([100.0] * 10)
        assert sharpe_ratio(series) == 0.0

    def test_sortino_ratio_no_downside(self) -> None:
        """Sortino with no downside returns → 0 (no negative days)"""
        from skills.review_plan.scripts.review_plan import sortino_ratio
        # Strictly increasing → no downside → 0
        series = pd.Series([100.0 + i for i in range(253)])
        result = sortino_ratio(series, risk_free_rate=0.04)
        assert result == 0.0

    def test_sortino_ratio_with_downside(self) -> None:
        """Sortino with both up and down days → positive"""
        from skills.review_plan.scripts.review_plan import sortino_ratio
        import numpy as np
        # Mixed returns with some downside
        np.random.seed(42)
        returns = np.random.randn(253) * 0.01 + 0.0005  # mean positive
        series = pd.Series([100.0 * (1 + r) for r in np.cumsum(returns)])
        result = sortino_ratio(series, risk_free_rate=0.04)
        assert result > 0

    def test_calmar_ratio(self) -> None:
        """Calmar = annualized_return / max_drawdown"""
        from skills.review_plan.scripts.review_plan import calmar_ratio
        series = pd.Series([100.0] * 100 + [120.0] * 10 + [110.0] * 150)
        result = calmar_ratio(series)
        assert result > 0


# ---------------------------------------------------------------------------
# P1: Data flow tests
# ---------------------------------------------------------------------------

class TestPlanCRUD:
    """Plan create/read/update/delete lifecycle"""

    def _write_plan_json(self, tmp_path: Path, plan_id: str, version: int, payload: dict) -> Path:
        plan_dir = tmp_path / "config" / "plans" / plan_id
        plan_dir.mkdir(parents=True, exist_ok=True)
        path = plan_dir / f"v{version}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def test_list_plan_versions(self, tmp_path: Path) -> None:
        """List versions returns correct sorted list"""
        from dao.config_dao import list_plan_versions
        self._write_plan_json(tmp_path, "test_plan", 1, {"plan_id": "test_plan", "version": 1})
        self._write_plan_json(tmp_path, "test_plan", 2, {"plan_id": "test_plan", "version": 2})
        self._write_plan_json(tmp_path, "test_plan", 3, {"plan_id": "test_plan", "version": 3})

        plans_root = tmp_path / "config" / "plans"
        versions = list_plan_versions("test_plan", plans_root)
        assert versions == [1, 2, 3]

    def test_list_plan_versions_nonexistent(self, tmp_path: Path) -> None:
        """Non-existent plan → empty list"""
        from dao.config_dao import list_plan_versions
        plans_root = tmp_path / "config" / "plans"
        versions = list_plan_versions("nonexistent", plans_root)
        assert versions == []

    def test_load_plan_latest_version(self, tmp_path: Path) -> None:
        """load_plan with version=None loads latest"""
        from dao.config_dao import load_plan
        payload = {
            "plan_id": "test_hb",
            "region": "CN",
            "currency": "CNY",
            "target_market_value": 500000,
            "constraints": {"drift_threshold": 0.05},
            "all_assets": [
                {"symbol": "159263", "target_weight": 0.50},
                {"symbol": "003376", "target_weight": 0.50},
            ],
        }
        self._write_plan_json(tmp_path, "test_hb", 1, {**payload, "version": 1})
        self._write_plan_json(tmp_path, "test_hb", 2, {**payload, "version": 2})

        plans_root = tmp_path / "config" / "plans"
        plan = load_plan("test_hb", version=None, plans_root=plans_root)
        assert plan.version == 2  # latest

    def test_load_plan_specific_version(self, tmp_path: Path) -> None:
        """load_plan with version=N loads that version"""
        from dao.config_dao import load_plan
        payload = {
            "plan_id": "test_hb",
            "region": "CN",
            "currency": "CNY",
            "all_assets": [
                {"symbol": "A", "target_weight": 0.50},
            ],
        }
        self._write_plan_json(tmp_path, "test_hb", 1, {**payload, "version": 1})
        self._write_plan_json(tmp_path, "test_hb", 2, {**payload, "version": 2})

        plans_root = tmp_path / "config" / "plans"
        plan = load_plan("test_hb", version=1, plans_root=plans_root)
        assert plan.version == 1

    def test_plan_asset_weights(self, tmp_path: Path) -> None:
        """Plan assets have correct target_weight"""
        from dao.config_dao import load_plan
        payload = {
            "plan_id": "weights_test",
            "region": "US",
            "currency": "USD",
            "all_assets": [
                {"symbol": "AAPL", "target_weight": 0.40},
                {"symbol": "GOOGL", "target_weight": 0.35},
                {"symbol": "BOND", "target_weight": 0.25},
            ],
        }
        self._write_plan_json(tmp_path, "weights_test", 1, {**payload, "version": 1})

        plans_root = tmp_path / "config" / "plans"
        plan = load_plan("weights_test", version=1, plans_root=plans_root)
        assert len(plan.all_assets) == 3
        weights = {a.symbol: a.target_weight for a in plan.all_assets}
        assert weights["AAPL"] == 0.40
        assert weights["GOOGL"] == 0.35
        assert weights["BOND"] == 0.25


class TestCSVLoading:
    """load_total_return_series with various edge cases"""

    def _write_csv(self, tmp_path: Path, filename: str, dates: list[str], values: list[float]) -> Path:
        csv_path = tmp_path / "knowledge" / "cn" / "3_processed" / filename
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({"date": dates, "total_return": values})
        df.to_csv(csv_path, index=False)
        return csv_path

    def test_load_csv_basic(self, tmp_path: Path) -> None:
        """Basic CSV loading with lookback trim"""
        dates = [f"2026-04-{d:02d}" for d in range(1, 11)]
        values = [1000.0 + i * 10 for i in range(10)]
        self._write_csv(tmp_path, "TEST.csv", dates, values)

        from skills.review_plan.scripts.review_plan import load_total_return_series
        series = load_total_return_series(
            tmp_path / "knowledge" / "cn" / "3_processed" / "TEST.csv",
            lookback_days=5
        )
        assert series is not None
        assert len(series) == 5  # trimmed to lookback

    def test_load_csv_missing_file(self, tmp_path: Path) -> None:
        """Missing CSV → None"""
        from skills.review_plan.scripts.review_plan import load_total_return_series
        result = load_total_return_series(
            tmp_path / "nonexistent.csv"
        )
        assert result is None

    def test_load_csv_missing_column(self, tmp_path: Path) -> None:
        """CSV without total_return column → None"""
        bad_csv = tmp_path / "bad.csv"
        bad_csv.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"date": ["2026-04-01"], "price": [100.0]}).to_csv(bad_csv, index=False)

        from skills.review_plan.scripts.review_plan import load_total_return_series
        result = load_total_return_series(bad_csv)
        assert result is None

    def test_load_csv_empty(self, tmp_path: Path) -> None:
        """Empty CSV → None"""
        empty_csv = tmp_path / "empty.csv"
        empty_csv.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(columns=["date", "total_return"]).to_csv(empty_csv, index=False)

        from skills.review_plan.scripts.review_plan import load_total_return_series
        result = load_total_return_series(empty_csv)
        assert result is None


# ---------------------------------------------------------------------------
# P2: Parser and validators
# ---------------------------------------------------------------------------

class TestCLI:
    """CLI argument parsing for decide.py"""

    def test_self_portrait_parser(self) -> None:
        """decide.py routes to self-portrait subcommand"""
        from decide import build_parser
        parser = build_parser()
        args = parser.parse_args(["self-portrait", "list"])
        assert args.command == "self-portrait"
        assert args.subcommand == ["list"]

    def test_stake_parser(self) -> None:
        """decide.py routes to stake subcommand (passthrough to stake.py)"""
        from decide import build_parser
        parser = build_parser()
        # Passthrough args: --plan cn_hb goes to stake.py's own parser
        args = parser.parse_args(["stake"])
        assert args.command == "stake"
        assert args.subcommand == []

    def test_decide_parser(self) -> None:
        """decide.py routes to decide subcommand (full workflow)"""
        from decide import build_parser
        parser = build_parser()
        args = parser.parse_args(["decide", "--plan", "cn_hb"])
        assert args.command == "decide"
        assert args.plan == "cn_hb"

    def test_update_position_parser(self) -> None:
        """update_position skill routes to refresh subcommand"""
        from skills.update_position.scripts.update_position import build_parser
        parser = build_parser()
        args = parser.parse_args(["refresh", "--plan", "cn_hb"])
        assert args.command == "refresh"
        assert args.plan == ["cn_hb"]


class TestValidators:
    """Input validation helpers"""

    def test_region_slug(self) -> None:
        """Region string normalization"""
        from skills.review_plan.scripts.review_plan import _region_slug
        assert _region_slug("CN") == "cn"
        assert _region_slug("cn") == "cn"
        assert _region_slug("  US  ") == "us"
        assert _region_slug("") == "all"
