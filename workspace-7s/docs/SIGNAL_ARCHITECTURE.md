# Signal Architecture: Situation → Strategy → Execution

> **Status**: Implemented (2026-06-01)
> **Previous**: v1 proposed 2-layer Pulse+Call — rejected (stateful signal creates look-ahead bias; redundant with existing backtest state machine)
> **Principle**: Three independent, non-overlapping layers. Signal engine remains stateless. Position tracking stays in execution layer. Zero coupling between strategy and backtest.

---

## Layer Naming (Bilingual Standard)

| Layer | English | 中文 | S-Code | Question | Source |
|-------|---------|------|--------|----------|--------|
| **L1** | Situation | 统计层 | S2 | 价格极端吗? | `situation.py`, `classify_pulse()` |
| **L2** | Strategy | 策略层 | S4 | 策略怎么说? | `tactic.py`, `signal_computer.py`, `pipeline.py` |
| **L3** | Execution | 执行层 | — | 能做吗? | `account_engine.py`, `account_simulator.py` |
| **Meta** | Alignment | 校准层 | — | 统计和策略一致吗? | `alignment.py` |

| Sub-layer | English | 中文 | Where |
|-----------|---------|------|-------|
| S1 | Species | 资产分类 | `species.py` — asset type for routing |
| S2 | Situation | 统计指标 | `situation.py` — raw indicators (LDev, ZScore, RSI) |
| — | Pulse | 脉冲 | `classify_pulse()` — statistical classification |
| S3 | System | 宏观环境 | `s3_context.py` — yield_pctile, vix_pctile |
| S4 | Strategy | 策略信号 | `tactic.py` — rule evaluation, signal names |
| — | Alignment | 一致性 | `alignment.py` — Pulse × Signal cross-product |
| E3 | Execution | 执行验证 | `account_engine.py` — position-aware filtering |

---

## Vocabulary (Single Source of Truth)

Every concept in the 7S signal pipeline has exactly one name. The same name is used in code, configs, output tables, and this document.

### Domain Concepts

| Term | Definition | Where Defined | Values / Examples |
|------|-----------|---------------|-------------------|
| **Species** | Asset behavior classification for strategy routing. NOT a statistical property. | `species.py` → `config/strategies/routing.yaml` | `STEADY`, `VOLATILE`, `MOMENTUM`, `BOND` |
| **Indicator** | A single computed metric from price data. Stateless per-row. | `situation.py` + `signal_computer.py` | `ldev`, `zscore`, `rsi`, `roc`, `adx`, `vol_ratio`, `ma_cross` |
| **Pulse** | Statistical classification of price extremity. Species-independent. Pure function. | NEW: `classify_pulse()` in `situation.py` | `EXTREME_OB`, `OVERBOUGHT`, `STRONG`, `NEUTRAL`, `WEAK`, `OVERSOLD`, `EXTREME_OS` |
| **Strategy** | A named (profile, tactic) pair routed per species/symbol. | `config/strategies/routing.yaml` | `{profile: 7s-base, tactic: dca}`, `{profile: momentum, tactic: trend}` |
| **Profile** | A named indicator set that the strategy requires. | `config/strategies/profiles/*.yaml` | `momentum`, `7s-base`, `dual-ma` |
| **Tactic** | A named ordered rule set that evaluates indicator conditions. | `config/strategies/tactics/*.yaml` | `follow`, `trend`, `volume`, `dca`, `deep-value`, `dual-ma-follow` |
| **Rule** | One condition→action pair within a tactic. Has an ID, a label, and a signal. | Inside tactic YAML `rules[]` | `trend_entry`, `deep_value`, `golden_cross` |
| **Directive** | What the rule instructs to DO. Verb + weight. | `do.verb` + `do.fraction` in rule | `BUY 1.0`, `SELL 0.5`, `HOLD`, `CLOSE 1.0` |
| **Signal** | Strategy-defined classification of a directive — what the strategy THINKS, not what it DOES. | NEW: `signal` field per rule in tactic YAML | `TREND_ENTRY`, `DEEP_VALUE`, `GOLDEN_CROSS`, `BUBBLE_EXIT` |
| **Alignment** | Cross-product of Pulse × Signal. Indicates statistical-strategy convergence/divergence. | Computed on-the-fly | `CONFIRMED`, `DIVERGENT`, `NEUTRAL` |
| **Position** | Current investment state: are we invested? | `AccountState` in account engine | `IN` (shares > 0), `OUT` (shares == 0) |
| **Execution** | Whether a directive can be applied given current position and cash. | `execute_trade()` in account engine | `EXECUTED`, `SKIPPED_NO_CASH`, `SKIPPED_NO_SHARES`, `PARTIAL` |

### What We Rename

| Old Name (v1 / current) | New Name | Reason |
|---|---|---|
| `Call` (v1 proposal) | *(removed)* | Redundant — position engine already tracks state. Stateful signal introduces look-ahead bias. |
| `signal_type` (tactic.py) | `signal` (per rule) | Strategy-defined, not verb-derived. `BUY→BULLISH` conflates action with interpretation. |
| `mkt_type` (engine.py) | Legacy — engine.py unchanged | Old engine serves BOND/backtest paths. Not refactored in this plan. |
| `Signal_Action` (output) | `Pulse Signal Strategy Directive` | Four columns instead of one mixed string. |
| Species-adjusted LDev thresholds | Universal LDev thresholds | Species is a routing label, not a statistical attribute. σ means σ regardless of species. |

---

## Architecture Overview

```
                          Price Series (daily OHLCV)
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────────┐
│  LAYER 1: SITUATION  (stateless pure functions)                        │
│                                                                         │
│  situation.py        → ldev, zscore, rsi, ma250, ma60                  │
│  signal_computer.py  → + roc, adx, slope, vol_ratio, ma_cross,         │
│                         price_above_ma, vol_signal                      │
│  NEW: classify_pulse → pulse_type, pulse_desc                          │
│                                                                         │
│  STATELESS. Species-independent. Pure functions.                        │
│  Answers: "Is the price statistically extreme?"                        │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
              indicator_df (with pulse_type column added)
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────────┐
│  LAYER 2: STRATEGY  (stateless rule evaluation)                         │
│                                                                         │
│  Input:   indicator_df  +  Strategy{profile, tactic, params}            │
│  Output:  eval_df with { rule_id, rule_label, signal, verb, fraction }  │
│                                                                         │
│  registry.py      → load Strategy (profile + tactic + params)          │
│  tactic.py        → evaluate_tactic(df, tactic) → directives per row   │
│                                                                         │
│  Each rule in tactic YAML now includes a `signal` field:                │
│    - id: trend_entry                                                    │
│      label: Trend Entry                                                 │
│      when: {roc: {gt: 0}, price_above_ma_200: {eq: true}}              │
│      do: {verb: BUY, fraction: 1.0}                                    │
│      signal: TREND_ENTRY          ← NEW: strategy-defined               │
│                                                                         │
│  STATELESS. No position awareness. Produces directives, not trades.     │
│  Answers: "What does this strategy say about today?"                    │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
              directives: {signal, verb, fraction} per row
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────────┐
│  LAYER 3: EXECUTION  (stateful position-aware filtering)                │
│                                                                         │
│  Input:   directives  +  price  +  AccountState{shares, cash}           │
│  Output:  filtered_trades  +  new_AccountState                          │
│                                                                         │
│  NEW: account_engine.py (extracted from account_simulator.py)          │
│    AccountState   → dataclass: shares, avg_cost, cash, total_invested │
│    execute_trade() → pure function: apply ONE directive to account     │
│    filter_trades() → filter today's directives against account         │
│                                                                         │
│  Backtest path:                                                         │
│    account_simulator.py → simulate_equity_curve(df, directives, init)  │
│    Uses execute_trade() in a loop. Computes metrics.                    │
│                                                                         │
│  Live signal path:                                                      │
│    run_strategy.py → filter_trades(today_directives, snap_position)    │
│    Reads position snapshot from logs/positions/.                        │
│    Reports which directives are executable vs. skipped.                 │
│                                                                         │
│  STATEFUL. Position-aware. Is NOT backtest-owned — shared module.       │
│  Answers: "Given my position, CAN I execute what the strategy says?"    │
└───────────────────────────────────────────────────────────────────────┘
```

### Why 3 Layers, Not 2

The v1 proposal merged Layer 2 (strategy rule evaluation) and Layer 3 (position-aware execution) into a single "Call" concept. This was wrong for two reasons:

1. **Look-ahead bias**: If the signal engine knows "I'm already invested" and filters SELLs accordingly, backtests become biased — the engine sees the future position state that the simulator created. In quantitative finance, the alpha model must NOT know portfolio state.

2. **Redundant state**: `account_simulator.py` already tracks position state correctly (cash/shares → SELL is a no-op when shares=0). Building a second state machine in the signal layer duplicates logic and creates sync risk.

3 layers keep concerns cleanly separated: **what is** (statistics) → **what to do** (rules) → **can I do it** (position).

---

## Layer 1: Situation — Pulse Classification

### Design Principle

Pulse answers: "How extreme is this price, statistically?" It is species-independent because:

- LDev measures distance from long-term trend in **standard deviations**. A σ is a σ — it has the same statistical meaning for every asset.
- Species (STEADY/VOLATILE/MOMENTUM/BOND) is a **routing label** that picks which strategy to apply. It has nothing to do with whether +2.0σ is "extreme."
- Species-specific signal thresholds already exist in the old engine's `_classify_row()` — that's where strategy-specific interpretation belongs (Layer 2).

### Pulse Values & Thresholds

| Pulse | Description | LDev Threshold | Z-Score | RSI |
|-------|-------------|----------------|---------|-----|
| `EXTREME_OB` | 极度超买 | > +3.0σ | > +2.0 | — |
| `OVERBOUGHT` | 超买 | +2.0σ ~ +3.0σ | +1.5 ~ +2.0 | > 70 |
| `STRONG` | 偏强 | +1.0σ ~ +2.0σ | +0.5 ~ +1.5 | > 60 |
| `NEUTRAL` | 中性 | -1.0σ ~ +1.0σ | -0.5 ~ +0.5 | 40 ~ 60 |
| `WEAK` | 偏弱 | -2.0σ ~ -1.0σ | -1.5 ~ -0.5 | < 40 |
| `OVERSOLD` | 超卖 | -3.0σ ~ -2.0σ | -2.0 ~ -1.5 | < 30 |
| `EXTREME_OS` | 极度超卖 | < -3.0σ | < -2.0 | — |

Priority: LDev > Z-Score > RSI. If LDev indicates OVERBOUGHT but RSI is 50, the LDev wins. RSI only tips the classification when LDev/Z-Score are borderline.

### Calibration

**Problem with hardcoded σ thresholds:** The initial thresholds (±1.0σ, ±2.0σ, ±3.0σ) assume LDev follows a Gaussian distribution. In reality, asset returns are fat-tailed — a +2.0σ LDev might occur on 5% of trading days (P95, normal) or 20% of days (P80, common for volatile assets). Without calibration, "OVERBOUGHT" fires too often on some assets, too rarely on others.

**Calibration approach — percentile-based thresholds:** Instead of asking "is this LDev > 2.0σ?", ask "is this LDev more extreme than 90% of all historical LDev values?" This anchors each Pulse level to a fixed firing rate:

| Pulse | Target Percentile | Meaning |
|-------|-------------------|---------|
| `EXTREME_OB` | P98+ | Only ~2% of all trading days |
| `OVERBOUGHT` | P90 ~ P98 | Top 10% most extreme |
| `STRONG` | P75 ~ P90 | Above 75th percentile |
| `NEUTRAL` | P25 ~ P75 | Middle 50% of all days |
| `WEAK` | P10 ~ P25 | Below 25th percentile |
| `OVERSOLD` | P2 ~ P10 | Bottom 10% most extreme |
| `EXTREME_OS` | < P2 | Only ~2% of all trading days |

**Calibration process:**

```python
# Step 1: Pool LDev values from all assets across all time, per region
# Step 2: Compute percentile boundaries from the pooled distribution
# Step 3: Map each Pulse level to a specific percentile range
#
# Example output for CN region:
#   P98 LDev = +2.8σ → EXTREME_OB fires above +2.8σ
#   P90 LDev = +1.8σ → OVERBOUGHT fires at +1.8σ ~ +2.8σ
#   P75 LDev = +0.7σ → STRONG fires at +0.7σ ~ +1.8σ
#   ...
#   P2  LDev = -2.5σ → EXTREME_OS fires below -2.5σ
#
# Notice: actual thresholds differ from the ±1/±2/±3 heuristics because
# the empirical distribution is heavy-tailed. The +2.0σ heuristic would
# fire OVERBOUGHT on P85, not P90 — 5% more signals than expected.
```

The calibrated thresholds are stored in `config/pulse_thresholds.yaml`. This allows re-calibration when adding new assets or after major market regime changes.

### Implementation

```python
# situation.py — add at module level
def classify_pulse(ldev: float, zscore: float, rsi: float) -> tuple[str, str]:
    """Species-independent statistical pulse classification.

    Args:
        ldev:   Log deviation from 1250-day OLS trend (in σ)
        zscore: Z-score from expanding normalization
        rsi:    RSI(14) value

    Returns:
        (pulse_type: str, pulse_desc: str)
    """
    # Threshold definitions (species-independent)
    # Configurable via project config; hardcoded as defaults
    if ldev > 3.0 or zscore > 2.0:
        return ("EXTREME_OB", "极度超买")
    if ldev > 2.0 or zscore > 1.5:
        return ("OVERBOUGHT", "超买")
    # ... etc
```

---

## Layer 2: Strategy — Signal Classification

### Design Principle

The current `tactic.py` hardcodes `BUY → BULLISH`, `SELL → BEARISH` via `_verb_to_signal_type()`. This is wrong because:

- `BUY` in a momentum strategy means "follow the trend" — semantically different from `BUY` in a deep-value strategy ("extreme undervaluation")
- `SELL` in a DCA strategy means "take partial profit" — different from `SELL` in momentum ("trend reversal exit")
- A single directive (`BUY 1.0`) should carry different signal meanings depending on which strategy issued it

Each rule in a tactic YAML gets a new `signal` field that describes what that rule **thinks**, independent of what it **does**.

### Signal Names Per Tactic

#### follow (profile: `momentum` + tactic: `follow`)

| Rule ID | Directive | Signal |
|---------|-----------|--------|
| `trend_entry` | BUY 1.0 | `TREND_ENTRY` |
| `major_trend_break` | SELL 1.0 | `TREND_BREAK` |
| `momentum_collapse` | SELL 1.0 | `MOMENTUM_COLLAPSE` |

#### trend (profile: `momentum` + tactic: `trend`)

| Rule ID | Directive | Signal |
|---------|-----------|--------|
| `trend_entry` | BUY 1.0 | `TREND_ENTRY` |
| `major_trend_break` | SELL 1.0 | `TREND_BREAK` |
| `momentum_collapse` | SELL 1.0 | `MOMENTUM_COLLAPSE` |
| `tolerant_exit` | SELL 1.0 | `TOLERANT_EXIT` |

#### volume (profile: `momentum` + tactic: `volume`)

| Rule ID | Directive | Signal |
|---------|-----------|--------|
| `trend_entry_volume` | BUY 1.0 | `VOL_CONFIRMED_ENTRY` |
| `major_trend_break` | SELL 1.0 | `TREND_BREAK` |
| `momentum_collapse` | SELL 1.0 | `MOMENTUM_COLLAPSE` |
| `tolerant_exit` | SELL 1.0 | `TOLERANT_EXIT` |

#### dca (profile: `7s-base` + tactic: `dca`)

| Rule ID | Directive | Signal |
|---------|-----------|--------|
| `bubble_exit` | SELL 0.5 | `BUBBLE_EXIT` |
| `trend_accum` | BUY 0.005 | `TREND_ACCUM` |
| `deep_value` | BUY 0.02 | `DEEP_VALUE_ACCUM` |
| `dip_buy` | BUY 0.01 | `DIP_BUY` |
| `rsi_oversold` | BUY 0.01 | `RSI_OVERSOLD` |

#### deep-value (profile: `7s-base` + tactic: `deep-value`)

| Rule ID | Directive | Signal |
|---------|-----------|--------|
| `extreme_value_entry` | BUY 1.0 | `EXTREME_VALUE_BUY` |
| `deep_value_entry` | BUY 0.5 | `DEEP_VALUE_ENTRY` |
| `overheat_half_exit` | SELL 0.5 | `OVERHEAT_REDUCE` |
| `bubble_exit` | SELL 1.0 | `BUBBLE_FULL_EXIT` |

#### dual-ma-follow (profile: `dual-ma` + tactic: `dual-ma-follow`)

| Rule ID | Directive | Signal |
|---------|-----------|--------|
| `golden_cross` | BUY 1.0 | `GOLDEN_CROSS` |
| `death_cross` | SELL 1.0 | `DEATH_CROSS` |

### Tactic YAML Schema Change

```yaml
# Before (current):
rules:
  - id: trend_entry
    label: Trend Entry
    when:
      - roc: { gt: 0.0 }
      - price_above_ma_200: { eq: true }
    do: { verb: BUY, fraction: 1.0 }
    cooldown: 10

# After (proposed):
rules:
  - id: trend_entry
    label: Trend Entry
    when:
      - roc: { gt: 0.0 }
      - price_above_ma_200: { eq: true }
    do: { verb: BUY, fraction: 1.0 }
    signal: TREND_ENTRY     # ← NEW: strategy-defined signal name
    cooldown: 10
```

Backward compatibility: if `signal` is absent, fall back to the old `_verb_to_signal_type()` mapping (`BUY→BULLISH`, etc.). This keeps the old engine (`engine.py`) working without changes.

### tactic.py Change

```python
# Before:
signal_type = _verb_to_signal_type(verb)  # "BULLISH"

# After:
signal_type = rule.get("signal") or _verb_to_signal_type(verb)  # "TREND_ENTRY" or fallback
```

The `signal_type` column in `eval_df` now carries strategy-defined names instead of generic `BULLISH`/`BEARISH`.

---

## Layer 3: Execution — Position-Aware Filtering

### Design Principle

The execution layer answers: "Given my current position and cash, can I execute what the strategy says?" It is stateful — it knows about shares, cash, and position state (IN/OUT).

**Critical:** Execution is NOT owned by the backtest skill. It is a shared module consumed by both backtest and live signal paths.

### Current State

`account_simulator.py` lives under `skills/backtest/scripts/`. It mixes two concerns:

1. Position bookkeeping (cash, shares, equity) — **general-purpose**
2. Backtest simulation loop (iterate all days, compute metrics) — **backtest-specific**

This creates the false impression that position tracking = backtest.

### Refactoring

```
Before:
  skills/backtest/scripts/account_simulator.py
    ├── simulate_account(df, trades, initial_cash, per_trade_cash)
    │     ├── Build trade_map from trades
    │     ├── Loop over all rows:
    │     │     ├── cash/shares bookkeeping (MIXED IN)
    │     │     ├── Apply trades
    │     │     └── Compute equity
    │     └── Compute metrics
    └── _maxdd(), _metrics_from_series()  (helpers)

After:
  utils/data_service/account_engine.py   ← NEW shared module
    ├── AccountState dataclass
    │     shares, avg_cost, cash, total_invested, is_invested
    ├── execute_trade(account, directive, price) → (AccountState, ExecutionResult)
    │     Pure function. Validates cash/shares. Returns new state + status.
    └── filter_trades(directives, account, price) → (executable, skipped)

  skills/backtest/scripts/account_simulator.py   ← Refactored
    ├── simulate_account(df, directives, initial_cash)
    │     ├── account = AccountState(cash=initial_cash)
    │     ├── Loop over all rows:
    │     │     ├── account, result = execute_trade(account, directive, price)  ← CALL SHARED
    │     │     └── Compute equity
    │     └── Compute metrics
    └── _maxdd(), _metrics_from_series()  (unchanged)

  skills/analyze/scripts/run_strategy.py   ← Live signal consumer
    ├── snap_pos = load_position_snapshot()
    ├── executable, skipped = filter_trades(today_directives, snap_pos, price)
    └── Display: "✅ BUY 1.0 TREND_ENTRY — executable" / "⚠️ SELL 1.0 — skipped (no position)"
```

### No Coupling

```
    Strategy Layer (stateless)          Execution Layer (stateful)
    ┌──────────────────────┐           ┌──────────────────────────┐
    │ tactic.py            │           │ account_engine.py      │
    │ evaluate_tactic()    │───produces──▶│ filter_trades()          │
    │ (no position awareness)│           │ (position-aware)         │
    └──────────────────────┘           └──────────┬───────────────┘
                                                  │
                         ┌────────────────────────┼────────────────────────┐
                         │                        │                        │
                         ▼                        ▼                        ▼
                  Backtest                  Live Signal              Report
             account_simulator.py      run_strategy.py       report_pusher.py
             (simulates full          (filters today's       (displays output)
              historical loop)         directives)
```

- Strategy never imports from `skills/backtest/`.
- Backtest depends on strategy (consumes directives) + position engine (tracks state).
- Live signal depends on strategy (consumes directives) + position engine (tracks state).
- Position engine depends on nothing — it's a pure utility.
- **Zero circular dependencies. Zero coupling between strategy preparation and trade execution.**

---

## Alignment: Pulse × Signal

### Cross-Product Table

| Pulse | Signal Direction | Alignment | Interpretation |
|-------|-----------------|-----------|----------------|
| OVERBOUGHT | Exit signal (SELL-based) | `✅ CONFIRMED` | Statistically hot + strategy says exit — strong exit signal |
| EXTREME_OB | Exit signal | `✅ CONFIRMED` | Extreme statistical heat + strategy exit — clear exit |
| OVERSOLD | Entry signal (BUY-based) | `✅ CONFIRMED` | Statistically cheap + strategy says buy — strong entry signal |
| EXTREME_OS | Entry signal | `✅ CONFIRMED` | Extreme statistical cheapness + strategy buy — clear entry |
| OVERBOUGHT | Entry signal (BUY-based) | `⚠️ DIVERGENT` | Statistically hot but strategy still entering — trend may continue |
| OVERSOLD | Exit signal (SELL-based) | `⚠️ DIVERGENT` | Statistically cheap but strategy exiting — don't catch falling knife |
| NEUTRAL | Any directive | `— NEUTRAL` | No statistical extreme, strategy operating normally |
| STRONG/WEAK | Any directive | `— NEUTRAL` | Mild deviation, strategy's call stands |

Entry signals: anything with `verb=BUY` in the directive.
Exit signals: anything with `verb=SELL` or `verb=CLOSE` in the directive.
`HOLD` is neither — always `NEUTRAL` alignment.

### Implementation

```python
def classify_alignment(pulse: str, directive_verb: str) -> str:
    """Cross-product of Pulse × Directive direction."""
    if pulse in ("EXTREME_OB", "OVERBOUGHT"):
        if directive_verb in ("SELL", "CLOSE"):
            return "CONFIRMED"
        if directive_verb == "BUY":
            return "DIVERGENT"
    if pulse in ("EXTREME_OS", "OVERSOLD"):
        if directive_verb == "BUY":
            return "CONFIRMED"
        if directive_verb in ("SELL", "CLOSE"):
            return "DIVERGENT"
    return "NEUTRAL"
```

---

## Output Format

### Current (Single Column)

```
Signal
─────────────────────────────
[NEUTRAL] Observing | PE 77%ile Div 75%ile
[BEARISH] Major Trend Break
```

Mixes: statistical context (PE/Div %ile) + strategy signal (NEUTRAL/BEARISH) + rule label (Observing / Major Trend Break) → one unstructured string.

### Proposed (Columnar)

```
─── Signal (2026-05-30) / CN ──────────────────────────────────────────────────
Symbol  Name       Species   Strategy   Pulse       Signal             Dir  Wgt  Alignment
159259  Growth100  VOLATILE  momentum+trend    OVERBOUGHT  TREND_ENTRY        BUY  1.0  ⚠️ DIVERGENT
510050  SSE50      STEADY    7s-base+dca       OVERSOLD    DEEP_VALUE_ACCUM   BUY  0.02 ✅ CONFIRMED
511880  CNBond     BOND      7s-base+dca       EXTREME_OS  EXTREME_VALUE_BUY  BUY  1.0  ✅ CONFIRMED
159952  ChiNext    VOLATILE  dual-ma+dual-ma-follow STRONG —                  HOLD —    —
512100  CSI1000    STEADY    7s-base+dca       NEUTRAL     GOLDEN_CROSS       BUY  1.0  NEUTRAL
────────────────────────────────────────────────────────────────────────────────
```

Each column answers one question:

| Column | Question |
|--------|----------|
| Symbol | Which asset? |
| Species | What type? |
| Strategy | Which engine produced this signal? |
| Pulse | Is the price statistically extreme? |
| Signal | What does the strategy think? |
| Dir | What does the strategy say to do? |
| Wgt | How much? (fraction) |
| Alignment | Do Pulse and Signal agree? |

---

## Implementation Plan

### Phase 1: Pulse — No Schema Changes (3 files, ~40 lines)

| File | Change | Lines |
|------|--------|-------|
| `situation.py` | Add `classify_pulse(ldev, zscore, rsi) → (type, desc)` | +20 |
| `signal_computer.py` | Call `classify_pulse` per row, add `pulse_type` column | +5 |
| `pipeline.py` | Propagate `pulse_type` to output dict | +5 |
| `run_strategy.py` | Display `Pulse` column in signal table | +10 |

**Zero breaking changes.** Pulse is a new column. Old Signal column unchanged.

### Phase 2: Strategy-Defined Signals (8 files, ~30 lines)

| File | Change | Lines |
|------|--------|-------|
| `follow.yaml` | Add `signal` field to each rule | +3 |
| `trend.yaml` | Add `signal` field to each rule | +4 |
| `volume.yaml` | Add `signal` field to each rule | +4 |
| `dca.yaml` | Add `signal` field to each rule | +5 |
| `deep-value.yaml` | Add `signal` field to each rule | +4 |
| `dual-ma-follow.yaml` | Add `signal` field to each rule | +2 |
| `tactic.py` | Read `signal` from rule, fallback to `_verb_to_signal_type` | +5 |
| `registry.py` | Validate `signal` field (optional, string) | +3 |

**Backward compatible.** Missing `signal` field → old behavior. Old engine (`engine.py`) unaffected.

### Phase 3: Position Engine Extraction (~100 lines)

| File | Change | Lines |
|------|--------|-------|
| NEW: `utils/data_service/account_engine.py` | `AccountState`, `execute_trade()`, `filter_trades()` | +60 |
| `account_simulator.py` | Refactor loop to use `execute_trade()` | +20/-30 (net -10) |
| `run_strategy.py` | Live mode: load snapshot, call `filter_trades()` | +15 |

**Backward compatible.** `account_simulator.py` output unchanged. New module is additive.

### Phase 4: Alignment + Unified Output (~50 lines)

| File | Change | Lines |
|------|--------|-------|
| NEW: `alignment.py` | `classify_alignment(pulse, directive_verb) → str` | +20 |
| `run_strategy.py` | Compute alignment, update output table | +15 |
| `_save_snapshot()` | Add `pulse`, `strategy`, `signal`, `alignment` to JSON | +10 |
| `report_pusher.py` | Display new columns (non-breaking) | +5 |

### Total: ~220 lines across 14 files. Zero breaking changes.

---

## Open Questions

1. **Pulse threshold calibration**: Current thresholds are initial heuristics. Should we calibrate against historical LDev distributions before shipping, or ship with heuristics and tune later?

2. **Old engine migration**: `engine.py` serves BOND and backtest paths. Should it also get Pulse, or remain as-is? Proposal: leave engine.py unchanged — it computes its own signal types. Only add Pulse to `signal_computer.py` output (which both paths share).

3. **Position snapshot integration**: Live signal reads position from `logs/positions/`. What if no snapshot exists? Proposal: default to OUT (conservative) and show all BUY directives as executable, all SELL directives as skipped.

4. **Backtest output**: Should backtest charts/results also show Pulse and Alignment columns? Proposal: yes, but Phase 4+ — add columns to backtest equity_df without changing metrics.

---

## References

- Conversation: Feishu group chat 2026-05-31, Kirk Xu × 7S
- Previous proposal (v1): This file, overwritten 2026-05-31
- Routing: `config/strategies/routing.yaml`
- Profile configs: `config/strategies/profiles/*.yaml`
- Tactic configs: `config/strategies/tactics/*.yaml`
- S2 Situation: `skills/analyze/scripts/situation.py`
- S4 Strategy (new): `skills/analyze/scripts/s4_strategy/pipeline.py`, `tactic.py`
- S4 Strategy (old): `skills/analyze/scripts/s4_strategy/engine.py` (BOND/backtest)
- Account: `skills/backtest/scripts/account_simulator.py`, `utils/data_service/account_engine.py`
- Backtest: `skills/backtest/scripts/run_backtest.py`
- Output: `skills/analyze/scripts/run_strategy.py`
- Reporting: `skills/view_report/scripts/report_pusher.py`
- Position snapshots: `logs/positions/`
