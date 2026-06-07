---
name: Data Calibration
description: "Use when the user asks to calibrate, validate, or reprocess raw market data files in 7S. This skill handles HTML parsing (Yahoo Finance), CSV parsing, validation, and normalization of historical price data into total-return series."
read_when:
  - User asks to calibrate, process, or validate raw data
  - User asks to run the calibration pipeline
  - User asks to parse HTML files from 1_raw and convert to 3_processed
  - User asks to check data quality or validate price data
allowed-tools: Bash(python:skills/data-calibration/scripts/run_calibration.py)
---

# Data Calibration

This skill parses, validates, and normalizes raw market data into total-return series for the 7S pipeline.

Uses the migrated legacy calibration classes (CalibrationUS/CalibrationCN) which implement proper upsert logic to merge new calibration data with existing history.

## Data Flow

```
1_raw/          →  Pre-Calibrator  →  2_staged/
HTML/CSV files      (renaming)          ↓
                            CalibrationUS/CalibrationCN
                                       ↓
3_processed/
Total-return CSV (upserted, preserving history)
```

## Supported Formats

| Format | Source | Parser |
|--------|--------|--------|
| Yahoo Finance HTML | `1_raw/*.html` | via `utils.file_parser` |
| CSV (date, price) | `1_raw/*.csv` | via `utils.file_parser` |
| CSV (date, adj_close) | `1_raw/*.csv` | via `utils.file_parser` |

## Output Format

All calibrated files in `3_processed/` use the same schema:

```
date,total_return
2003-05-01,1.0000
2003-05-02,1.0023
...
```

Values are normalized to start at 1.0 (cumulative return index).

## Invocation

```bash
# US region (default)
python3 skills/data-calibration/scripts/run_calibration.py --region US

# CN region
python3 skills/data-calibration/scripts/run_calibration.py --region CN

# Preflight check only (scan 2_staged/ without calibrating)
python3 skills/data-calibration/scripts/run_calibration.py --region US --preflight-only
```

## Key Features

- **Upsert Logic**: Recalibration merges new data with existing history (same dates are updated, older history is preserved)
- **Archive**: Processed CSVs are snapshotted to `4_archive/` before being overwritten
- **Source Rotation**: Old source files in `4_archive/` are rotated (keeps latest 12)
- **Validation**: Rejects files with < 5 rows or non-positive prices

## Integration with Asset Infrastructure

This skill uses 7S's central `AssetManifest` to:
- Resolve symbol → data file mapping
- Check `cal_source` for data provider info
- Filter by region (CN/US)
- Respect `active` status of assets
