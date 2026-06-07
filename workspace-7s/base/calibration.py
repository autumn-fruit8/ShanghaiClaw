"""
Calibration base classes for workspace-7s.

Calibration is the file-import pipeline that parses source files (CSV, HTML, XLSX),
validates and upserts price-history CSVs, and archives originals.
"""

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging
import os
import shutil
import pandas as pd

from base.service_base import ServiceBase


@dataclass
class CalibrationManifest:
    """
    Result of a pre-flight inbox scan.

    Attributes:
        region:       Region value string ("CN", "US", ...).
        staged:       Symbols that have a matching file in inbox.
        missing:      Active symbols with NO matching file in inbox.
        rejected:     Filenames in inbox that match no active symbol.
        coverage_pct: staged / total_active * 100.
    """
    region: str
    staged: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    rejected: List[str] = field(default_factory=list)
    coverage_pct: float = 0.0

    @property
    def total_active(self) -> int:
        return len(self.staged) + len(self.missing)

    def is_ready(self, min_coverage: float = 0.0) -> bool:
        """Return True if coverage_pct >= min_coverage."""
        return self.coverage_pct >= min_coverage

    def summary(self) -> str:
        return (
            f"{self.region} preflight: {len(self.staged)}/{self.total_active} staged "
            f"| {len(self.missing)} missing | {len(self.rejected)} rejected "
            f"| {self.coverage_pct:.0f}% coverage"
        )


class CalibrationBase(ServiceBase):
    """Base class for calibration services (file-import pipeline)."""

    # Maximum number of archived copies to keep per symbol in 4_archive/.
    # Oldest are deleted when the limit is exceeded (default = 12 monthly runs).
    MAX_ARCHIVE_FILES: int = 12

    def __init__(self, region: str, config: Optional[Dict] = None, **kwargs):
        super().__init__("calibration", region, config, **kwargs)

    @abstractmethod
    def _get_assets(self) -> List:
        """
        Return the list of active assets for this region.
        Override in each subclass to fetch from AssetManifest.
        """

    def _scan_staged(self, assets: List, staged_dir: str) -> CalibrationManifest:
        """
        Read-only scan: classify staged files against the active asset list.

        Args:
            assets:     Active Asset objects for this region.
            staged_dir: Directory to scan (typically 2_staged/).

        Returns:
            CalibrationManifest with staged / missing / rejected / coverage.
        """
        supported_exts = {".csv", ".html", ".xlsx", ".xls", ".mhtml"}

        staged_files: Dict[str, str] = {}  # stem_lower -> filename
        if os.path.isdir(staged_dir):
            for f in os.listdir(staged_dir):
                ext = os.path.splitext(f)[1].lower()
                if ext in supported_exts:
                    staged_files[os.path.splitext(f)[0].lower()] = f

        staged, missing = [], []
        matched_filenames: set = set()
        for asset in assets:
            sym_lower = asset.symbol.lower()
            if sym_lower in staged_files:
                staged.append(asset.symbol)
                matched_filenames.add(staged_files[sym_lower])
            else:
                missing.append(asset.symbol)

        rejected = [f for f in staged_files.values() if f not in matched_filenames]
        total = len(assets)
        coverage_pct = (len(staged) / total * 100) if total > 0 else 0.0

        return CalibrationManifest(
            region=self.region,
            staged=staged,
            missing=missing,
            rejected=rejected,
            coverage_pct=round(coverage_pct, 1),
        )

    @abstractmethod
    def _execute_impl(
        self,
        staged_dir: str = None,
        processed_dir: str = None,
        archive_dir: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Process calibration staged files → upsert price-history CSVs.
        (Sync — no async needed since file I/O is the bottleneck.)

        Args:
            staged_dir:    Path to 2_staged/ (renamed files ready to parse)
            processed_dir: Path to 3_processed/ (output {symbol}.csv files)
            archive_dir:  Path to 4_archive/ (source files moved here after processing)

        Returns:
            {
                "processed_count": int,
                "files_processed": [{"symbol", "file", "rows_new", "rows_merged"}],
                "skipped_count": int,
                "errors": [{"file", "error"}],
            }
        """
        pass

    def _validate_parsed_df(
        self, df: "pd.DataFrame", source_file: str
    ) -> dict:
        """
        Gate check on a freshly parsed DataFrame before it is upserted.

        Returns dict with keys:
            valid    bool  — False means skip this file entirely
            error    str   — reason for rejection (set when valid=False)
            warnings list  — non-fatal observations logged but not blocking
        """
        warnings_list = []

        # 1. Row count floor
        MIN_ROWS = 5
        if len(df) < MIN_ROWS:
            return {
                "valid": False,
                "error": f"only {len(df)} rows after parsing (minimum {MIN_ROWS}); "
                          "likely wrong header row detected",
                "warnings": [],
            }
        if len(df) < 20:
            warnings_list.append(
                f"only {len(df)} rows — confirm this is a full history file"
            )

        # 2. All price values must be positive
        nonpositive = (df["total_return"] <= 0).sum()
        if nonpositive > 0:
            return {
                "valid": False,
                "error": f"{nonpositive} non-positive price value(s) — "
                          "wrong column mapped or data corruption",
                "warnings": [],
            }

        # 3. Date range sanity: warn if first date is suspiciously recent (< 1 year)
        date_span_days = (df["date"].max() - df["date"].min()).days
        if date_span_days < 365:
            warnings_list.append(
                f"date span only {date_span_days} days — "
                "historical baseline may be too short for strategy signals"
            )

        # 4. Duplicate dates
        dup_count = df["date"].duplicated().sum()
        if dup_count > 0:
            warnings_list.append(
                f"{dup_count} duplicate date(s) found — will be de-duped on upsert"
            )

        return {"valid": True, "error": "", "warnings": warnings_list}

    # ── Atomic sub-services ───────────────────────────────────────────────────

    def _process_asset(
        self,
        asset,
        staged_dir: str,
        processed_dir: str,
    ) -> Optional[Dict]:
        """Parse + upsert one asset's staged file into processed CSV."""
        from utils.data_service.file_parser import parse_calibration_file

        supported_exts = {".csv", ".html", ".xlsx", ".xls", ".mhtml"}
        symbol = asset.symbol
        name   = asset.name

        candidates = [
            f for f in os.listdir(staged_dir)
            if os.path.splitext(f)[0].lower() == symbol.lower()
            and os.path.splitext(f)[1].lower() in supported_exts
        ]
        if not candidates:
            return None

        source_file = candidates[0]
        src_path    = os.path.join(staged_dir, source_file)
        dst_path    = os.path.join(processed_dir, f"{symbol}.csv")

        self.logger.info(f"⚡ [{name}] {source_file} -> {symbol}.csv")

        df_new = parse_calibration_file(src_path)
        if df_new is None:
            return {"symbol": symbol, "file": source_file, "error": "parse failed"}

        validation = self._validate_parsed_df(df_new, source_file)
        if not validation["valid"]:
            self.logger.warning(f"  ❌ validation failed [{source_file}]: {validation['error']}")
            return {"symbol": symbol, "file": source_file, "error": validation["error"]}
        for w in validation["warnings"]:
            self.logger.warning(f"  ⚠️  [{source_file}] {w}")

        # Upsert: new dates overwrite existing rows; older history is kept
        if os.path.exists(dst_path):
            try:
                df_old = pd.read_csv(dst_path)
                df_old["date"] = pd.to_datetime(df_old["date"])
                new_dates = set(df_new["date"])
                df_kept = df_old[~df_old["date"].isin(new_dates)]
                df_final = pd.concat([df_kept, df_new], ignore_index=True)
                merge_msg = f"{len(df_old)} + {len(df_new)} -> {len(df_final)} rows"
            except Exception as e:
                self.logger.warning(f"Read old CSV failed ({e}), overwriting")
                df_final = df_new
                merge_msg = f"overwrite {len(df_new)} rows"
        else:
            df_final = df_new
            merge_msg = f"new file {len(df_new)} rows"

        df_final = (
            df_final
            .sort_values("date")
            .drop_duplicates(subset=["date"], keep="last")
        )
        os.makedirs(processed_dir, exist_ok=True)
        df_final.to_csv(dst_path, index=False)
        self.logger.info(f"  💾 {symbol}.csv ({merge_msg})")

        return {
            "symbol":      symbol,
            "name":        name,
            "src_path":    src_path,
            "rows_new":    len(df_new),
            "rows_merged": len(df_final),
        }

    def _archive_processed(
        self,
        symbol: str,
        processed_dir: str,
        archive_dir: str,
        stamp: str,
    ) -> None:
        """Archive processed CSV snapshot."""
        src = os.path.join(processed_dir, f"{symbol}.csv")
        if not os.path.exists(src):
            return
        dest_name = f"{stamp}_{symbol}_processed.csv"
        dest = os.path.join(archive_dir, dest_name)
        try:
            shutil.copy2(src, dest)
            self.logger.info(f"  📸 snapshot -> {dest_name}")
        except Exception as e:
            self.logger.warning(f"  ⚠️ snapshot failed: {e}")

    def _archive_staged(
        self,
        src_path: str,
        symbol: str,
        archive_dir: str,
        stamp: str,
    ) -> None:
        """Archive the staged source file with rotation."""
        source_file  = os.path.basename(src_path)
        archive_name = f"{stamp}_{source_file}"
        try:
            shutil.move(src_path, os.path.join(archive_dir, archive_name))
            self.logger.info(f"  🗄️  archived -> {archive_name}")
        except Exception as e:
            self.logger.warning(f"  ⚠️ archive failed: {e}")
            return

        # Rotate: keep only MAX_ARCHIVE_FILES source copies per symbol
        try:
            existing_sources = sorted([
                f for f in os.listdir(archive_dir)
                if len(f) > 11
                and not f.endswith("_processed.csv")
                and os.path.splitext(f[11:])[0].lower() == symbol.lower()
            ])
            to_delete = (
                existing_sources[:-self.MAX_ARCHIVE_FILES]
                if len(existing_sources) > self.MAX_ARCHIVE_FILES
                else []
            )
            for old_file in to_delete:
                os.remove(os.path.join(archive_dir, old_file))
                self.logger.info(f"  🗑️  rotated old archive: {old_file}")
        except Exception as e:
            self.logger.warning(f"  ⚠️ archive rotation failed: {e}")

    # ── Orchestrator ─────────────────────────────────────────────────────────

    def _run_all(
        self,
        assets,
        staged_dir: str,
        processed_dir: str,
        archive_dir: str,
    ) -> Dict[str, Any]:
        """
        Orchestrator: for each asset run sub-service 1 → 2 → 3 in sequence.

        Sub-service 1  _process_asset     — parse + upsert
        Sub-service 2  _archive_processed — copy final CSV to archive
        Sub-service 3  _archive_staged    — move source file to archive + rotate
        """
        os.makedirs(processed_dir, exist_ok=True)
        os.makedirs(archive_dir, exist_ok=True)

        stamp           = datetime.now().strftime("%Y-%m-%d")
        processed_count = 0
        files_processed = []
        skipped_count   = 0
        errors          = []

        for asset in assets:
            result = self._process_asset(asset, staged_dir, processed_dir)
            if result is None:
                skipped_count += 1
                continue
            if "error" in result:
                errors.append(result)
                continue

            self._archive_processed(result["symbol"], processed_dir, archive_dir, stamp)
            self._archive_staged(result["src_path"], result["symbol"], archive_dir, stamp)

            processed_count += 1
            files_processed.append({
                "symbol":      result["symbol"],
                "file":        os.path.basename(result["src_path"]),
                "rows_new":    result["rows_new"],
                "rows_merged": result["rows_merged"],
            })

        return {
            "processed_count": processed_count,
            "files_processed": files_processed,
            "skipped_count":   skipped_count,
            "errors":          errors,
        }
