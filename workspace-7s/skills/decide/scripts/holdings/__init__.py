"""Holdings — concentration overlap analysis for decide.

Public API:
  - holdings.overlap.compute_overlap() — cross-reference engine
  - holdings.format_overlap_report() — markdown formatting
"""
from holdings.overlap import compute_overlap


def format_overlap_report(overlap: dict) -> str:
    """Format overlap analysis as a markdown section for the decide report.

    Args:
        overlap: dict returned by compute_overlap()

    Returns:
        Markdown string ready to append to the decide report.
    """
    if overlap["etf_count"] == 0:
        return "\n⚠️  Holdings overlap analysis: no data available.\n"

    threshold_pct = overlap["threshold"]
    flagged = [o for o in overlap["overlaps"] if o["flagged"]]
    lines = [
        "",
        "─── Holdings Overlap Analysis ───",
        f"Top-10 holdings cross-referenced across {overlap['etf_count']} ETFs:",
        "",
    ]

    for o in overlap["overlaps"]:
        source_strs = [f"{s['etf']}({s['weight_in_etf']:.1f}%)" for s in o["sources"]]
        flag = "⚠️ " if o["flagged"] else "✅"
        lines.append(f"  {o['name']:<8} {o['symbol']:<6} {' | '.join(source_strs)}")
        lines.append(f"          → combined portfolio weight: {o['combined_weight']:.2f}%  {flag}")

    lines.append("")
    lines.append(f"Concentration threshold: {threshold_pct:.0f}% of portfolio")
    lines.append(f"Stocks flagged: {len(flagged)} / {overlap['holdings_count']}")
    lines.append("")

    return "\n".join(lines)
