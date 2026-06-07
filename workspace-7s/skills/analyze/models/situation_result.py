"""SituationResult — S2 output contract."""

from dataclasses import dataclass
import pandas as pd


@dataclass
class SituationResult:
    """
    S2 Situation output: raw indicator values for the latest date.

    All indicators are computed by situation.py using the same algorithms
    as Jarvis strategy_engine.py (vectorized + per-row LDev loop).

    Attributes
    ----------
    symbol : str
        Asset symbol.
    ldev : float
        Latest log-deviation from 1250-day rolling OLS trend.
    z_score : float
        Latest Z-score of (price - MA250) / MA250 vs its expanding mean/std.
    rsi : float
        Latest EWM-based RSI (com=13).
    ma_base : float
        Latest 250-day simple moving average.
    ma_tactical : float
        Latest 60-day simple moving average.
    ma60_pct : float
        Percentage distance from MA60: (price - MA60) / MA60 * 100.
    price_current : float
        Latest price (val column).
    date_current : str
        Latest date as ISO string.
    df_with_indicators : pd.DataFrame
        Full price history with appended indicator columns:
        log_val, ma_base, ma_tactical, z_score, rsi, log_dev.
    """

    symbol: str
    ldev: float
    z_score: float
    rsi: float
    ma_base: float
    ma_tactical: float
    ma60_pct: float
    price_current: float
    date_current: str
    df_with_indicators: pd.DataFrame
