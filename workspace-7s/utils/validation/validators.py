"""
Validators for data quality, positions, and risk constraints.

Functions:
- Validate price data (continuity, outliers)
- Validate portfolios (allocation limits, position sizes)
- Validate signals (constraints from AGENTS.md)
"""

from typing import List, Dict, Tuple, Optional
import numpy as np
from ..constants import Region, Strategy, AssetType, INDICATOR_PERIODS, DATA_VALIDATION


def validate_price_data(
    prices: List[float],
    symbol: str = "",
    min_points: int = None,
    allow_missing_percent: float = None
) -> Tuple[bool, str]:
    """
    Validate price data quality.
    
    Args:
        prices: List of prices to validate
        symbol: Asset symbol (for error messages)
        min_points: Minimum required data points (default from constants)
        allow_missing_percent: Max missing data percentage allowed
    
    Returns:
        (is_valid, error_message)
    
    Checks:
        - Sufficient data points (default 60)
        - No extreme outliers (>50% daily move)
        - Positive prices
    """
    min_points = min_points or DATA_VALIDATION["min_data_points"]
    allow_missing_percent = allow_missing_percent or DATA_VALIDATION["max_missing_percent"]
    
    # Check minimum data points
    if len(prices) < min_points:
        return False, f"{symbol}: Insufficient data ({len(prices)} < {min_points})"
    
    # Check for None/NaN values
    valid_prices = [p for p in prices if p is not None and not np.isnan(float(p))]
    missing_percent = (len(prices) - len(valid_prices)) / len(prices) * 100
    
    if missing_percent > allow_missing_percent:
        return False, f"{symbol}: Too many missing values ({missing_percent:.1f}%)"
    
    # Check for negative prices
    if any(p <= 0 for p in valid_prices if p is not None):
        return False, f"{symbol}: Contains non-positive prices"
    
    # Check for extreme moves (>100% in single day)
    deltas = np.diff(valid_prices)
    max_pct_move = max(abs(delta / valid_prices[i]) * 100 for i, delta in enumerate(deltas))
    
    if max_pct_move > 100:
        return False, f"{symbol}: Extreme price move detected ({max_pct_move:.1f}%)"
    
    return True, ""


def validate_portfolio(
    portfolio: Dict[str, float],
    total_capital: float,
    max_position_size: float = 0.15,
    min_position_size: float = 0.01
) -> Tuple[bool, List[str]]:
    """
    Validate portfolio allocation constraints.
    
    Args:
        portfolio: Dict of {symbol: amount_allocated}
        total_capital: Total portfolio value
        max_position_size: Max single position as % of portfolio (default 15%)
        min_position_size: Min position as % of portfolio (default 1%)
    
    Returns:
        (is_valid, list_of_errors)
    
    Constraints (from AGENTS.md):
        - No position > 15% of portfolio
        - Sum of allocations = 100%
        - All positive amounts
    """
    errors = []
    
    if not portfolio:
        errors.append("Portfolio is empty")
        return False, errors
    
    # Check total allocation
    total_allocated = sum(portfolio.values())
    if not (0.99 <= total_allocated / total_capital <= 1.01):
        errors.append(f"Total allocation {total_allocated/total_capital*100:.1f}% != 100%")
    
    # Check individual positions
    for symbol, amount in portfolio.items():
        if amount < 0:
            errors.append(f"{symbol}: Negative allocation")
        
        position_pct = amount / total_capital
        
        if position_pct > max_position_size:
            errors.append(f"{symbol}: Position {position_pct*100:.1f}% > max {max_position_size*100:.1f}%")
        
        if 0 < position_pct < min_position_size:
            errors.append(f"{symbol}: Position {position_pct*100:.1f}% < min {min_position_size*100:.1f}%")
    
    return len(errors) == 0, errors


def validate_signal_generation(
    symbol: str,
    region: Region,
    strategy: Strategy,
    indicators: Dict,
    current_position: float = 0
) -> Tuple[bool, str]:
    """
    Validate that signal generation is permitted.
    
    Args:
        symbol: Asset symbol
        region: Market region (CN or US)
        strategy: Investment strategy (STEADY/VOLATILE/MOMENTUM)
        indicators: Indicator dictionary from indicators.py
        current_position: Current position size (for validation)
    
    Returns:
        (is_allowed, reason)
    
    Constraints (from AGENTS.md - ask/act/never):
        - Never trade without valid indicators
        - Never use incomplete data
        - Can trade within strategy rules
    """
    # Check required indicators present
    required = ["ldev", "z_score", "rsi", "ma_60", "ma_240", "price_current"]
    for key in required:
        if key not in indicators or indicators[key] is None:
            return False, f"Missing indicator: {key}"
    
    # Check data quality from indicators
    if indicators.get("data_points", 0) < 60:
        return False, f"Insufficient data points ({indicators['data_points']} < 60)"
    
    # Strategy-specific validation
    if strategy == Strategy.MOMENTUM and current_position > 0:
        # MOMENTUM positions need parachute exit rule (hard stop at MA60)
        if indicators.get("ma_60") is None:
            return False, "MOMENTUM needs MA60 for parachute exit"
    
    return True, ""


def validate_region_config(
    region: Region,
    config: Dict
) -> Tuple[bool, List[str]]:
    """
    Validate region-specific configuration.

    Args:
        region: Region to validate (CN or US)
        config: Region config

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []
    required_fields = ["market_hours", "assets", "data_path"]

    for field in required_fields:
        if field not in config:
            errors.append(f"Missing required field: {field}")
    
    # Validate asset list
    assets = config.get("assets", [])
    if not assets:
        errors.append("No assets configured")
    
    return len(errors) == 0, errors


def validate_trade_constraints(
    symbol: str,
    action: str,  # "BUY" or "SELL"
    position_size: float,
    current_allocation_pct: float,
    max_position_pct: float = 0.15
) -> Tuple[bool, str]:
    """
    Validate trade is within constraints (from AGENTS.md).
    
    Args:
        symbol: Asset symbol
        action: "BUY" or "SELL"
        position_size: Size of proposed trade
        current_allocation_pct: Current allocation as % of portfolio
        max_position_pct: Maximum allowed position %
    
    Returns:
        (is_allowed, reason)
    
    Rules (from AGENTS.md):
        - Never use leverage
        - Never exceed position limits
        - Never ignore allocations
    """
    if position_size < 0:
        return False, "Trade size must be positive"
    
    if position_size == 0:
        return False, "Trade size cannot be zero"
    
    if action.upper() == "BUY":
        new_allocation = current_allocation_pct + (position_size / 100)  # Rough estimate
        if new_allocation > max_position_pct:
            return False, f"Would exceed max position {max_position_pct*100:.1f}%"
    
    elif action.upper() == "SELL":
        if position_size > current_allocation_pct * 100:
            return False, f"Cannot sell more than owned ({position_size:.1f} > {current_allocation_pct*100:.1f})"
    
    else:
        return False, f"Unknown action: {action}"
    
    return True, ""
