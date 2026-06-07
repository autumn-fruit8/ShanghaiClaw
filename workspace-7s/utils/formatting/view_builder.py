from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from skills.analyze.scripts.species import resolve_analysis_selection
from skills.analyze.scripts.system import assess_regime


def _normalize_signal(value: str | None) -> str:
    text = str(value or "").upper()
    for label in ("OPPORTUNITY", "WARNING", "DANGER", "BEARISH", "BULLISH", "NEUTRAL"):
        if label in text:
            return "WARNING" if label in {"DANGER", "BEARISH"} else label
    return "UNKNOWN"


def _load_profile(profile_name: str | None) -> dict[str, Any]:
    """Load investor profile from config/state_db/investor-profiles.json if available."""
    from pathlib import Path
    import os

    profiles_path = Path(os.getenv("SEVENS_WORKSPACE_ROOT", str(Path.cwd()))) / "config" / "state_db" / "investor-profiles.json"
    if profiles_path.exists():
        try:
            import json
            with profiles_path.open("r", encoding="utf-8") as f:
                profiles = json.load(f)
            if profile_name and profile_name in profiles:
                return profiles[profile_name]
            if profiles:
                first = next(iter(profiles.values()))
                return first
        except Exception:
            pass

    # S5 stub fallback
    return {
        "profile": profile_name or "balanced",
        "risk_tolerance": "medium",
        "max_single_weight": 0.4,
        "rebalance_style": "gradual",
    }


def _build_species_layer(asset: dict[str, Any]) -> dict[str, Any]:
    strategy_type = str(asset.get("strategy_type", "STEADY")).lower()
    gene = {
        "steady": "steady",
        "volatile": "volatile",
        "momentum": "momentum",
    }.get(strategy_type, "steady")

    return {
        "gene": gene,
        "asset_type": asset.get("asset_type", "unknown"),
        "region": asset.get("region", "ALL"),
    }


def _build_situation_layer(snapshot_row: dict[str, Any] | None) -> dict[str, Any]:
    if not snapshot_row:
        return {
            "signal": "UNAVAILABLE",
            "ldev": None,
            "rsi": None,
            "state": "pre-run",
        }

    signal = _normalize_signal(snapshot_row.get("Signal"))
    ldev = snapshot_row.get("LDev")
    rsi = snapshot_row.get("RSI")
    if signal == "OPPORTUNITY":
        state = "dislocated"
    elif signal == "WARNING":
        state = "stretched"
    elif signal == "NEUTRAL":
        state = "balanced"
    else:
        state = "observing"

    return {
        "signal": signal,
        "ldev": ldev,
        "rsi": rsi,
        "state": state,
    }


def _build_strategy_layer(species: dict[str, Any], situation: dict[str, Any]) -> dict[str, Any]:
    signal = situation.get("signal")
    gene = species.get("gene")

    if signal == "OPPORTUNITY":
        action = "accumulate"
    elif signal == "WARNING":
        action = "trim_or_wait"
    else:
        action = "hold"

    confidence = 0.55
    if signal == "OPPORTUNITY":
        confidence = 0.72 if gene == "steady" else 0.64
    elif signal == "WARNING":
        confidence = 0.68

    return {
        "action": action,
        "confidence": round(confidence, 2),
        "signal": signal,
    }


def _build_system_layer(region: str, snapshot_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """S3 — Market system assessment. Calls assess_regime for the region."""
    # Build a representative asset dict for the region
    asset = {"symbol": region, "region": region, "name": f"{region} Market"}
    try:
        s3 = assess_regime(asset)
        # Also compute signal counts (original behavior kept for backward compat)
        counts = {"OPPORTUNITY": 0, "WARNING": 0, "NEUTRAL": 0, "BULLISH": 0, "UNKNOWN": 0}
        for row in snapshot_rows:
            counts[_normalize_signal(row.get("Signal"))] = counts.get(_normalize_signal(row.get("Signal")), 0) + 1

        return {
            "region": region,
            "regime": s3.get("regime_summary", "neutral"),
            "confidence": s3.get("confidence", 0.0),
            "signal_bias": s3.get("signal_bias", "neutral"),
            "macro": s3.get("macro"),
            "risk": s3.get("risk"),
            "context": s3.get("context", []),
            "signal_counts": counts,
        }
    except Exception as exc:
        # Fallback: original signal-count logic
        counts = {"OPPORTUNITY": 0, "WARNING": 0, "NEUTRAL": 0, "BULLISH": 0, "UNKNOWN": 0}
        for row in snapshot_rows:
            counts[_normalize_signal(row.get("Signal"))] = counts.get(_normalize_signal(row.get("Signal")), 0) + 1

        if counts.get("WARNING", 0) > counts.get("OPPORTUNITY", 0):
            regime = "cautious"
        elif counts.get("OPPORTUNITY", 0) > 0:
            regime = "selective-opportunity"
        else:
            regime = "balanced"

        return {
            "region": region,
            "regime": regime,
            "signal_counts": counts,
            "s3_error": str(exc),
        }


def _build_stake_layer(selection: dict[str, Any], assets: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    weight_sum = round(sum(float(asset.get("weight", 0.0)) for asset in assets), 6)
    max_weight = max((float(asset.get("weight", 0.0)) for asset in assets), default=0.0)
    max_allowed = float(profile.get("max_single_weight", 0.4))

    return {
        "plan_name": selection.get("value") if selection.get("mode") == "stake" else None,
        "selection_mode": selection.get("mode"),
        "weight_sum": weight_sum,
        "rebalance_needed": abs(weight_sum - 1.0) > 1e-6 or max_weight > max_allowed,
        "max_weight": round(max_weight, 4),
    }


def _build_self_evolution_layer(snapshot_rows: list[dict[str, Any]]) -> dict[str, Any]:
    signals = [_normalize_signal(row.get("Signal")) for row in snapshot_rows]
    review_items: list[str] = []

    if "WARNING" in signals:
        review_items.append("Review overheated assets before adding risk")
    if "OPPORTUNITY" in signals:
        review_items.append("Track dip-buy candidates for confirmation")
    if not review_items:
        review_items.append("No strong edge detected; keep monitoring")

    return {
        "review_date": str(date.today()),
        "review_items": review_items,
    }


def build_seven_layer_view(
    workspace_root: str | Path,
    region: str | None = None,
    symbol: str | None = None,
    symbols: str | None = None,
    profile_name: str | None = None,
    selection_payload: dict[str, Any] | None = None,
    snapshot_rows: list[dict[str, Any]] | None = None,
    use_default_watchlist: bool = False,
    use_active_state: bool = False,
) -> dict[str, Any]:
    workspace_root = Path(workspace_root)
    payload = selection_payload or resolve_analysis_selection(
        workspace_root=workspace_root,
        region=region or "all",
        symbol=symbol,
        symbols=symbols,
        use_default_watchlist=use_default_watchlist,
        use_active_state=use_active_state,
    )

    snapshot_rows = list(snapshot_rows or [])
    snapshot_by_symbol = {
        str(row.get("symbol", "")).strip(): row
        for row in snapshot_rows
        if isinstance(row, dict)
    }

    profile = _load_profile(profile_name)
    system = _build_system_layer(payload.get("region", "ALL"), snapshot_rows)
    stake_layer = _build_stake_layer(payload.get("selection", {}), payload.get("assets", []), profile)

    assets_out: list[dict[str, Any]] = []
    gene_mix: dict[str, int] = {}
    for asset in payload.get("assets", []):
        species = _build_species_layer(asset)
        situation = _build_situation_layer(snapshot_by_symbol.get(asset.get("symbol")))
        strategy = _build_strategy_layer(species, situation)
        gene_mix[species["gene"]] = gene_mix.get(species["gene"], 0) + 1

        assets_out.append(
            {
                "symbol": asset.get("symbol"),
                "name": asset.get("name"),
                "weight": asset.get("weight"),
                "layers": {
                    "species": species,
                    "situation": situation,
                    "strategy": strategy,
                    "stake": {
                        "target_weight": asset.get("weight"),
                    },
                },
            }
        )

    return {
        "region": payload.get("region", "ALL"),
        "selection": payload.get("selection", {}),
        "species": {
            "asset_count": len(payload.get("assets", [])),
            "gene_mix": gene_mix,
        },
        "system": system,
        "self_portrait": profile,
        "stake": stake_layer,
        "self_evolution": _build_self_evolution_layer(snapshot_rows),
        "assets": assets_out,
    }


def write_seven_layer_view(
    workspace_root: str | Path,
    out_dir: str | Path,
    selection_payload: dict[str, Any],
    snapshot_rows: list[dict[str, Any]] | None = None,
    profile_name: str | None = None,
    run_date: str | None = None,
) -> Path:
    context = build_seven_layer_view(
        workspace_root=workspace_root,
        selection_payload=selection_payload,
        snapshot_rows=snapshot_rows,
        profile_name=profile_name,
    )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    region = str(context.get("region", "all")).lower()
    label = run_date or str(date.today())
    out_path = out_dir / f"{label}_{region}_7s.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(context, f, ensure_ascii=False, indent=2)
    return out_path
