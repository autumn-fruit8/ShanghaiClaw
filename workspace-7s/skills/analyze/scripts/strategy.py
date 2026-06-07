"""
strategy.py — 7S S4 strategy signal engine.

Backward-compatible re-export. The StrategyEngine class has moved to
s4_strategy/engine.py and run_strategy_pipeline() to s4_strategy/pipeline.py.

New code should import directly:
  from skills.analyze.scripts.s4_strategy.engine import StrategyEngine
  from skills.analyze.scripts.s4_strategy.pipeline import run_strategy_pipeline
"""

from skills.analyze.scripts.s4_strategy.engine import StrategyEngine
from skills.analyze.scripts.s4_strategy.pipeline import run_strategy_pipeline
