"""
Unit tests for apply_trades module.

Covers:
- P0: CSV validation, update/delete/add operations, dry_run mode
- P1: Archive behavior, multi-plan CSV, edge cases
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Add update_position scripts to path
_UPDATE_POSITION = str(ROOT / "skills" / "update_position" / "scripts")
if _UPDATE_POSITION not in sys.path:
    sys.path.insert(0, _UPDATE_POSITION)

from skills.update_position.scripts.apply_trades import (
    BrokerUpdateEntry,
    ApplyResult,
    ValidationError,
    validate_file,
    apply_trades,
    _apply_plan_updates,
    _archive_csv,
)
from skills.update_position.scripts import apply_trades as apply_trades_module


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def temp_positions_root(tmp_path: Path, monkeypatch) -> Path:
    """Temp positions root with mock POSITIONS_DIR."""
    pos_root = tmp_path / "positions"
    pos_root.mkdir(parents=True)
    monkeypatch.setattr(apply_trades_module, "POSITIONS_DIR", pos_root)
    return pos_root


@pytest.fixture
def sample_position(temp_positions_root: Path) -> dict:
    """Create a sample position JSON for us_hb plan."""
    pos_dir = temp_positions_root / "us_hb"
    pos_dir.mkdir(parents=True)
    data = {
        "plan_id": "us_hb",
        "plan_version": 1,
        "snapshot_date": "2026-05-07",
        "total_market_value": 50000.0,
        "positions": [
            {"symbol": "SPYM", "name": "S&P 500", "shares": 20.0, "current_price": 100.0, "market_value": 2000.0},
            {"symbol": "TLT", "name": "Treasury Bond", "shares": 50.0, "current_price": 80.0, "market_value": 4000.0},
            {"symbol": "GLDM", "name": "Gold", "shares": 30.0, "current_price": 50.0, "market_value": 1500.0},
        ],
    }
    path = pos_dir / "2026-05-07.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def write_csv(path: Path, rows: list[dict]) -> Path:
    """Write a CSV file with given rows (list of dicts)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        # Empty CSV with just header
        path.write_text("plan_id,symbol,name,current_shares,new_shares\n", encoding="utf-8")
    else:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["plan_id", "symbol", "name", "current_shares", "new_shares"])
            writer.writeheader()
            writer.writerows(rows)
    return path


# ─── P0: CSV Validation Tests ──────────────────────────────────────────────────


class TestValidateFile:
    """validate_file: parse and validate broker updates CSV."""

    def test_file_not_found_raises(self, temp_positions_root: Path) -> None:
        """Non-existent file → ValidationError."""
        with pytest.raises(ValidationError, match="File not found"):
            validate_file(temp_positions_root / "nonexistent.csv")

    def test_missing_plan_id_raises(self, temp_positions_root: Path) -> None:
        """Row without plan_id → ValidationError."""
        csv_path = write_csv(temp_positions_root / "test.csv", [
            {"plan_id": "", "symbol": "AAPL", "name": "Apple", "current_shares": "10", "new_shares": "20"},
        ])
        with pytest.raises(ValidationError, match="Missing plan_id"):
            validate_file(csv_path)

    def test_missing_symbol_raises(self, temp_positions_root: Path) -> None:
        """Row without symbol → ValidationError."""
        csv_path = write_csv(temp_positions_root / "test.csv", [
            {"plan_id": "test_plan", "symbol": "", "name": "Test", "current_shares": "10", "new_shares": "20"},
        ])
        with pytest.raises(ValidationError, match="Missing plan_id or symbol"):
            validate_file(csv_path)

    def test_invalid_current_shares_raises(self, temp_positions_root: Path) -> None:
        """Non-numeric current_shares → ValidationError."""
        csv_path = write_csv(temp_positions_root / "test.csv", [
            {"plan_id": "test_plan", "symbol": "AAPL", "name": "Apple", "current_shares": "abc", "new_shares": "20"},
        ])
        with pytest.raises(ValidationError, match="Invalid current_shares"):
            validate_file(csv_path)

    def test_invalid_new_shares_raises(self, temp_positions_root: Path) -> None:
        """Non-numeric new_shares → ValidationError."""
        csv_path = write_csv(temp_positions_root / "test.csv", [
            {"plan_id": "test_plan", "symbol": "AAPL", "name": "Apple", "current_shares": "10", "new_shares": "xyz"},
        ])
        with pytest.raises(ValidationError, match="Invalid new_shares"):
            validate_file(csv_path)

    def test_valid_csv_parses_correctly(self, temp_positions_root: Path) -> None:
        """Valid CSV → list of BrokerUpdateEntry with correct actions."""
        csv_path = write_csv(temp_positions_root / "test.csv", [
            {"plan_id": "plan_a", "symbol": "AAPL", "name": "Apple", "current_shares": "10", "new_shares": "20"},
            {"plan_id": "plan_a", "symbol": "GOOGL", "name": "Google", "current_shares": "5", "new_shares": "5"},  # no change
            {"plan_id": "plan_a", "symbol": "MSFT", "name": "Microsoft", "current_shares": "0", "new_shares": ""},  # skip
        ])
        entries = validate_file(csv_path)

        assert len(entries) == 3

        # Update: new_shares != current_shares
        assert entries[0].action == "update"
        assert entries[0].current_shares == 10.0
        assert entries[0].new_shares == 20.0

        # Skip: new_shares == current_shares
        assert entries[1].action == "skip"
        assert entries[1].current_shares == 5.0
        assert entries[1].new_shares == 5.0

        # Skip: empty new_shares
        assert entries[2].action == "skip"
        assert entries[2].current_shares == 0.0
        assert entries[2].new_shares is None

    def test_empty_current_shares_defaults_to_zero(self, temp_positions_root: Path) -> None:
        """Empty current_shares → 0.0."""
        csv_path = write_csv(temp_positions_root / "test.csv", [
            {"plan_id": "plan_a", "symbol": "AAPL", "name": "Apple", "current_shares": "", "new_shares": "50"},
        ])
        entries = validate_file(csv_path)
        assert entries[0].current_shares == 0.0
        assert entries[0].action == "update"

    def test_decimal_shares(self, temp_positions_root: Path) -> None:
        """Decimal share values are parsed correctly."""
        csv_path = write_csv(temp_positions_root / "test.csv", [
            {"plan_id": "plan_a", "symbol": "AAPL", "name": "Apple", "current_shares": "10.5", "new_shares": "20.75"},
        ])
        entries = validate_file(csv_path)
        assert entries[0].current_shares == 10.5
        assert entries[0].new_shares == 20.75


# ─── P0: Core apply_trades Tests ──────────────────────────────────────────────


class TestApplyTradesUpdate:
    """apply_trades: update existing symbol shares."""

    def test_update_existing_symbol(self, temp_positions_root: Path, sample_position: dict) -> None:
        """Change in shares → position updated."""
        csv_path = write_csv(temp_positions_root / "apply.csv", [
            {"plan_id": "us_hb", "symbol": "SPYM", "name": "S&P 500", "current_shares": "20", "new_shares": "25"},
            {"plan_id": "us_hb", "symbol": "TLT", "name": "Treasury Bond", "current_shares": "50", "new_shares": "50"},
            {"plan_id": "us_hb", "symbol": "GLDM", "name": "Gold", "current_shares": "30", "new_shares": "30"},
        ])

        result = apply_trades(csv_path, dry_run=False)

        assert result.success is True
        # SPYM updated, TLT/GLDM skipped (in CSV with same values)
        assert ("SPYM", 20.0, 25.0) in result.updated
        assert ("TLT", 50.0, 50.0) not in result.updated  # no change
        assert len(result.deleted) == 0

        # Verify file was saved
        saved = temp_positions_root / "us_hb" / f"{date.today().isoformat()}.json"
        assert saved.exists()
        with open(saved) as f:
            saved_data = json.load(f)
        spym = next(p for p in saved_data["positions"] if p["symbol"] == "SPYM")
        assert spym["shares"] == 25.0

    def test_no_change_skipped(self, temp_positions_root: Path, sample_position: dict) -> None:
        """new_shares == current_shares → skipped, no file update."""
        csv_path = write_csv(temp_positions_root / "apply.csv", [
            {"plan_id": "us_hb", "symbol": "SPYM", "name": "S&P 500", "current_shares": "20", "new_shares": "20"},
            {"plan_id": "us_hb", "symbol": "TLT", "name": "Treasury Bond", "current_shares": "50", "new_shares": "50"},
            {"plan_id": "us_hb", "symbol": "GLDM", "name": "Gold", "current_shares": "30", "new_shares": "30"},
        ])

        result = apply_trades(csv_path, dry_run=False)

        assert result.success is True
        assert len(result.updated) == 0  # No changes
        assert ("SPYM", "no change") in result.skipped
        assert ("TLT", "no change") in result.skipped
        assert ("GLDM", "no change") in result.skipped
        assert len(result.deleted) == 0  # All symbols kept (in CSV)

    def test_add_new_symbol(self, temp_positions_root: Path, sample_position: dict) -> None:
        """Symbol not in position → added."""
        csv_path = write_csv(temp_positions_root / "apply.csv", [
            {"plan_id": "us_hb", "symbol": "BND", "name": "Total Bond", "current_shares": "0", "new_shares": "100"},
        ])

        result = apply_trades(csv_path, dry_run=False)

        assert result.success is True
        assert ("BND", 0.0, 100.0) in result.updated

        # Verify in saved file
        saved = temp_positions_root / "us_hb" / f"{date.today().isoformat()}.json"
        with open(saved) as f:
            saved_data = json.load(f)
        bnd = next((p for p in saved_data["positions"] if p["symbol"] == "BND"), None)
        assert bnd is not None
        assert bnd["shares"] == 100.0

    def test_delete_symbol_not_in_csv(self, temp_positions_root: Path, sample_position: dict) -> None:
        """Symbol in position but not in CSV → deleted."""
        csv_path = write_csv(temp_positions_root / "apply.csv", [
            {"plan_id": "us_hb", "symbol": "SPYM", "name": "S&P 500", "current_shares": "20", "new_shares": "20"},
        ])

        result = apply_trades(csv_path, dry_run=False)

        assert result.success is True
        assert "GLDM" in result.deleted
        assert "TLT" in result.deleted
        # SPYM kept (in CSV, unchanged)
        # GLDM and TLT deleted (not in CSV)


# ─── P0: Dry Run Tests ────────────────────────────────────────────────────────


class TestApplyTradesDryRun:
    """apply_trades with dry_run=True: preview without saving."""

    def test_dry_run_does_not_save(self, temp_positions_root: Path, sample_position: dict) -> None:
        """Dry run → no file created or modified."""
        csv_path = write_csv(temp_positions_root / "apply.csv", [
            {"plan_id": "us_hb", "symbol": "SPYM", "name": "S&P 500", "current_shares": "20", "new_shares": "99"},
        ])

        result = apply_trades(csv_path, dry_run=True)

        assert result.success is True
        # No file with today's date
        today_file = temp_positions_root / "us_hb" / f"{date.today().isoformat()}.json"
        assert not today_file.exists()
        # Original file untouched
        orig_file = temp_positions_root / "us_hb" / "2026-05-07.json"
        assert orig_file.exists()

    def test_dry_run_shows_preview(self, temp_positions_root: Path, sample_position: dict) -> None:
        """Dry run returns same result as non-dry-run (except archiving)."""
        csv_path = write_csv(temp_positions_root / "apply.csv", [
            {"plan_id": "us_hb", "symbol": "SPYM", "name": "S&P 500", "current_shares": "20", "new_shares": "99"},
        ])

        dry_result = apply_trades(csv_path, dry_run=True)

        assert ("SPYM", 20.0, 99.0) in dry_result.updated
        assert "GLDM" in dry_result.deleted
        assert "TLT" in dry_result.deleted


# ─── P1: Archive Tests ─────────────────────────────────────────────────────────


class TestArchive:
    """_archive_csv: move CSV to archive after successful apply."""

    def test_csv_archived_after_apply(self, temp_positions_root: Path, sample_position: dict) -> None:
        """Non-dry-run → CSV moved to archive/."""
        csv_path = write_csv(temp_positions_root / "apply.csv", [
            {"plan_id": "us_hb", "symbol": "SPYM", "name": "S&P 500", "current_shares": "20", "new_shares": "25"},
        ])

        result = apply_trades(csv_path, dry_run=False)

        assert result.success is True
        assert not csv_path.exists()  # Original moved
        archive_path = temp_positions_root / "archive" / f"apply_trade_{date.today().isoformat()}.csv"
        assert archive_path.exists()

    def test_dry_run_does_not_archive(self, temp_positions_root: Path, sample_position: dict) -> None:
        """Dry run → CSV not archived."""
        csv_path = write_csv(temp_positions_root / "apply.csv", [
            {"plan_id": "us_hb", "symbol": "SPYM", "name": "S&P 500", "current_shares": "20", "new_shares": "25"},
        ])

        apply_trades(csv_path, dry_run=True)

        assert csv_path.exists()  # Still there
        archive_path = temp_positions_root / "archive" / f"apply_trade_{date.today().isoformat()}.csv"
        assert not archive_path.exists()

    def test_archive_dir_created_if_missing(self, temp_positions_root: Path, sample_position: dict) -> None:
        """Archive dir doesn't exist → created automatically."""
        csv_path = write_csv(temp_positions_root / "apply.csv", [
            {"plan_id": "us_hb", "symbol": "SPYM", "name": "S&P 500", "current_shares": "20", "new_shares": "25"},
        ])

        apply_trades(csv_path, dry_run=False)

        archive_path = temp_positions_root / "archive" / f"apply_trade_{date.today().isoformat()}.csv"
        assert archive_path.exists()


# ─── P1: Multi-Plan Tests ──────────────────────────────────────────────────────


class TestMultiPlanCSV:
    """CSV with entries for multiple plans."""

    def test_multi_plan_csv(self, temp_positions_root: Path, sample_position: dict) -> None:
        """Entries for multiple plans → each processed independently."""
        # Add another plan's position
        pos_dir = temp_positions_root / "cn_hb"
        pos_dir.mkdir(parents=True)
        cn_data = {
            "plan_id": "cn_hb",
            "plan_version": 1,
            "snapshot_date": "2026-05-07",
            "positions": [
                {"symbol": "159263", "name": "Value 100", "shares": 100.0, "current_price": 10.0, "market_value": 1000.0},
            ],
        }
        (pos_dir / "2026-05-07.json").write_text(json.dumps(cn_data), encoding="utf-8")

        csv_path = write_csv(temp_positions_root / "apply.csv", [
            {"plan_id": "us_hb", "symbol": "SPYM", "name": "S&P 500", "current_shares": "20", "new_shares": "30"},
            {"plan_id": "cn_hb", "symbol": "159263", "name": "Value 100", "current_shares": "100", "new_shares": "200"},
        ])

        result = apply_trades(csv_path, dry_run=False)

        assert result.success is True
        assert ("SPYM", 20.0, 30.0) in result.updated
        assert ("159263", 100.0, 200.0) in result.updated


# ─── P1: Edge Cases ───────────────────────────────────────────────────────────


class TestApplyTradesEdgeCases:
    """Edge cases and error handling."""

    def test_nonexistent_plan_creates_new_position(self, temp_positions_root: Path, sample_position: dict) -> None:
        """Entry for non-existent plan → creates new empty position and adds symbol.
        
        Position.load() is permissive: if plan doesn't exist, it returns an empty Position.
        """
        csv_path = write_csv(temp_positions_root / "apply.csv", [
            {"plan_id": "nonexistent_plan", "symbol": "AAPL", "name": "Apple", "current_shares": "0", "new_shares": "20"},
        ])

        result = apply_trades(csv_path, dry_run=False)

        # Code is permissive - creates new position for non-existent plan
        assert result.success is True
        assert ("AAPL", 0.0, 20.0) in result.updated

        # Verify new position file was created
        saved = temp_positions_root / "nonexistent_plan" / f"{date.today().isoformat()}.json"
        assert saved.exists()

    def test_empty_csv(self, temp_positions_root: Path, sample_position: dict) -> None:
        """CSV with no rows → error."""
        csv_path = write_csv(temp_positions_root / "empty.csv", [])

        result = apply_trades(csv_path, dry_run=False)

        assert result.success is False
        assert "No entries found" in result.errors[0]

    def test_result_to_dict(self) -> None:
        """ApplyResult.to_dict() returns correct structure."""
        result = ApplyResult()
        result.skipped.append(("AAPL", "no change"))
        result.updated.append(("GOOGL", 10.0, 20.0))
        result.deleted.append("MSFT")
        result.errors.append("some error")
        result.success = True

        d = result.to_dict()
        assert d["success"] is True
        assert len(d["skipped"]) == 1
        assert d["skipped"][0] == {"symbol": "AAPL", "reason": "no change"}
        assert len(d["updated"]) == 1
        assert d["updated"][0] == {"symbol": "GOOGL", "old": 10.0, "new": 20.0}
        assert d["deleted"] == ["MSFT"]
        assert d["errors"] == ["some error"]

    def test_market_value_recomputed_on_update(self, temp_positions_root: Path, sample_position: dict) -> None:
        """When shares updated, market_value = shares * current_price."""
        csv_path = write_csv(temp_positions_root / "apply.csv", [
            {"plan_id": "us_hb", "symbol": "SPYM", "name": "S&P 500", "current_shares": "20", "new_shares": "30"},
        ])

        apply_trades(csv_path, dry_run=False)

        saved = temp_positions_root / "us_hb" / f"{date.today().isoformat()}.json"
        with open(saved) as f:
            saved_data = json.load(f)
        spym = next(p for p in saved_data["positions"] if p["symbol"] == "SPYM")
        # current_price=100.0, new_shares=30 → market_value=3000
        assert spym["shares"] == 30.0
        assert spym["market_value"] == 3000.0
