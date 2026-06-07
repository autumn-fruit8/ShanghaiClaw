"""
Shared utilities for workspace-7s services.

Exports:
  - constants: Enums and configuration
  - indicators: Technical analysis functions
  - validators: Data and constraint validation
  - formatters: Output formatting helpers
"""

from .constants import (
    Region,
    AssetType,
    Strategy,
    Signal,
    STEADY_PARAMS,
    VOLATILE_PARAMS,
    MOMENTUM_PARAMS,
    INDICATOR_PERIODS,
    DATA_VALIDATION,
)

from .indicators.indicators import (
    calculate_ldev,
    calculate_z_score,
    calculate_rsi,
    calculate_moving_averages,
    calculate_all_indicators,
    validate_indicators,
)

from .validation.validators import (
    validate_price_data,
    validate_portfolio,
    validate_signal_generation,
    validate_region_config,
    validate_trade_constraints,
)

from .formatting.formatters import (
    format_percentage,
    format_price,
    format_indicators,
    signal_to_description,
    format_signal_justification,
    format_portfolio_summary,
    format_table,
    format_daily_report_header,
    format_error_message,
    format_timestamp,
)

__all__ = [
    # Constants
    "Region",
    "AssetType",
    "Strategy",
    "Signal",
    "STEADY_PARAMS",
    "VOLATILE_PARAMS",
    "MOMENTUM_PARAMS",
    "INDICATOR_PERIODS",
    "DATA_VALIDATION",
    # Indicators
    "calculate_ldev",
    "calculate_z_score",
    "calculate_rsi",
    "calculate_moving_averages",
    "calculate_all_indicators",
    "validate_indicators",
    # Validators
    "validate_price_data",
    "validate_portfolio",
    "validate_signal_generation",
    "validate_region_config",
    "validate_trade_constraints",
    # Formatters
    "format_percentage",
    "format_price",
    "format_indicators",
    "signal_to_description",
    "format_signal_justification",
    "format_portfolio_summary",
    "format_table",
    "format_daily_report_header",
    "format_error_message",
    "format_timestamp",
]
