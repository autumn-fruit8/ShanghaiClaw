"""Ranking — dual-threshold signal model.

BUY  when score >  buy_threshold  (positive momentum, enter)
SELL when score <  sell_threshold (negative momentum, exit)
HOLD when score <= buy_threshold AND score >= sell_threshold (between)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MomentumResult:
    symbol: str
    name: str
    score: float
    method: str
    period: int


@dataclass
class MomentumRanking:
    results: list[MomentumResult] = field(default_factory=list)
    buy_threshold: float = 0.05
    sell_threshold: float = -0.02
    active_symbols: set[str] = field(default_factory=set)


def rank(results: list[MomentumResult], buy_threshold: float = 0.05,
         sell_threshold: float = -0.02,
         active_symbols: set[str] | None = None) -> MomentumRanking:
    """Sort by score descending, tag signals by dual thresholds."""
    active = active_symbols or set()
    sorted_results = sorted(results, key=lambda r: r.score, reverse=True)
    return MomentumRanking(results=sorted_results, buy_threshold=buy_threshold,
                           sell_threshold=sell_threshold, active_symbols=active)


def signal_for(ranking: MomentumRanking, result: MomentumResult) -> str:
    """Dual-threshold signal: BUY above buy_th, SELL below sell_th, HOLD between."""
    if result.score > ranking.buy_threshold:
        return "BUY"
    elif result.score < ranking.sell_threshold:
        return "SELL"
    else:
        return "HOLD"


def generate_decision(ranking: MomentumRanking) -> str:
    """Generate human-readable decision text."""
    if not ranking.results:
        return "No assets to evaluate"

    buys = [r for r in ranking.results if signal_for(ranking, r) == "BUY"]
    holds = [r for r in ranking.results if signal_for(ranking, r) == "HOLD"]
    sells = [r for r in ranking.results if signal_for(ranking, r) == "SELL"]

    lines: list[str] = []
    if buys:
        lines.append(f"BUY ({len(buys)}): " + ", ".join(f"{r.symbol} ({r.score:+.3f})" for r in buys))
    if holds:
        lines.append(f"HOLD ({len(holds)}): " + ", ".join(f"{r.symbol} ({r.score:+.3f})" for r in holds))
    if sells:
        lines.append(f"SELL ({len(sells)}): " + ", ".join(f"{r.symbol} ({r.score:+.3f})" for r in sells))

    lines.append("")
    lines.append(f"Thresholds: BUY > {ranking.buy_threshold:+.2f} | HOLD between | SELL < {ranking.sell_threshold:+.2f}")
    lines.append("")
    lines.append("Ranking:")
    for i, r in enumerate(ranking.results):
        sig = signal_for(ranking, r)
        lines.append(f"  #{i+1} {r.symbol:<8} {r.name:<20} {r.score:+.4f} → {sig}")

    return "\n".join(lines)
