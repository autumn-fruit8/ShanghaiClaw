"""
S3 System — configuration and data source settings.
"""

from __future__ import annotations

import os


# FRED API
FRED_API_KEY: str = os.environ.get(
    "FRED_API_KEY",
    "252224e74e4b5d75333826790c430318",  # fallback — configured in .env
)

# Data cache: keep FRED/VIX data here so we don't re-fetch every analyze run
from config import CACHE_ROOT
CACHE_DIR = CACHE_ROOT
CACHE_TTL_HOURS: int = 4  # re-fetch if cache is older than this

# FRED series used for S3a macro assessment
MACRO_SERIES: dict[str, str] = {
    "FEDFUNDS": "Fed Funds Rate",
    "DGS10": "10Y Treasury Yield",
    "DFII10": "10Y TIPS Real Yield",
    "T10YIE": "10Y Breakeven Inflation",
    "CPIAUCSL": "CPI All Urban",
    "PCEPILFE": "Core PCE",
    "UNRATE": "Unemployment Rate",
    "BAA10Y": "BAA-10Y Credit Spread",
    "BAMLH0A0HYM2": "HY OAS",
    "INDPRO": "Industrial Production",
    "USREC": "NBER Recession Flag",
}

# VIX percentile thresholds
VIX_ZONE_THRESHOLDS: dict[str, float] = {
    "low": 0.25,       # below 25th percentile
    "normal": 0.75,    # 25th-75th
    "elevated": 0.90,  # 75th-90th
    "high": 0.95,      # 90th-95th
    "panic": 1.0,      # above 95th
}

# Yield curve inversion flag (2Y vs 10Y)
# DGS2 is 2Y Treasury; we can derive from FRED
YIELD_CURVE_SERIES = {
    "short": "DGS2",
    "long": "DGS10",
}
