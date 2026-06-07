"""
volume.py — Structured volume signal computation.

Peer of valuation.py. Reads rules from config/strategies/rules/volume.yaml.
Computes structured vol_signal from vol_ratio + price direction.

Output: vol_signal string (CONFIRM_BULLISH, CONFIRM_BEARISH, EXHAUSTION, etc.)
        and vol_memo string (for display).

Usage:
    from skills.analyze.scripts.s4_strategy.volume import compute_volume_signal
    vol_signal, vol_memo = compute_volume_signal(vol_ratio, roc)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

_THIS = Path(__file__).resolve()
_WORKSPACE_ROOT = _THIS.parents[4]
_RULES_PATH = _WORKSPACE_ROOT / "config" / "strategies" / "rules" / "volume.yaml"


def _load_rules() -> dict:
    """Load volume analysis rules from config/strategies/rules/volume.yaml."""
    if not _RULES_PATH.exists():
        return {}
    try:
        with open(_RULES_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _classify_vol_ratio(ratio: float, thresholds: dict) -> str:
    """Classify vol_ratio into surge/normal/shrink based on thresholds."""
    surge_cfg = thresholds.get("surge", {})
    if ratio > surge_cfg.get("gt", 1.5):
        return "surge"

    shrink_cfg = thresholds.get("shrink", {})
    if ratio < shrink_cfg.get("lt", 0.7):
        return "shrink"

    return "normal"


def _classify_roc(roc: float, thresholds: dict) -> str:
    """Classify ROC (%) into up/down/flat based on thresholds."""
    flat_cfg = thresholds.get("flat", {})
    flat_range = flat_cfg.get("between", [-0.5, 0.5])
    if flat_range[0] <= roc <= flat_range[1]:
        return "flat"

    if roc > 0:
        return "up"
    return "down"


def compute_volume_signal(
    vol_ratio: Optional[float],
    roc: Optional[float],
) -> tuple[str, str]:
    """Compute structured volume signal from vol_ratio and ROC.

    Args:
        vol_ratio: Volume ratio vs 20-day avg (e.g. 1.2 = 1.2x avg).
                    None if volume data unavailable.
        roc: Rate of change (%) over 20 days. None if unavailable.

    Returns:
        (vol_signal, vol_memo)
        vol_signal: one of CONFIRM_BULLISH, CONFIRM_BEARISH, EXHAUSTION,
                    WEAKEN, ABNORMAL, NORMAL, QUIET, or "N/A"
        vol_memo: human-readable description.
    """
    if vol_ratio is None:
        return "N/A", "量比数据缺失"

    rules = _load_rules()
    vol_thresholds = rules.get("vol_ratio", {})
    roc_thresholds = rules.get("roc", {})
    signal_rules = rules.get("volume_signals", [])

    vol_class = _classify_vol_ratio(vol_ratio, vol_thresholds)
    roc_class = _classify_roc(roc or 0.0, roc_thresholds) if roc is not None else "flat"

    # Match against rules (first match wins)
    for rule in signal_rules:
        when = rule.get("when", {})
        rule_vol = when.get("vol_ratio", "")
        rule_roc = when.get("roc", "")

        vol_match = (rule_vol == vol_class) if rule_vol else True
        roc_match = (rule_roc == roc_class) if rule_roc else True

        if vol_match and roc_match:
            signal = rule.get("signal", "NORMAL")
            memo = rule.get("memo", "量能正常")
            return signal, memo

    return "NORMAL", "量能正常"
