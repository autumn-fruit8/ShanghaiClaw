"""
S3 System — FRED data client.

Wraps fredapi to fetch macro-economic time series used by S3a.
Provides caching to avoid re-fetching on every analyze call.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from skills.analyze.scripts.s3_system.config import (
    FRED_API_KEY,
    CACHE_DIR,
    CACHE_TTL_HOURS,
    MACRO_SERIES,
)


class FredClient:
    """Fetch and cache FRED macro data series."""

    def __init__(self, api_key: str | None = None, cache_dir: str | Path | None = None):
        self.api_key = api_key or FRED_API_KEY
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._fred = None  # lazy import

    # ──────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────

    def _get_fred(self):
        if self._fred is None:
            from fredapi import Fred
            self._fred = Fred(api_key=self.api_key)
        return self._fred

    def _cache_path(self, series_id: str) -> Path:
        return self.cache_dir / f"fred_{series_id}.json"

    def _is_cache_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        return age < timedelta(hours=CACHE_TTL_HOURS)

    def _load_cache(self, path: Path) -> pd.Series | None:
        try:
            data = json.loads(path.read_text())
            idx = pd.to_datetime(data["index"])
            vals = pd.Series(data["values"], index=idx, dtype=float)
            return vals
        except Exception:
            return None

    def _write_cache(self, path: Path, series: pd.Series):
        payload = {
            "index": [str(d.date()) for d in series.index],
            "values": [float(v) for v in series.values],
        }
        path.write_text(json.dumps(payload))

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def get_series(self, series_id: str) -> pd.Series | None:
        """Fetch a single FRED series, using cache when possible."""
        cache_path = self._cache_path(series_id)

        # Try cache first
        if self._is_cache_fresh(cache_path):
            cached = self._load_cache(cache_path)
            if cached is not None and len(cached) > 0:
                return cached

        # Fetch from API
        try:
            fred = self._get_fred()
            series = fred.get_series(series_id)
            if series is not None and len(series) > 0:
                self._write_cache(cache_path, series)
                return series
        except Exception as exc:
            # Log warning but don't crash — try returning stale cache
            print(f"  ⚠ FRED {series_id}: fetch failed — {exc}")

            # Fallback: serve stale cache if available
            if cache_path.exists():
                stale = self._load_cache(cache_path)
                if stale is not None and len(stale) > 0:
                    return stale
        return None

    def get_latest(self, series_id: str) -> float | None:
        """Get the most recent value of a FRED series."""
        series = self.get_series(series_id)
        if series is None or series.empty:
            return None
        return float(series.iloc[-1])

    def get_percentile(self, series_id: str, n_days: int | None = None) -> float | None:
        """Get the current value's percentile rank vs its own history.

        Args:
            series_id: FRED series ID
            n_days: lookback window in calendar days (None = full history)

        Returns:
            Percentile (0.0-1.0), or None if data unavailable.
        """
        series = self.get_series(series_id)
        if series is None or series.empty:
            return None

        if n_days is not None:
            cutoff = datetime.now() - timedelta(days=n_days)
            series = series[series.index >= pd.Timestamp(cutoff)]

        if len(series) < 10:
            return None

        current = float(series.iloc[-1])
        rank = (series < current).sum()
        return rank / len(series)

    def get_macro_snapshot(self) -> dict[str, Any]:
        """Fetch latest values and percentiles for all configured macro series.

        Returns:
            Dict keyed by series code, with value and percentile.
        """
        snapshot = {}
        for sid, name in MACRO_SERIES.items():
            val = self.get_latest(sid)
            pct = self.get_percentile(sid, n_days=365 * 10)  # 10yr lookback
            snapshot[sid] = {
                "name": name,
                "value": val,
                "percentile_10yr": round(pct, 3) if pct is not None else None,
            }
        return snapshot


# Convenience singleton (module-level cache)
_client: FredClient | None = None


def get_fred_client() -> FredClient:
    global _client
    if _client is None:
        _client = FredClient()
    return _client


def get_macro_snapshot() -> dict:
    """Convenience: get full macro snapshot."""
    return get_fred_client().get_macro_snapshot()
