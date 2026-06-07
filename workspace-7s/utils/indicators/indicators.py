"""
Technical indicators for analysis across all regions.

Indicators:
- LDev: Percentage deviation from 60-day MA (valuation)
- Z-Score: Standard deviations from 240-day MA (momentum)  
- RSI: Relative Strength Index (strength)
- MA: Moving averages (trend)
"""

from typing import List, Tuple, Optional
import numpy as np
import pandas as pd

# Import periods from constants (allows override at runtime)
try:
    from .constants import INDICATOR_PERIODS
    DEFAULT_RSI_PERIOD = INDICATOR_PERIODS.get("rsi", 14)
    DEFAULT_MA_SHORT = INDICATOR_PERIODS.get("ma_short", 60)
    DEFAULT_MA_LONG = INDICATOR_PERIODS.get("ma_long", 240)
except ImportError:
    DEFAULT_RSI_PERIOD = 14
    DEFAULT_MA_SHORT = 60
    DEFAULT_MA_LONG = 240


def calculate_ldev(
    prices: List[float],
    period: int = None
) -> Tuple[float, float]:
    """
    Calculate LDev (List Deviation) - percentage deviation from MA.
    
    Args:
        prices: List of daily closing prices (oldest first)
        period: MA period (default: MA_SHORT from constants = 60 days)
    
    Returns:
        (ldev_percent, ma_value) where ldev = (current - ma) / ma * 100
    
    Example:
        ldev, ma = calculate_ldev([100, 101, 102, ..., 105], period=60)
        # If MA=102, current=105: ldev = (105-102)/102 * 100 = 2.94%
    """
    period = period if period is not None else DEFAULT_MA_SHORT
    if len(prices) < period:
        return np.nan, np.nan
    
    prices_arr = np.array(prices)
    ma = prices_arr[-period:].mean()
    current = prices_arr[-1]
    ldev = (current - ma) / ma * 100
    
    return ldev, ma


def calculate_z_score(
    prices: List[float],
    period: int = None
) -> float:
    """
    Calculate Z-Score - standard deviations from long-term MA.
    
    Args:
        prices: List of daily closing prices (oldest first)
        period: MA period (default: MA_LONG from constants = 240 days)
    
    Returns:
        Z-score value (positive = above, negative = below)
    
    Example:
        z = calculate_z_score([100, 101, ..., 120], period=240)
        # If MA=110, std=5, current=120: z = (120-110)/5 = 2.0
    """
    period = period if period is not None else DEFAULT_MA_LONG
    if len(prices) < period:
        return np.nan
    
    prices_arr = np.array(prices)
    prices_recent = prices_arr[-period:]
    ma = prices_recent.mean()
    std = prices_recent.std()
    
    if std == 0:
        return 0.0
    
    current = prices_arr[-1]
    z_score = (current - ma) / std
    
    return z_score


def calculate_rsi(
    prices: List[float],
    period: int = None
) -> float:
    """
    Calculate RSI (Relative Strength Index) - strength indicator.
    
    Args:
        prices: List of daily closing prices (oldest first)
        period: RSI period (default: RSI from constants = 14 days)
    
    Returns:
        RSI value (0-100)
    
    Example:
        rsi = calculate_rsi([100, 101, 99, 102, ...], period=14)
        # Returns 40-60 in equilibrium, <30 oversold, >70 overbought
    """
    period = period if period is not None else DEFAULT_RSI_PERIOD
    if len(prices) < period + 1:
        return np.nan
    
    prices_arr = np.array(prices)
    deltas = np.diff(prices_arr)
    
    seed = deltas[:period + 1]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    
    # Handle edge cases
    if down == 0:
        # Pure uptrend: RSI = 100
        return 100.0 if up > 0 else 0.0
    
    rs = up / down
    rsi = 100 - 100 / (1 + rs)
    
    return rsi


def calculate_moving_averages(
    prices: List[float],
    short_period: int = None,
    long_period: int = None
) -> Tuple[Optional[float], Optional[float]]:
    """
    Calculate short and long-term moving averages.
    
    Args:
        prices: List of daily closing prices (oldest first)
        short_period: Short MA period (default: MA_SHORT from constants = 60 days)
        long_period: Long MA period (default: MA_LONG from constants = 240 days)
    
    Returns:
        (ma_short, ma_long) tuple, None if insufficient data
    """
    short_period = short_period if short_period is not None else DEFAULT_MA_SHORT
    long_period = long_period if long_period is not None else DEFAULT_MA_LONG
    prices_arr = np.array(prices)
    
    ma_short = None
    if len(prices) >= short_period:
        ma_short = prices_arr[-short_period:].mean()
    
    ma_long = None
    if len(prices) >= long_period:
        ma_long = prices_arr[-long_period:].mean()
    
    return ma_short, ma_long


def calculate_all_indicators(
    prices: List[float],
    asset_symbol: str = ""
) -> dict:
    """
    Calculate all technical indicators for an asset.
    
    Args:
        prices: List of daily closing prices (oldest first)
        asset_symbol: Asset identifier (for logging/debugging)
    
    Returns:
        Dictionary with all indicators and their values
    
    Example:
        indicators = calculate_all_indicators(
            prices=[100, 101, 102, ..., 105],
            asset_symbol="159207"
        )
        # Returns {
        #   "ldev": 2.94, "ldev_ma": 102.0,
        #   "z_score": 1.5,
        #   "rsi": 55.0,
        #   "ma_60": 102.0, "ma_240": 100.0,
        #   "price_current": 105.0,
        #   "data_points": 500
        # }
    """
    ldev, ldev_ma = calculate_ldev(prices)
    z_score = calculate_z_score(prices)
    rsi = calculate_rsi(prices)
    ma_60, ma_240 = calculate_moving_averages(prices)
    
    return {
        "symbol": asset_symbol,
        "ldev": ldev,
        "ldev_ma": ldev_ma,
        "z_score": z_score,
        "rsi": rsi,
        "ma_60": ma_60,
        "ma_240": ma_240,
        "price_current": prices[-1] if prices else np.nan,
        "data_points": len(prices),
    }


def validate_indicators(indicators: dict) -> bool:
    """
    Validate that all required indicators are present and valid.
    
    Args:
        indicators: Dictionary from calculate_all_indicators()
    
    Returns:
        True if all indicators are valid, False otherwise
    """
    required_keys = ["ldev", "z_score", "rsi", "ma_60", "ma_240", "price_current"]
    
    for key in required_keys:
        if key not in indicators:
            return False
        value = indicators[key]
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return False
    
    return True
