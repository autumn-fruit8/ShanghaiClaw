"""
S3 System — Macro regime assessment (S3a).

Evaluates the current macro environment by combining:
  - Monetary policy (Fed Funds Rate → real rate)
  - Inflation (CPI, Core PCE, Breakeven)
  - Growth (Industrial Production, Unemployment)
  - Credit conditions (BAA-10Y Spread, HY OAS)
  - Recession flag

Outputs a regime classification: expansion / slowdown / recession / recovery / overheat / stagflation
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from skills.analyze.scripts.s3_system.fred_client import get_fred_client
from skills.analyze.scripts.s3_system.config import MACRO_SERIES


_REQUIRED = {"FEDFUNDS", "DGS10", "DFII10", "T10YIE", "CPIAUCSL",
             "UNRATE", "BAA10Y", "INDPRO", "USREC"}


def assess_macro() -> dict[str, Any]:
    """Evaluate the current macro regime using FRED data.

    Returns dict with:
        regime: classification string
        confidence: 0.0-1.0
        drivers: per-factor assessment
        context: human-readable annotation list
    """
    client = get_fred_client()
    snapshot = client.get_macro_snapshot()

    result: dict[str, Any] = {
        "regime": "unknown",
        "confidence": 0.0,
        "drivers": {},
        "context": [],
    }

    # ── Extract latest raw values ──
    def _val(sid: str) -> float | None:
        v = snapshot.get(sid, {}).get("value")
        return float(v) if v is not None else None

    def _pct(sid: str) -> float | None:
        p = snapshot.get(sid, {}).get("percentile_10yr")
        return float(p) if p is not None else None

    fd = _val("FEDFUNDS")       # Fed Funds Rate
    dgs10 = _val("DGS10")       # 10Y Treasury
    tips = _val("DFII10")       # 10Y TIPS Real Yield
    be = _val("T10YIE")         # 10Y Breakeven Inflation
    cpi_yoy = _val("CPIAUCSL")  # CPI (index level, not YoY — we compute trend)
    unemp = _val("UNRATE")      # Unemployment Rate
    spread = _val("BAA10Y")     # BAA-10Y credit spread
    indpro = _val("INDPRO")     # Industrial Production
    rec = _val("USREC")         # NBER Recession flag

    # ── Derived calculations ──
    real_rate = (dgs10 - be) if (dgs10 is not None and be is not None) else None
    yield_curve = fd is not None and dgs10 is not None  # fed funds vs 10Y
    curve_inverted = (fd is not None and dgs10 is not None and fd > dgs10)

    # Inflation trend: CPI YoY from raw index is not directly available,
    # but we can compute from the index level's rate of change
    # For now, use Breakeven inflation as proxy
    inflation_regime = "stable"
    if be is not None:
        if be > 3.0:
            inflation_regime = "high"
        elif be > 2.5:
            inflation_regime = "elevated"
        elif be < 1.5:
            inflation_regime = "low"

    # Growth proxy: Industrial Production level / unemployment
    growth_regime = "stable"
    if indpro is not None:
        indpro_pct = _pct("INDPRO")
        if indpro_pct is not None:
            if indpro_pct < 0.10:
                growth_regime = "weak"
            elif indpro_pct > 0.90:
                growth_regime = "strong"

    # Credit conditions
    credit_regime = "normal"
    if spread is not None:
        if spread > 3.5:
            credit_regime = "tight"
        elif spread < 1.0:
            credit_regime = "loose"

    # Recession check
    in_recession = (rec is not None and rec == 1.0)

    # ── Regime classification ──
    regime = "neutral"
    confidence = 0.5
    context = []

    if in_recession:
        if inflation_regime in ("high", "elevated"):
            regime = "stagflation"
            confidence = 0.7
            context.append("NBER recession + elevated inflation → stagflation risk")
        else:
            regime = "recession"
            confidence = 0.7
            context.append("NBER recession flag active")
    elif growth_regime == "strong" and inflation_regime in ("high", "elevated"):
        regime = "overheat"
        confidence = 0.6
        context.append("Strong growth + elevated inflation → overheating")
    elif growth_regime == "strong" and inflation_regime in ("stable", "low"):
        regime = "expansion"
        confidence = 0.6
        context.append("Strong growth + contained inflation → expansion")
    elif growth_regime == "weak" and inflation_regime in ("high", "elevated"):
        regime = "stagflation_watch"
        confidence = 0.5
        context.append("Growth weakening + elevated inflation → watch stagflation")
    elif growth_regime == "weak" and inflation_regime == "low":
        regime = "slowdown"
        confidence = 0.5
        context.append("Growth weakness + low inflation → slowdown/disinflation")
    else:
        regime = "expansion"
        confidence = 0.4
        context.append("Neutral baseline → default expansion")

    # Curve inversion modifier
    if curve_inverted:
        confidence = max(confidence - 0.1, 0.0)
        context.append("⚠ Yield curve inverted (2Y > 10Y) — recession signal")

    # Credit conditions modifier
    if credit_regime == "tight":
        confidence = max(confidence - 0.05, 0.0)
        context.append("Credit spreads elevated — tighter financial conditions")

    result["regime"] = regime
    result["confidence"] = round(confidence, 2)
    result["drivers"] = {
        "real_rate": round(real_rate, 2) if real_rate is not None else None,
        "inflation_regime": inflation_regime,
        "growth_regime": growth_regime,
        "credit_regime": credit_regime,
        "curve_inverted": curve_inverted,
        "unemployment": unemp,
        "credit_spread_baa10y": spread,
    }
    result["context"] = context

    # ── CN bond yield for context (via bond_service, not akshare) ──
    try:
        from utils.data_service.bond_service import get_yield_series
        cn_yield = get_yield_series("cn", "10年")
        cgb10 = float(cn_yield.iloc[-1])
        result["drivers"]["cn_10y_cgb"] = round(cgb10, 2)
        context.append(f"CN 10Y CGB at {cgb10:.2f}%")
    except Exception:
        pass

    # ── Equity Risk Premium (ERP) ──
    try:
        us_erp = _compute_erp("us")
        if us_erp is not None:
            result["drivers"]["us_erp"] = round(us_erp, 2)
            if us_erp < 0:
                context.append(f"\u26a0 US ERP negative ({us_erp:.2f}%) \u2014 US bonds more attractive than US equities")
            elif us_erp < 1.5:
                context.append(f"US ERP {us_erp:.2f}% \u2014 moderate equity risk premium")
            elif us_erp < 3.0:
                context.append(f"US ERP {us_erp:.2f}% \u2014 healthy equity risk premium")
            else:
                context.append(f"US ERP {us_erp:.2f}% \u2014 elevated, equities undervalued vs bonds")
    except Exception:
        pass

    try:
        cn_erp = _compute_erp("cn")
        if cn_erp is not None:
            result["drivers"]["cn_erp"] = round(cn_erp, 2)
            if cn_erp < 0:
                context.append(f"\u26a0 CN ERP negative ({cn_erp:.2f}%)")
            elif cn_erp < 3.0:
                context.append(f"CN ERP {cn_erp:.2f}% \u2014 moderate")
            else:
                context.append(f"CN ERP {cn_erp:.2f}% \u2014 elevated, A-shares attractive vs bonds")
    except Exception:
        pass

    return result


def _compute_erp(region: str) -> float | None:
    """Compute Equity Risk Premium from raw data.

    US ERP = S&P 500 earnings yield - 10Y Treasury yield.
    CN ERP = CSI 300 earnings yield - 10Y CGB yield.

    All raw data sourced via market_service (with file cache).
    """
    try:
        from utils.data_service.bond_service import get_yield_series
        if region == "us":
            from utils.data_service.market_service import fetch_ticker_info
            info = fetch_ticker_info("SPY")
            if info:
                pe = info.get("trailingPE") or info.get("forwardPE")
                if pe and pe > 0:
                    earnings_yield = (1.0 / pe) * 100
                    yield_series = get_yield_series("us", "10Y")
                    dgs10 = float(yield_series.iloc[-1])
                    if dgs10 and dgs10 > 0:
                        return earnings_yield - dgs10
        elif region == "cn":
            from utils.data_service.market_service import fetch_csi_pe
            csi = fetch_csi_pe("000300")
            if csi and csi.get("current_pe") and csi["current_pe"] > 0:
                pe = csi["current_pe"]
                earnings_yield = (1.0 / pe) * 100
                yield_series = get_yield_series("cn", "10\u5e74")
                cgb = float(yield_series.iloc[-1])
                if cgb and cgb > 0:
                    return earnings_yield - cgb
    except Exception:
        pass
    return None
