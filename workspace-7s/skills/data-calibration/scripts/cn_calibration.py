"""
CN region calibration — file-import pipeline.

Scans knowledge/cn/2_staged/ for renamed data files, parses each one, and
upserts the result into knowledge/cn/3_processed/{symbol}.csv.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_WORKSPACE_ROOT))

from base.calibration import CalibrationBase, CalibrationManifest
from dao.asset_dao import AssetManifest


class CalibrationCN(CalibrationBase):
    """Calibration for CN region (A股 ETF + 场外基金)."""

    def __init__(self, region: str = "CN", config=None, **kwargs):
        super().__init__(region=region, config=config, **kwargs)

    def _get_assets(self):
        """Return all active CN assets from AssetManifest."""
        return AssetManifest().get_by_region("CN")

    def preflight(self, staged_dir: str = None) -> CalibrationManifest:
        """Read-only scan: which CN assets are staged, missing, or rejected."""
        staged_dir = staged_dir or str(_WORKSPACE_ROOT / "knowledge" / "cn" / "2_staged")
        return self._scan_staged(self._get_assets(), staged_dir)

    def _execute_impl(
        self,
        staged_dir: str = None,
        processed_dir: str = None,
        archive_dir: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Process all CN files found in staged_dir (sync)."""
        staged_dir    = staged_dir    or str(_WORKSPACE_ROOT / "knowledge" / "cn" / "2_staged")
        processed_dir = processed_dir or str(_WORKSPACE_ROOT / "knowledge" / "cn" / "3_processed")
        archive_dir   = archive_dir   or str(_WORKSPACE_ROOT / "knowledge" / "cn" / "4_archive")

        self.logger.info(f"Calibration CN | staged={staged_dir}")

        result = self._run_all(self._get_assets(), staged_dir, processed_dir, archive_dir)

        self.logger.info(
            f"✓ CN calibration done: {result['processed_count']} processed, "
            f"{result['skipped_count']} skipped, {len(result['errors'])} errors"
        )
        return result
