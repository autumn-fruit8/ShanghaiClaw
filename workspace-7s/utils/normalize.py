"""Price data normalization utilities.

Shared conversion functions for ETF close prices → synthetic total_return.
Used by data_resolver, symbol_bootstrapper, and daily_update to avoid
duplicated logic.
"""

from __future__ import annotations

import numpy as np


def close_to_total_return(closes: np.ndarray, base: float = 1000.0) -> np.ndarray:
    """Convert a Series of close prices to synthetic total_return (base-normalized).

    Formula: total_return = closes / closes[0] * base

    This is the "ETF proxy" pattern used when a tracking index has no
    direct total-return API — the ETF's close prices serve as a proxy
    for daily changes, normalized to a common base for comparability.

    Args:
        closes: Array of close prices, chronologically ordered.
        base: Normalization base value (default 1000).

    Returns:
        Array of same length, normalized such that result[0] == base.

    Raises:
        ValueError: If first close price is zero or negative.
    """
    if len(closes) == 0:
        return np.array([], dtype=float)
    if closes[0] <= 0:
        raise ValueError(f"First close price must be positive, got {closes[0]}")
    return closes / closes[0] * base
