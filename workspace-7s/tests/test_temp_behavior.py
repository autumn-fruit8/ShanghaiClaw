import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dao.config_dao import AssetManifest
from base.daily_update import DailyUpdateBase


class FakeDailyUpdate(DailyUpdateBase):
    def __init__(self, base_path: Path):
        super().__init__(region="US", base_path=str(base_path))

    def fetch_incremental_data(self, symbol: str, asset_info: dict, last_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
                "price": [100.0, 102.0, 101.0],
            }
        )


def test_asset_manifest_can_load_temp_manifest_replace_mode(monkeypatch, tmp_path):
    temp_manifest = tmp_path / "temp-assets.json"
    temp_manifest.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "symbol": "TMP1",
                        "name": "Temporary US Asset",
                        "region": "US",
                        "strategy_type": "STEADY",
                        "state": "watchlist",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("SEVENS_TEMP_ASSET_MANIFEST", str(temp_manifest))
    monkeypatch.setenv("SEVENS_TEMP_ASSET_MODE", "replace")
    AssetManifest._instance = None
    AssetManifest._last_config_signature = None

    manifest = AssetManifest()
    us_symbols = [a.symbol for a in manifest.get_by_region("US")]
    temp_asset = manifest.get("TMP1")

    assert us_symbols == ["TMP1"]
    assert temp_asset is not None
    assert temp_asset.name == "Temporary US Asset"

    monkeypatch.delenv("SEVENS_TEMP_ASSET_MANIFEST", raising=False)
    monkeypatch.delenv("SEVENS_TEMP_ASSET_MODE", raising=False)
    AssetManifest._instance = None
    AssetManifest._last_config_signature = None


def test_asset_manifest_loads_default_asset_master(monkeypatch):
    monkeypatch.delenv("SEVENS_ASSET_MASTER_PATH", raising=False)
    monkeypatch.delenv("SEVENS_TEMP_ASSET_MANIFEST", raising=False)
    monkeypatch.delenv("SEVENS_TEMP_ASSET_MODE", raising=False)
    AssetManifest._instance = None
    AssetManifest._last_config_signature = None

    manifest = AssetManifest()
    default_asset = manifest.get("159207")

    assert default_asset is not None
    assert default_asset.cal_source is not None
    assert default_asset.cal_source.provider == "中证指数官网 (CSI)"


def test_asset_manifest_preserves_watchlist_metadata(monkeypatch):
    monkeypatch.delenv("SEVENS_ASSET_MASTER_PATH", raising=False)
    monkeypatch.delenv("SEVENS_TEMP_ASSET_MANIFEST", raising=False)
    monkeypatch.delenv("SEVENS_TEMP_ASSET_MODE", raising=False)
    AssetManifest._instance = None
    AssetManifest._last_config_signature = None

    manifest = AssetManifest()
    watchlist_asset = manifest.get("XLU")

    assert watchlist_asset is not None
    assert watchlist_asset.sector == "equity"
    assert "utilities" in watchlist_asset.tags
    assert "dividend" in watchlist_asset.tags


def test_asset_manifest_can_override_asset_master(monkeypatch, tmp_path):
    asset_master = tmp_path / "asset-master.json"
    asset_master.write_text(
        json.dumps(
            {
                "schema_version": "asset-master-v1",
                "assets": [
                    {
                        "symbol": "MASTER1",
                        "name": "Master Asset",
                        "region": "US",
                        "asset_type": "US_ETF",
                        "strategy_type": "STEADY",
                        "sector": "equity",
                        "data_file": "MASTER1.csv",
                        "cal_source": {
                            "provider": "Test",
                            "url": "",
                            "note": "",
                            "format": "web",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("SEVENS_ASSET_MASTER_PATH", str(asset_master))
    monkeypatch.delenv("SEVENS_TEMP_ASSET_MANIFEST", raising=False)
    monkeypatch.delenv("SEVENS_TEMP_ASSET_MODE", raising=False)
    AssetManifest._instance = None
    AssetManifest._last_config_signature = None

    manifest = AssetManifest()
    master_asset = manifest.get("MASTER1")

    assert master_asset is not None
    assert master_asset.name == "Master Asset"
    assert master_asset.sector == "equity"

    monkeypatch.delenv("SEVENS_ASSET_MASTER_PATH", raising=False)
    AssetManifest._instance = None
    AssetManifest._last_config_signature = None


def test_daily_update_bootstraps_missing_processed_csv_in_temp_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("SEVENS_TEMP_ASSET_MODE", "replace")
    updater = FakeDailyUpdate(tmp_path / "knowledge" / "us")

    result = updater.execute(
        {
            "TMPBOOT": {
                "name": "Bootstrap Asset",
                "type": "US_ETF",
            }
        }
    )

    created_csv = tmp_path / "knowledge" / "us" / "3_processed" / "TMPBOOT.csv"
    assert created_csv.exists()
    assert result["updated_count"] == 1
