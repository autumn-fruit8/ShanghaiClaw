"""
Formatters for reports, messages, and data presentation.

Functions for:
- Formatting indicator values for display
- Generating signal descriptions
- Creating report sections
- Formatting tables and lists
"""

from typing import Dict, List, Tuple
from datetime import datetime
from ..constants import Signal, Strategy, Region


def format_percentage(value: float, decimals: int = 2) -> str:
    """Format percentage with sign and decimals."""
    if value is None or value != value:  # Check for NaN
        return "N/A"
    
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def format_price(value: float, decimals: int = 2) -> str:
    """Format price with proper decimals."""
    if value is None or value != value:  # Check for NaN
        return "N/A"
    
    return f"{value:.{decimals}f}"


def format_indicators(indicators: Dict) -> str:
    """
    Format all technical indicators for display.
    
    Args:
        indicators: Dictionary from calculate_all_indicators()
    
    Returns:
        Formatted string for reporting
    """
    lines = [
        f"Price: {format_price(indicators.get('price_current'))}",
        f"LDev: {format_percentage(indicators.get('ldev'))} (MA: {format_price(indicators.get('ldev_ma'))})",
        f"Z-Score: {indicators.get('z_score', 'N/A'):.2f}",
        f"RSI: {indicators.get('rsi', 'N/A'):.1f}",
        f"MA60: {format_price(indicators.get('ma_60'))} | MA240: {format_price(indicators.get('ma_240'))}",
    ]
    return "\n".join(lines)


def signal_to_description(signal: Signal, severity: str = "") -> str:
    """
    Convert signal enum to human-readable description.
    
    Args:
        signal: Signal enum value
        severity: Optional severity indicator (high/medium/low)
    
    Returns:
        Human-readable signal description
    
    Example:
        signal_to_description(Signal.BULLISH) -> "📈 Bullish - Strong buy signal"
    """
    descriptions = {
        Signal.BULLISH: "📈 Bullish - Strong buy signal, high conviction",
        Signal.BEARISH: "📉 Bearish - Strong sell signal, reduce exposure",
        Signal.OPPORTUNITY: "🎯 Opportunity - Good entry point, consider accumulating",
        Signal.WARNING: "⚠️  Warning - Caution suggested, reduce risk",
        Signal.DANGER: "🚨 Danger - Risk of loss, consider exit",
        Signal.NEUTRAL: "⏸️  Neutral - No clear signal, hold position",
    }
    
    desc = descriptions.get(signal, str(signal))
    if severity:
        desc = f"[{severity.upper()}] {desc}"
    
    return desc


def format_signal_justification(
    symbol: str,
    strategy: Strategy,
    signal: Signal,
    indicators: Dict,
    triggers: List[str]
) -> str:
    """
    Format detailed signal justification for reporting.
    
    Args:
        symbol: Asset symbol
        strategy: Strategy type
        signal: Generated signal
        indicators: Technical indicators
        triggers: List of conditions that triggered signal
    
    Returns:
        Formatted justification string
    """
    lines = [
        f"═══ {symbol} [{strategy.value}] ═══",
        f"Signal: {signal_to_description(signal)}",
        "",
        "Indicators:",
        format_indicators(indicators),
        "",
        "Triggers:",
    ]
    
    for trigger in triggers:
        lines.append(f"  ✓ {trigger}")
    
    return "\n".join(lines)


def format_portfolio_summary(
    portfolio: Dict[str, float],
    total_value: float,
    change_pct: float = None,
    timestamp: datetime = None
) -> str:
    """
    Format portfolio summary for display.
    
    Args:
        portfolio: Dict of {symbol: value}
        total_value: Total portfolio value
        change_pct: Daily change percentage
        timestamp: Timestamp of snapshot
    
    Returns:
        Formatted portfolio summary
    """
    lines = [
        "═══ Portfolio Summary ═══",
        f"Total Value: {format_price(total_value)}",
    ]
    
    if change_pct is not None:
        lines.append(f"Daily Change: {format_percentage(change_pct)}")
    
    if timestamp:
        lines.append(f"Timestamp: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
    
    lines.append("")
    
    # Sort by value descending
    sorted_positions = sorted(portfolio.items(), key=lambda x: x[1], reverse=True)
    for symbol, value in sorted_positions:
        pct = (value / total_value) * 100
        lines.append(f"  {symbol:15} {format_price(value):>12}  ({format_percentage(pct):>6})")
    
    return "\n".join(lines)


def format_table(
    headers: List[str],
    rows: List[List[str]],
    title: str = ""
) -> str:
    """
    Format data as a simple text table.
    
    Args:
        headers: Column headers
        rows: List of row data (each row is list of strings)
        title: Optional title
    
    Returns:
        Formatted table string
    """
    if not headers or not rows:
        return ""
    
    # Calculate column widths
    col_widths = []
    for i in range(len(headers)):
        max_width = len(str(headers[i]))
        for row in rows:
            if i < len(row):
                max_width = max(max_width, len(str(row[i])))
        col_widths.append(max_width)
    
    # Build table
    lines = []
    if title:
        lines.append(f"═══ {title} ═══")
    
    # Header
    header_line = " | ".join(
        str(h).ljust(w) for h, w in zip(headers, col_widths)
    )
    lines.append(header_line)
    lines.append("─" * len(header_line))
    
    # Rows
    for row in rows:
        row_line = " | ".join(
            str(cell).ljust(w) if cell is not None else "N/A".ljust(w)
            for cell, w in zip(row, col_widths)
        )
        lines.append(row_line)
    
    return "\n".join(lines)


def format_daily_report_header(
    region: Region,
    date: datetime,
    market_status: str = "OPEN"
) -> str:
    """
    Format header for daily report.
    
    Args:
        region: Market region
        date: Report date
        market_status: "OPEN", "CLOSED", "PRE_MARKET"
    
    Returns:
        Formatted header string
    """
    status_emoji = {
        "OPEN": "🟢",
        "CLOSED": "🔴",
        "PRE_MARKET": "🟡",
    }
    
    emoji = status_emoji.get(market_status, "⚪")
    date_str = date.strftime("%Y-%m-%d")
    
    return f"\n{'='*50}\n{emoji} {region.value} Market Report - {date_str}\nMarket Status: {market_status}\n{'='*50}\n"


def format_error_message(error_code: str, details: str = "") -> str:
    """
    Format error messages consistently.
    
    Args:
        error_code: Error code (e.g., "API_TIMEOUT", "INVALID_DATA")
        details: Additional details
    
    Returns:
        Formatted error message
    """
    lines = [
        f"❌ Error [{error_code}]",
        f"   {details}" if details else "",
    ]
    return "\n".join(filter(None, lines))


def format_timestamp(dt: datetime = None, include_time: bool = True) -> str:
    """
    Format timestamp consistently.
    
    Args:
        dt: Datetime to format (default: now)
        include_time: Include time component
    
    Returns:
        Formatted timestamp string
    """
    if dt is None:
        dt = datetime.now()
    
    if include_time:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    else:
        return dt.strftime("%Y-%m-%d")
