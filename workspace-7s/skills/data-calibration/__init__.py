"""
Data Calibration Skill — 7S Pipeline

This module now wraps the migrated legacy calibration classes:
- CalibrationUS (from skills.skill_us)
- CalibrationCN (from skills.skill_cn)

Usage:
    python3 skills/data-calibration/scripts/run_calibration.py --region US
    python3 skills/data-calibration/scripts/run_calibration.py --region CN

Note:
    The legacy CalibrationEngine has been removed. It lacked proper upsert
    logic and would overwrite existing calibrated data on recalibration.
    All calibration now uses the proven CalibrationUS/CalibrationCN classes.
"""

__all__ = []
