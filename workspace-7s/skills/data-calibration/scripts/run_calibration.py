#!/usr/bin/env python3
"""
Data Calibration Runner — 7S Pipeline

Usage:
    python3 skills/data-calibration/scripts/run_calibration.py --region US
    python3 skills/data-calibration/scripts/run_calibration.py --region CN
    python3 skills/data-calibration/scripts/run_calibration.py --region US --preflight-only
"""

import argparse
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

# Import via sys.path (data-calibration has dash, not a Python package)
sys.path.insert(0, str(WORKSPACE_ROOT / "skills" / "data-calibration" / "scripts"))
from us_calibration import CalibrationUS
from cn_calibration import CalibrationCN


def get_calibrator(region: str):
    region = region.upper()
    if region == "US":
        return CalibrationUS()
    elif region == "CN":
        return CalibrationCN()
    else:
        raise ValueError(f"Unknown region: {region}")


def run_calibration(region: str, preflight_only: bool = False):
    calibrator = get_calibrator(region)

    if preflight_only:
        manifest = calibrator.preflight()
        print(f"\n{'=' * 60}")
        print(f"Calibration Preflight — {region}")
        print(f"{'=' * 60}")
        print(f"\n{manifest.summary()}")
        print(f"\nStaged: {manifest.staged}")
        print(f"Missing: {manifest.missing}")
        print(f"Rejected: {manifest.rejected}")
        return 0

    print(f"\n{'=' * 60}")
    print(f"Running Calibration — {region}")
    print(f"{'=' * 60}")

    result = calibrator.execute()

    print(f"\n{'=' * 60}")
    print("Calibration Results")
    print(f"{'=' * 60}")
    print(f"Processed: {result.get('processed_count', 0)}")
    print(f"Skipped: {result.get('skipped_count', 0)}")
    print(f"Errors: {len(result.get('errors', []))}")

    if result.get('files_processed'):
        print(f"\nFiles processed:")
        for fp in result['files_processed']:
            print(f"  - {fp['symbol']}: {fp['rows_merged']} rows")

    if result.get('errors'):
        print(f"\nErrors:")
        for err in result['errors']:
            print(f"  - {err}")

    return 0 if result.get('status') == 'success' else 1


def main():
    parser = argparse.ArgumentParser(description="Data Calibration for 7S Pipeline")
    parser.add_argument("--region", default="US", choices=["US", "CN"],
                        help="Region for calibration (default: US)")
    parser.add_argument("--preflight-only", action="store_true",
                        help="Run preflight check only")
    args = parser.parse_args()

    try:
        exit_code = run_calibration(args.region, args.preflight_only)
        sys.exit(exit_code)
    except Exception as e:
        print(f"\n❌ Calibration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
