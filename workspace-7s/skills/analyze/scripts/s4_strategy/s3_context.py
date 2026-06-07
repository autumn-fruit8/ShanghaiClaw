"""
s3_context.py — Inject S3 macro indicators as data columns.

Adds yield_pctile, vix_pctile to the indicator DataFrame so any
tactic YAML can reference them in `when:` conditions.

No dependency on strategy. No side effects. Pure column injection.
"""

from __future__ import annotations

import pandas as pd


def inject_s3_context(
    df: pd.DataFrame,
    symbol: str,
    region: str,
    species: str,
    tags: list[str],
    sleeve: str,
) -> pd.DataFrame:
    """Add S3 macro columns to indicator DataFrame.

    Columns added (when applicable):
      - yield_pctile: bond yield percentile (from bond_service, for bond assets)
      - vix_pctile:   VIX current percentile (for US assets)

    These are plain float columns — any tactic YAML can reference them in rules.
    """
    df = df.copy()

    if sleeve == "bond" and any(t in tags for t in (
        "short_duration", "medium_duration", "long_duration",
    )):
        tenor = _resolve_tenor(region, tags)
        try:
            from utils.data_service.bond_service import get_yield_percentiles
            yp = get_yield_percentiles(region, tenor)
            pct = yp["windows"][-2]["pctile"]
        except Exception:
            pct = 50.0
        df["yield_pctile"] = float(pct)

    if region.lower() == "us":
        try:
            from skills.analyze.scripts.s3_system.vix import VixAnalyzer
            vix = VixAnalyzer()
            series = vix.get_series()
            if series is not None and len(series) > 0:
                latest = float(series.iloc[-1])
                rank = (series < latest).mean()
                df["vix_pctile"] = float(rank * 100)
        except Exception:
            pass

    return df


def _resolve_tenor(region: str, tags: list[str]) -> str:
    """Map region + duration tags to bond_service tenor key."""
    if region.lower() == "us":
        return "30Y" if "long_duration" in tags else "10Y"
    return "30年" if "long_duration" in tags else "10年"
