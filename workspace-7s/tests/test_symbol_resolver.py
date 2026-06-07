"""Tests for utils.symbol_resolver — shared symbol/selector resolution."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.symbols.state_resolver import (
    detect_region,
    normalize_region,
    load_state_symbols,
    resolve_symbols,
    resolve_symbols_from_args,
)


def _make_state_dir(root: Path, state: str, symbols: list[str]):
    """Create a temporary state file."""
    (root / "config" / "states").mkdir(parents=True, exist_ok=True)
    path = root / "config" / "states" / f"{state}.json"
    path.write_text(json.dumps({"assets": [{"symbol": s} for s in symbols]}))


# ═══════════════════════════════════════════════════════════════════════════
# detect_region
# ═══════════════════════════════════════════════════════════════════════════

def test_detect_region_cn_six_digit():
    assert detect_region("159207") == "cn"


def test_detect_region_us_alpha():
    assert detect_region("SPY") == "us"


def test_detect_region_hk():
    assert detect_region("3032.HK") == "us"


def test_detect_region_explicit_override():
    assert detect_region("159207", "us") == "us"


# ═══════════════════════════════════════════════════════════════════════════
# normalize_region
# ═══════════════════════════════════════════════════════════════════════════

def test_normalize_region_cn():
    assert normalize_region("cn") == "CN"


def test_normalize_region_us():
    assert normalize_region("us") == "US"


def test_normalize_region_all():
    assert normalize_region("all") == "ALL"


def test_normalize_region_invalid():
    import pytest
    with pytest.raises(ValueError):
        normalize_region("eu")


# ═══════════════════════════════════════════════════════════════════════════
# load_state_symbols
# ═══════════════════════════════════════════════════════════════════════════

def test_load_state_symbols_active():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_state_dir(root, "active", ["159207", "159263", "159222"])
        syms = load_state_symbols(root, "active")
        assert syms == {"159207", "159263", "159222"}


def test_load_state_symbols_nonexistent():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        syms = load_state_symbols(root, "active")
        assert syms == set()


def test_load_state_symbols_empty():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_state_dir(root, "active", [])
        syms = load_state_symbols(root, "active")
        assert syms == set()


# ═══════════════════════════════════════════════════════════════════════════
# resolve_symbols
# ═══════════════════════════════════════════════════════════════════════════

def test_resolve_symbols_single():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        syms = resolve_symbols(root, symbol="159207")
        assert syms == ["159207"]


def test_resolve_symbols_symbols_comma():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        syms = resolve_symbols(root, symbols="159207,159263,159222")
        assert syms == ["159207", "159263", "159222"]


def test_resolve_symbols_active():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_state_dir(root, "active", ["159207", "159263"])
        syms = resolve_symbols(root, use_active_state=True)
        assert "159207" in syms
        assert "159263" in syms


def test_resolve_symbols_active_empty():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_state_dir(root, "active", [])
        syms = resolve_symbols(root, use_active_state=True)
        assert syms == []


def test_resolve_symbols_watchlist():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_state_dir(root, "watchlist", ["519300"])
        syms = resolve_symbols(root, use_watchlist_state=True)
        assert "519300" in syms


def test_resolve_symbols_void():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_state_dir(root, "void", ["000001"])
        syms = resolve_symbols(root, use_void_state=True)
        assert "000001" in syms


def test_resolve_symbols_multiple_selectors_raises():
    import pytest
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with pytest.raises(ValueError):
            resolve_symbols(root, symbol="159207", use_active_state=True)


# ═══════════════════════════════════════════════════════════════════════════
# resolve_symbols_from_args
# ═══════════════════════════════════════════════════════════════════════════

def test_resolve_symbols_from_args_default_returns_all():
    """With no selectors, returns all region assets from manifest (integration)."""
    syms = resolve_symbols_from_args(ROOT, region="all")
    assert len(syms) > 0
    assert all(isinstance(s, str) for s in syms)
