from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dao.config_dao import build_state_index, load_state_records


def test_state_db_index_active_takes_priority_over_watchlist():
    """active is the production set; watchlist symbols not in active resolve to watchlist."""
    index = build_state_index(ROOT)

    # XLU is in watchlist only (not active), resolves to watchlist
    assert index["XLU"]["state"] == "watchlist"
    # 159207 moved to active
    assert index["159207"]["state"] == "active"
    # CN symbol present
    assert "005223" in index


def test_watchlist_records_can_be_loaded_by_region():
    records = load_state_records(ROOT, "watchlist", "US")

    symbols = {record["symbol"] for record in records}
    assert "XLU" in symbols
    assert "005223" not in symbols


def test_active_state_file_has_promoted_symbols():
    """active.json holds the production symbols (previously in void)."""
    records = load_state_records(ROOT, "active")

    symbols = {r["symbol"] for r in records}
    assert "159207" in symbols
    assert len(records) > 0