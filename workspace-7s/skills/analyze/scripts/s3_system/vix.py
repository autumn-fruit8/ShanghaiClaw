"""
S3 System — VIX risk regime assessment.

Fetches VIX history via yfinance, computes percentile zones,
and returns a risk regime assessment.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from skills.analyze.scripts.s3_system.config import (
    CACHE_DIR,
    CACHE_TTL_HOURS,
    VIX_ZONE_THRESHOLDS,
)


class VixAnalyzer:
    """Fetch and analyze VIX for risk regime assessment."""

    def __init__(self, cache_dir: str | Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────────────
    # Cache helpers
    # ──────────────────────────────────────────────

    def _cache_path(self) -> Path:
        return self.cache_dir / "vix_history.json"

    def _is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        return age < timedelta(hours=CACHE_TTL_HOURS)

    def _load_cache(self) -> pd.Series | None:
        path = self._cache_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            idx = pd.to_datetime(data["index"])
            vals = pd.Series(data["values"], index=idx, dtype=float)
            return vals
        except Exception:
            return None

    def _write_cache(self, series: pd.Series):
        payload = {
            "index": [str(d.date()) for d in series.index],
            "values": [float(v) for v in series.values],
        }
        self._cache_path().write_text(json.dumps(payload))

    # ──────────────────────────────────────────────
    # Fetch
    # ──────────────────────────────────────────────

    def _fetch_vix(self) -> pd.Series | None:
        """Fetch VIX history via market_service (centralized yfinance → Tiingo)."""
        from utils.data_service.market_service import fetch_ohlcv
        try:
            df = fetch_ohlcv("^VIX", "us")
            if df is not None and not df.empty and "close" in df.columns:
                series = pd.Series(df["close"].values, index=pd.to_datetime(df["date"]), name="vix")
                return series.sort_index()
        except Exception as e:
            print(f"  \u26a0 VIX fetch failed: {e}")
        return None

    def get_vix_series(self) -> pd.Series | None:
        """Get VIX history, using cache."""
        cached = self._load_cache()
        if cached is not None:
            return cached
        series = self._fetch_vix()
        if series is not None and len(series) > 0:
            self._write_cache(series)
            return series
        return None

    # ──────────────────────────────────────────────
    # Analysis
    # ──────────────────────────────────────────────

    def assess(self) -> dict[str, Any]:
        """Full VIX risk regime assessment.

        Returns dict with:
            vix_current: latest VIX value
            vix_percentiles: key percentile thresholds
            vix_zone: low / normal / elevated / high / panic
            tail_risk: SKEW index if available (else None)
        """
        series = self.get_vix_series()
        result: dict[str, Any] = {
            "vix_current": None,
            "vix_percentiles": {},
            "vix_zone": "unknown",
            "vix_percentile_rank": None,
            "vix_100ma": None,
            "skew_current": None,
        }

        if series is None or len(series) < 100:
            return result

        vals = series.values.astype(float)
        current = float(vals[-1])

        # Percentile thresholds
        pcts = {p: float(np.percentile(vals, p)) for p in [5, 25, 50, 75, 90, 95]}

        # Current percentile rank
        rank = float((vals < current).sum() / len(vals))

        # Zone mapping
        zone = "low"
        for z_name, threshold in sorted(VIX_ZONE_THRESHOLDS.items(), key=lambda x: x[1]):
            if rank <= threshold:
                zone = z_name
                break

        # 100-day moving average for trend context
        recent = vals[-100:] if len(vals) >= 100 else vals
        ma100 = float(np.mean(recent))

        result["vix_current"] = round(current, 1)
        result["vix_percentiles"] = {str(k): round(v, 1) for k, v in pcts.items()}
        result["vix_zone"] = zone
        result["vix_percentile_rank"] = round(rank, 3)
        result["vix_100ma"] = round(ma100, 1)

        # Try SKEW (tail risk)
        try:
            from utils.data_service.market_service import fetch_ohlcv
            skew_df = fetch_ohlcv("^SKEW", "us")
            if skew_df is not None and not skew_df.empty and "close" in skew_df.columns:
                result["skew_current"] = round(float(skew_df["close"].iloc[-1]), 1)
        except Exception:
            pass

        return result


# Convenience
_analyzer: VixAnalyzer | None = None


def assess_vix() -> dict[str, Any]:
    global _analyzer
    if _analyzer is None:
        _analyzer = VixAnalyzer()
    return _analyzer.assess()
