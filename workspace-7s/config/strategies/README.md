# Strategy Configuration

## Structure

```
config/
├── routing/
│   └── strategy_routing.yaml     ← species/symbol → {profile, tactic}
└── strategies/
    ├── profiles/                 ← What indicators to compute
    │   ├── 7s-base.yaml          → ldev, rsi, zscore, price_above_ma[60]
    │   ├── momentum.yaml         → rsi, roc, slope, adx, sma[200], vol_ratio, price_above_ma[60,200]
    │   └── dual-ma.yaml          → sma[100,250], ma_cross, price_above_ma[100,250], roc
    └── tactics/                  ← What rules to evaluate
        ├── dca.yaml              → gradual accumulation (0.005~0.5 fractions)
        ├── deep-value.yaml       → all-in at extreme lows, all-out at peaks (0.5~1.0)
        ├── follow.yaml           → trend following (BUY/SELL 1.0)
        ├── trend.yaml            → trend + tolerant exit (-5% threshold)
        ├── volume.yaml           → trend + volume-confirmed entry
        └── dual-ma-follow.yaml   → golden cross / death cross
```

## How Routing Works

`strategy_routing.yaml` maps every asset to a `(profile, tactic)` pair:

```yaml
# Species-level defaults:
species_defaults:
  STEADY:    { profile: 7s-base,    tactic: dca }
  BOND:      { profile: 7s-base,    tactic: dca }
  VOLATILE:  { profile: 7s-base,    tactic: deep-value }
  MOMENTUM:  { profile: momentum,   tactic: follow }

# Per-symbol overrides (take priority over species):
symbols:
  - symbol: "159259"   → { profile: momentum, tactic: trend }
  - symbol: "159269"   → { profile: momentum, tactic: trend }
  - symbol: TLT        → { profile: momentum, tactic: trend }
  - symbol: "159952"   → { profile: dual-ma,  tactic: dual-ma-follow }
  - symbol: "510170"   → { profile: 7s-base,  tactic: deep-value }
  - symbol: "512400"   → { profile: 7s-base,  tactic: deep-value }

# Shared params:
params:
  initial_cash: 100000.0
```

Resolution order: **symbol override** > **species default** > **fallback**.

## Profile — What Indicators

A profile declares which columns `signal_computer.py` adds to the DataFrame before tactic evaluation. All profiles implicitly include `ldev`, `zscore`, `rsi` (computed by `situation.py`).

### 7s-base (default for STEADY, BOND, VOLATILE)

| Indicator | Config | Notes |
|---|---|---|
| `ldev` | window: 252 | Log deviation from 1250-day OLS trend |
| `zscore` | window: 252 | Expanding Z-score normalization |
| `rsi` | window: 14 | RSI(14) |
| `price_above_ma` | [60] | Boolean: `price >= sma_60` |

### momentum (for MOMENTUM species, trend-momentum assets)

| Indicator | Config | Notes |
|---|---|---|
| `ldev`, `zscore`, `rsi` | (implicit) | Always available |
| `sma` | [200] | MA200 |
| `price_above_ma` | [60, 200] | Boolean flags |
| `roc` | window: 20 | 20-day rate of change (%) |
| `slope` | window: 20 | OLS log slope (annualized) |
| `adx` | window: 14 | Average Directional Index (simplified) |
| `vol_ratio` | window: 20 | Volume / 20-day MA volume |

### dual-ma (for dual-MA crossover assets)

| Indicator | Config | Notes |
|---|---|---|
| `sma` | [100, 250] | MA100, MA250 |
| `ma_cross` | fast: 100, slow: 250 | 1=golden cross, -1=death cross, 0=none |
| `price_above_ma` | [100, 250] | Boolean flags |
| `roc` | window: 20 | 20-day rate of change (%) |

## Tactic — What Rules

A tactic is an ordered list of rules evaluated per row by `tactic.py`. The first matching rule fires. Each rule has a `signal` field (strategy-defined classification).

### dca (gradual accumulation)

| Rule | When | Action | Signal |
|---|---|---|---|
| `bubble_exit` | ldev > 3.0σ | SELL 0.5 | `BUBBLE_EXIT` |
| `trend_accum` | ldev < 0.5σ, price > MA60 | BUY 0.005 | `TREND_ACCUM` |
| `deep_value` | ldev < -1.5σ | BUY 0.02 | `DEEP_VALUE_ACCUM` |
| `dip_buy` | -1.5 ≤ ldev < 1.0, price < MA60 | BUY 0.01 | `DIP_BUY` |
| `rsi_oversold` | ldev < 1.0, rsi < 35 | BUY 0.01 | `RSI_OVERSOLD` |

Fraction semantics: BUY fraction = % of available cash. SELL fraction = % of current shares.

### deep-value (all-in / all-out)

| Rule | When | Action | Signal |
|---|---|---|---|
| `extreme_value_entry` | ldev < -2.0σ | BUY 1.0 | `EXTREME_VALUE_BUY` |
| `deep_value_entry` | -2.0 ≤ ldev < -1.5 | BUY 0.5 | `DEEP_VALUE_ENTRY` |
| `overheat_half_exit` | 2.5 ≤ ldev ≤ 3.0 | SELL 0.5 | `OVERHEAT_REDUCE` |
| `bubble_exit` | ldev > 3.0σ | SELL 1.0 | `BUBBLE_FULL_EXIT` |

### follow (trend following)

| Rule | When | Action | Signal |
|---|---|---|---|
| `major_trend_break` | price < MA60 AND price < MA200 | SELL 1.0 | `TREND_BREAK` |
| `momentum_collapse` | roc < -12% | SELL 1.0 | `MOMENTUM_COLLAPSE` |
| `trend_entry` | roc > 0%, price ≥ MA200 | BUY 1.0 | `TREND_ENTRY` |

### trend (trend + tolerant exit)

Same as follow, plus:

| Rule | When | Action | Signal |
|---|---|---|---|
| `tolerant_exit` | roc < -5% | SELL 1.0 | `TOLERANT_EXIT` |

### volume (trend + volume-confirmed entry)

Same as follow, plus:

| Rule | When | Action | Signal |
|---|---|---|---|
| `trend_entry_volume` | roc > 0%, price ≥ MA200, vol_ratio ≥ 1.2 | BUY 1.0 | `VOL_CONFIRMED_ENTRY` |
| `tolerant_exit` | roc < -3% | SELL 1.0 | `TOLERANT_EXIT` |

Requires `momentum` profile (which computes `vol_ratio`).

### dual-ma-follow (golden/death cross)

| Rule | When | Action | Signal |
|---|---|---|---|
| `golden_cross` | MA100 crosses above MA250 | BUY 1.0 | `GOLDEN_CROSS` |
| `death_cross` | MA100 crosses below MA250 | SELL 1.0 | `DEATH_CROSS` |

Requires `dual-ma` profile.

## Adding a New Strategy

1. Create the profile YAML (`profiles/my-profile.yaml`) defining indicators to compute
2. Create the tactic YAML (`tactics/my-tactic.yaml`) defining ordered rules with signals
3. Add routing entry in `config/strategies/routing.yaml`:
   ```yaml
   species_defaults:
     MY_SPECIES: { profile: my-profile, tactic: my-tactic }
   ```
   Or per-symbol:
   ```yaml
   symbols:
     - symbol: "123456"
       profile: my-profile
       tactic: my-tactic
   ```

No strategy YAML file needed. Routing is the single source of truth.

## S3 Macro Indicators

`yield_pctile` (bond yield percentile) and `vix_pctile` (VIX percentile) are injected as data columns by `s3_context.py` before tactic evaluation. Any tactic rule can reference them in `when:` conditions:

```yaml
- id: safe_entry
  when:
    - roc: { gt: 0 }
    - vix_pctile: { lt: 80 }
  do: { verb: BUY, fraction: 1.0 }
  signal: SAFE_ENTRY
```

## Registry Validation

`StrategyRegistry` validates every profile-tactic pair at load time:
- Every `when:` indicator must exist as a column produced by the profile
- Every `verb` must be BUY/SELL/CLOSE/HOLD
- Every SELL/CLOSE fraction must be in [0, 1]
- Every BUY fraction must be ≥ 0
