"""
alignment.py — Cross-product of Pulse × Directive for divergence detection.

Computes whether the statistical assessment (Pulse) and the strategy's
action direction (Directive verb) agree or conflict.

Stateless pure function. Species-independent. Strategy-independent.
"""

from __future__ import annotations


_ALIGNMENT_DESC: dict[str, str] = {
    "CONFIRMED": "一致",
    "DIVERGENT": "分歧",
    "NEUTRAL": "中性",
}


def classify_alignment(pulse: str, directive_verb: str) -> str:
    """Cross-product of Pulse classification × Directive direction.

    Args:
        pulse:           Pulse type from classify_pulse().
                          One of: EXTREME_OB, OVERBOUGHT, STRONG, NEUTRAL,
                                  WEAK, OVERSOLD, EXTREME_OS.
        directive_verb:  The verb from the tactic rule that fired.
                          One of: BUY, SELL, CLOSE, HOLD.

    Returns:
        "CONFIRMED":   Pulse and strategy agree on direction.
                       (e.g. OVERSOLD + BUY = strong entry signal)
        "DIVERGENT":   Pulse and strategy disagree.
                       (e.g. OVERBOUGHT + BUY = hot but still buying)
        "NEUTRAL":     Pulse is not extreme; strategy's call stands.
                       (e.g. NEUTRAL/STRONG/WEAK + any verb, or HOLD)
    """
    if directive_verb == "HOLD":
        return "NEUTRAL"

    if pulse in ("EXTREME_OB", "OVERBOUGHT"):
        if directive_verb in ("SELL", "CLOSE"):
            return "CONFIRMED"
        if directive_verb == "BUY":
            return "DIVERGENT"
        return "NEUTRAL"

    if pulse in ("EXTREME_OS", "OVERSOLD"):
        if directive_verb == "BUY":
            return "CONFIRMED"
        if directive_verb in ("SELL", "CLOSE"):
            return "DIVERGENT"
        return "NEUTRAL"

    return "NEUTRAL"


def get_alignment_desc(alignment: str) -> str:
    """Return Chinese description for an alignment value."""
    return _ALIGNMENT_DESC.get(alignment, alignment)
