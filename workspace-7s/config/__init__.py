"""Shared configuration for workspace-7s data layer.

All skills access plan and position data through this module.
"""
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = WORKSPACE_ROOT / "config"
LOGS_ROOT = WORKSPACE_ROOT / "logs"

PLANS_DIR = CONFIG_ROOT / "plans"
POSITIONS_DIR = LOGS_ROOT / "positions"
SNAPSHOTS_DIR = LOGS_ROOT / "snapshots"
BACKTEST_DIR = LOGS_ROOT / "backtest"
REPORTS_DIR = LOGS_ROOT / "reports"

# Holdings cache (semi-static reference data)
HOLDINGS_DIR = WORKSPACE_ROOT / "logs" / "holdings"

# System cache root (FRED data, bond yields, VIX — 4h TTL)
CACHE_ROOT = WORKSPACE_ROOT / "adhoc" / "cache" / "system"
