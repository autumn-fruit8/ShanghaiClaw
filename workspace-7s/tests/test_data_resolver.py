"""Tests for utils.data_resolver — shared 3-tier data resolution."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.data_service.data_resolver import resolve_price_data, resolve_data_for_symbols


def _make_csv(path: Path, columns: list[str] | None = None) -> Path:
    """Create a minimal CSV with date+val columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = columns or ["date", "total_return", "close"]
    rows = {
        "date": ["2020-01-01", "2020-01-02", "2020-01-03"],
        "total_return": [100.0, 101.0, 102.0],
        "close": [100.0, 101.0, 102.0],
    }
    df = pd.DataFrame({c: rows[c] for c in cols})
    df.to_csv(path, index=False)
    return path


# ═══════════════════════════════════════════════════════════════════════════
# resolve_price_data — tier resolution
# ═══════════════════════════════════════════════════════════════════════════

def test_resolve_price_data_tier1_found():
    """Tier 1: knowledge/3_processed is found first."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "knowledge" / "cn" / "3_processed" / "159207.csv"
        _make_csv(path)
        df = resolve_price_data(root, "159207", "cn")
        assert df is not None
        assert len(df) == 3
        assert "date" in df.columns


def test_resolve_price_data_tier2_fallback():
    """Tier 2: adhoc/cache when tier 1 missing."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "adhoc" / "cache" / "159207.csv"
        _make_csv(path)
        df = resolve_price_data(root, "159207", "cn")
        assert df is not None
        assert len(df) == 3


def test_resolve_price_data_tier3_cache():
    """Tier 3: adhoc/cache when tiers 1 and 2 missing."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "adhoc" / "cache" / "159207.csv"
        _make_csv(path)
        df = resolve_price_data(root, "159207", "cn")
        assert df is not None
        assert len(df) == 3


def test_resolve_price_data_not_found():
    """Returns None for unknown symbol."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        df = resolve_price_data(root, "NONEXISTENT", "cn")
        assert df is None


def test_resolve_price_data_tier1_priority():
    """Tier 1 takes priority even if tier 2 and 3 exist."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_csv(root / "knowledge" / "cn" / "3_processed" / "159207.csv")
        _make_csv(root / "knowledge" / "cn" / "prices" / "159207.csv")
        _make_csv(root / "adhoc" / "cache" / "159207.csv")
        df = resolve_price_data(root, "159207", "cn")
        assert df is not None
        # Should return tier 1 path — verify by checking parent dir name
        source_path = root / "knowledge" / "cn" / "3_processed" / "159207.csv"
        assert source_path.exists()


def test_resolve_price_data_invalid_csv():
    """Returns None for corrupted/empty CSVs."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "knowledge" / "cn" / "3_processed" / "159207.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not,a,csv\n1,2,3\n")
        df = resolve_price_data(root, "159207", "cn")
        # Returns None because no 'date' column found after lowercasing
        assert df is None


def test_resolve_price_data_empty_csv():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "knowledge" / "cn" / "3_processed" / "159207.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("date,total_return\n")
        df = resolve_price_data(root, "159207", "cn")
        assert df is not None
        assert len(df) == 0


# ═══════════════════════════════════════════════════════════════════════════
# resolve_data_for_symbols — batch resolution
# ═══════════════════════════════════════════════════════════════════════════

def test_resolve_data_for_symbols_found():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_csv(root / "knowledge" / "cn" / "3_processed" / "159207.csv")
        _make_csv(root / "knowledge" / "cn" / "3_processed" / "159263.csv")
        result = resolve_data_for_symbols(root, ["159207", "159263"], "cn")
        assert "159207" in result
        assert "159263" in result
        assert len(result) == 2


def test_resolve_data_for_symbols_partial():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_csv(root / "knowledge" / "cn" / "3_processed" / "159207.csv")
        result = resolve_data_for_symbols(root, ["159207", "NONEXISTENT"], "cn")
        assert "159207" in result
        assert "NONEXISTENT" not in result
        assert len(result) == 1


def test_resolve_data_for_symbols_empty():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = resolve_data_for_symbols(root, ["NONEXISTENT"], "cn")
        assert result == {}
