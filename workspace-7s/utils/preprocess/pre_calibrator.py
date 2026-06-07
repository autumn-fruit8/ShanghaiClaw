"""
Pre-Calibrator
==============

Scans a source directory (1_raw/) for newly downloaded data files, extracts the
asset symbol from the filename, renames the file to ``{symbol}.{ext}``, and moves
it into the calibration staging area (2_staged/).

Files whose symbol cannot be identified are moved to ``1_raw/rejected/`` so the
originals are never silently lost.  A ``_move_log.txt`` in ``1_raw/`` records
every move operation for auditability.

Ported from the legacy pre_calibrate.py with minimal changes.
Called before CalibrationCN / CalibrationUS to normalise incoming filenames.

Symbol extraction rules (priority order):
1. H股  — ``H`` + 5 digits  (e.g. H00001)
2. HK ticker with dot suffix — digits + ``.HK``  (e.g. 2800.HK)
3. A股 / 指数 — 6 digits  (e.g. 000300)
   • 北交所: 4-prefixed → 9-prefixed  (430047 → 930047)
4. 美股 (括号) — 2–5 uppercase letters in ``(…)``  (e.g. ``(USMV)``)
5. 美股 (开头) — 2–5 uppercase letters at start  (e.g. ``AAPL_Historical``)
"""

import logging
import os
import re
import shutil
from datetime import datetime
from typing import Dict, Optional

VALID_EXTENSIONS = {".csv", ".html", ".mhtml", ".xls", ".xlsx"}

# Module-level fallback logger (used when caller doesn't pass one)
_DEFAULT_LOGGER = logging.getLogger(__name__)


def _extract_symbol(base_name: str) -> Optional[str]:
    """Return the normalised symbol extracted from a bare filename, or None."""

    # 1. H股 — H + 5 digits
    m = re.search(r"(H\d{5})", base_name, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    # 2. HK dot-format ticker — digits.HK (e.g. 2800.HK, 3032.HK)
    m = re.search(r"(\d{2,5}\.HK)", base_name, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    # 3. 6-digit A股 / 北交所
    m = re.search(r"(\d{6})", base_name)
    if m:
        raw = m.group(1)
        if raw.startswith("4"):
            mapped = "9" + raw[1:]
            return mapped
        return raw

    # 4. 美股 ticker in parentheses
    m = re.search(r"\(([A-Z]{2,5})\)", base_name)
    if m:
        return m.group(1)

    # 5. 美股 ticker at start
    m = re.match(r"^([A-Z]{2,5})", base_name.upper())
    if m:
        return m.group(1)

    return None


def auto_rename_and_move_files(
    source_dir: str,
    target_dir: str,
    logger: Optional[logging.Logger] = None,
    index_map: Optional[Dict[str, str]] = None,
    allowed_symbols: Optional[set] = None,
) -> int:
    """
    Scan *source_dir* (1_raw/), extract symbols, rename, and move to *target_dir* (2_staged/).

    Files with unrecognised symbols are moved to ``{source_dir}/rejected/`` instead
    of being silently skipped.  Every operation is recorded in
    ``{source_dir}/_move_log.txt``.

    Args:
        source_dir:      Directory where raw downloaded files land (1_raw/).
        target_dir:      Staging directory that calibration reads from (2_staged/).
        logger:          Optional logger instance. Falls back to module-level logger
                         (which falls back to stdout if no handler is configured).
        index_map:       Optional dict mapping raw index codes to canonical asset
                         symbols, e.g. ``{"000300": "159925", "H30269": "159547"}``.
                         When provided, an extracted code is looked up here first;
                         the canonical symbol is used when found.
        allowed_symbols: Optional set of valid asset symbols.  When provided, any
                         file whose (post-index_map) symbol is NOT in this set is
                         moved to ``rejected/`` instead of ``target_dir``.

    Returns:
        Number of files successfully moved to target_dir.
    """
    log = logger or _DEFAULT_LOGGER

    if not os.path.exists(source_dir):
        log.info(f"Source dir not found, skipping pre-calibration: {source_dir}")
        return 0

    os.makedirs(target_dir, exist_ok=True)
    rejected_dir = os.path.join(source_dir, "rejected")
    os.makedirs(rejected_dir, exist_ok=True)

    log_path = os.path.join(source_dir, "_move_log.txt")
    session_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log.info(f"Pre-calibrator: scanning {source_dir}")

    processed_count = 0
    log_lines = [f"\n=== session {session_stamp} ==="]

    for filename in sorted(os.listdir(source_dir)):
        # Skip the log file, rejected/ dir, and hidden files
        if filename.startswith("_") or filename == "rejected":
            continue

        full_path = os.path.join(source_dir, filename)
        if not os.path.isfile(full_path):
            continue

        base_name, ext = os.path.splitext(filename)
        if ext.lower() not in VALID_EXTENSIONS:
            log.debug(f"  skip (unsupported extension): {filename}")
            continue

        symbol = _extract_symbol(base_name)

        # If an index→symbol map was provided, translate the raw code to the
        # canonical asset symbol (e.g. "000300" → "159925").
        if symbol and index_map:
            symbol = index_map.get(symbol, symbol)

        # If an allowed-symbols set was provided, reject anything not in it.
        if symbol and allowed_symbols and symbol not in allowed_symbols:
            dst = os.path.join(rejected_dir, filename)
            try:
                shutil.move(full_path, dst)
                msg = f"  [REJECTED] {filename} → rejected/ (symbol '{symbol}' not in allowed set)"
                log.warning(msg)
                log_lines.append(f"REJECTED  {filename}  →  rejected/{filename}")
            except Exception as e:
                log.error(f"  [ERROR] could not move {filename} to rejected/: {e}")
                log_lines.append(f"ERROR     {filename}  →  rejected/ FAILED: {e}")
            continue

        if not symbol:
            # Move to rejected/ so the original is preserved
            dst = os.path.join(rejected_dir, filename)
            try:
                shutil.move(full_path, dst)
                msg = f"  [REJECTED] {filename} → rejected/ (symbol not recognised)"
                log.warning(msg)
                log_lines.append(f"REJECTED  {filename}  →  rejected/{filename}")
            except Exception as e:
                log.error(f"  [ERROR] could not move {filename} to rejected/: {e}")
                log_lines.append(f"ERROR     {filename}  →  rejected/ FAILED: {e}")
            continue

        dst_filename = f"{symbol}{ext}"
        dst_path = os.path.join(target_dir, dst_filename)

        # If a staged file already exists for this symbol, overwrite it —
        # new raw data always takes precedence (production behaviour).
        if os.path.exists(dst_path) and os.path.abspath(full_path) != os.path.abspath(dst_path):
            msg = f"  [OVERWRITE] {filename} → {dst_filename} (replacing existing staged file)"
            log.warning(msg)
            log_lines.append(f"OVERWRITE {filename}  →  {dst_filename} (replaced existing)")

        try:
            shutil.move(full_path, dst_path)
            msg = f"  [STAGED] {filename} → {dst_filename}"
            log.info(msg)
            log_lines.append(f"STAGED    {filename}  →  {dst_filename}")
            processed_count += 1
        except Exception as e:
            log.error(f"  [ERROR] move failed {filename}: {e}")
            log_lines.append(f"ERROR     {filename}  →  {dst_filename} FAILED: {e}")

    # Append session record to move log
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + "\n")
    except Exception as e:
        log.warning(f"Could not write move log: {e}")

    log.info(
        f"Pre-calibrator done: {processed_count} staged, "
        f"{len([l for l in log_lines if l.startswith('REJECTED')])} rejected"
    )
    return processed_count
