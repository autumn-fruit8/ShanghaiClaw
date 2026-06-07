"""
Shared constants and enumerations for workspace-7s services.
Used across CN and US implementations.
"""

from enum import Enum
from typing import Dict, Final


class Region(str, Enum):
    """Market regions supported by workspace-7s."""
    CN = "CN"  # China (akshare)
    US = "US"  # United States + Hong Kong (yfinance)


class AssetType(str, Enum):
    """Classification of tradeable assets."""
    CN_ETF = "CN_ETF"      # China mainland ETFs
    CN_OTC = "CN_OTC"      # China OTC (individual stocks)
    HK_ETF = "HK_ETF"      # Hong Kong ETFs
    US_ETF = "US_ETF"      # US ETFs


class Strategy(str, Enum):
    """Investment strategies for each asset."""
    STEADY = "STEADY"       # Buy-and-hold, rebalance on dips
    VOLATILE = "VOLATILE"   # Mean-reversion trading
    MOMENTUM = "MOMENTUM"   # Trend-following


class Signal(str, Enum):
    """Generated signals for decision making."""
    BULLISH = "BULLISH"           # Strong buy signal
    BEARISH = "BEARISH"           # Strong sell signal
    OPPORTUNITY = "OPPORTUNITY"   # Good entry point (accumulate)
    WARNING = "WARNING"           # Caution, reduce exposure
    DANGER = "DANGER"             # Risk of loss, possible exit
    NEUTRAL = "NEUTRAL"           # No clear signal


# Technical Indicator Thresholds
# These come from SOUL.md and are tunable via TOOLS.md

STEADY_PARAMS: Final[Dict[str, float]] = {
    "strategic_buy_max_dev": 1.0,      # Buy when LDev < 1.0
    "strategic_deep_val": -1.5,        # Aggressive buy when LDev < -1.5
    "tactical_rsi_buy": 35,            # Buy when RSI < 35
    "bubble_exit_dev": 3.0,            # Exit when LDev > 3.0
}

VOLATILE_PARAMS: Final[Dict[str, float]] = {
    "strategic_buy_start": -1.0,       # Start buying when LDev < -1.0
    "strategic_heavy_buy": -2.0,       # Heavy buy when LDev < -2.0
    "tactical_sell_z": 1.0,            # Trim when Z-Score > 1.0
    "tactical_high_z": 1.5,            # Clear when Z-Score > 1.5
}

MOMENTUM_PARAMS: Final[Dict[str, float]] = {
    "sell_level": 2.5,                 # Exit when LDev > 2.5 (melt-up)
    "chase_buy_min_rsi": 50,           # Chase when RSI > 50 & price > MA60
    "parachute_trigger": True,         # Exit all on price < MA60 (broken trend)
}

# Technical Indicator Periods
INDICATOR_PERIODS: Final[Dict[str, int]] = {
    "rsi": 14,         # RSI lookback period
    "ma_short": 60,    # Short-term MA (60 days)
    "ma_long": 240,    # Long-term MA (240 days, ~1 year)
}

# Data Validation
DATA_VALIDATION: Final[Dict[str, int]] = {
    "min_data_points": 60,             # Minimum daily data points required
    "max_missing_percent": 5,          # Max %Missing data allowed
}

# API Configurations (defaults, overridden by TOOLS.md)
CN_API_CONFIG = {
    "provider": "akshare",
    "timeout": 10,
    "retry_count": 3,
    "rate_limit_delay": 0.2,           # 200ms between requests
}

US_API_CONFIG = {
    "provider": "yfinance",
    "timeout": 15,
    "retry_count": 3,
    "rate_limit_delay": 1.0,           # 1s between requests (stricter)
}

# Market Hours (default, can be overridden by region config)
MARKET_HOURS = {
    "CN": {
        "open": "09:30",
        "close": "15:00",
        "timezone": "Asia/Shanghai",
    },
    "US": {
        "open": "09:30",
        "close": "16:00",
        "timezone": "America/New_York",
    },
}
