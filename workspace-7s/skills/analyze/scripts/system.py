"""
S3 System — Market regime assessment.

Combines S3a (macro), S3b (market structure — P1), and S3c (risk/VIX)
to produce a comprehensive system-level assessment for each asset.

This is the entry point called by the analyze pipeline.
"""

from __future__ import annotations

from typing import Any

from skills.analyze.scripts.s3_system.macro import assess_macro
from skills.analyze.scripts.s3_system.vix import assess_vix


def assess_regime(asset: dict) -> dict:
    """Assess the market/system regime for a single asset.

    Args:
        asset: Asset dict (from species/species selection).
               Must contain 'symbol', 'region', 'name'.

    Returns:
        Dict with:
            symbol
            regime_summary: high-level regime label
            confidence: 0.0-1.0
            macro: S3a macro assessment
            risk: S3c VIX/risk assessment
            context: list of human-readable annotations
            signal_bias: bullish/bearish/neutral (for S4)
    """
    symbol = asset.get("symbol", "unknown")
    region = str(asset.get("region", "US")).upper()

    result: dict[str, Any] = {
        "symbol": symbol,
        "region": region,
        "regime_summary": "neutral",
        "confidence": 0.0,
        "macro": None,
        "risk": None,
        "structure": None,  # P1
        "context": [],
        "signal_bias": "neutral",
    }

    # ── S3a Macro (all regions — US-centric for now) ──
    try:
        macro = assess_macro()
        result["macro"] = macro
        result["context"].extend(macro.get("context", []))
    except Exception as e:
        result["context"].append(f"⚠ Macro assessment failed: {e}")

    # ── S3c Risk/VIX (US only) ──
    if region == "US":
        try:
            vix = assess_vix()
            result["risk"] = vix

            vix_zone = vix.get("vix_zone", "unknown")
            vix_current = vix.get("vix_current")

            if vix_current is not None:
                result["context"].append(
                    f"VIX at {vix_current} ({vix_zone} zone, "
                    f"{vix.get('vix_percentile_rank', 0)*100:.0f}%ile)"
                )

            if vix_zone in ("panic", "high"):
                result["context"].append("⚠ Elevated VIX — risk-off environment")
            elif vix_zone == "low":
                result["context"].append("VIX low — benign risk environment")
        except Exception as e:
            result["context"].append(f"⚠ VIX assessment failed: {e}")
    else:
        # CN region — VIX not applicable (use A-share volatility proxy — future)
        result["risk"] = {"note": "VIX not applicable for CN region"}

    # ── Regime summary ──
    macro_regime = "neutral"
    if result["macro"] and "regime" in result["macro"]:
        macro_regime = result["macro"]["regime"]
        result["regime_summary"] = macro_regime
        result["confidence"] = result["macro"].get("confidence", 0.0)

    # ── Signal bias for S4 ──
    if macro_regime in ("recession", "stagflation"):
        result["signal_bias"] = "bearish"
    elif macro_regime in ("overheat",) and \
            result.get("risk", {}).get("vix_zone") in ("high", "panic"):
        result["signal_bias"] = "bearish"
    elif macro_regime in ("expansion", "recovery"):
        result["signal_bias"] = "bullish"
    else:
        result["signal_bias"] = "neutral"

    return result
