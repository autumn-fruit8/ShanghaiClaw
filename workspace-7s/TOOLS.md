# TOOLS.md - 7S Skill Routing

## Skills Reference

| Skill | Keyword | What It Does | NL Territory | SKILL.md |
|-------|---------|--------------|--------------|----------|
| **Analyze** | `analyze` | S1-S4 signal pipeline: Species → Situation → Pulse → Strategy → Alignment. Profile+tactic routing from `strategy_routing.yaml`. Output: Pulse + Signal + Alignment columns. | 分析, 看看, 有什么信号 | `skills/analyze/SKILL.md` |
| **Backtest** | `backtest` | 10-year backtest simulation (single symbol or region, active-only). Combined PNG for singles, universe report + per-asset charts for region | 回测, 模拟, 策略表现 | `skills/backtest/SKILL.md` |
| **Momentum** | `momentum` | Momentum rotation — rank assets, select Top-N, BUY/HOLD/SELL signal + bar chart + decision text | 动量, 动量轮动, 动量排名 | `skills/momentum/SKILL.md` |
| **Decide** | `decide` | S5 (plan) + S6 (drift) | 调仓, 漂移, 新建/更新计划 | `skills/decide/SKILL.md` |
| **Position** | `position` | Refresh prices, apply trades | 刷新, 导出, 执行, 核对 | `skills/update_position/SKILL.md` |
| **Report** | `report` | Feishu delivery (MD + PNG) | 日报, 周报, 推送 | `skills/view_report/SKILL.md` |
| **Review** | `review` | Historical performance + metrics | 历史, 收益, 复盘 | `skills/review_plan/SKILL.md` |
| **Evolve** | `evolve` | S7 human-led review | 月度, 反思, 策略调整 | `skills/evolve/SKILL.md` |
| **Logarithm** | `log` | 全收益对数坐标图 + CAGR 趋势线 + 回撤面板 | 对数图, 走势, CAGR, 回撤 | `skills/logarithm/SKILL.md` |
| **Momentum** | `momentum` | 动量轮动 — rank assets by momentum, dual-threshold BUY/HOLD/SELL signals, multi-period composite, rotation simulation with equity curve | 动量, 动量轮动, 动量排名, 哪个强 | `skills/momentum/SKILL.md` |

**Routing**: Keyword match first → read SKILL.md → execute. Ambiguous → ask human.

---

## Data Architecture (2026-05-22)

### Two-Column CSV Contract
Every CSV in `knowledge/{region}/3_processed/{symbol}.csv`:
```
date,total_return,close
```
- **`total_return`**: signal layer only (LDev, RSI, Z-Score, CAGR, log chart). NEVER use `close`.
- **`close`**: position pricing only (current_price, market_value, apply_trades). NEVER enters signal computation.
- Never cross the streams.

### Data Source Priority (CN region)

#### CN_ETF (exchange-traded ETFs)
1. **CSI TR API** (中证全收益): `stock_zh_index_hist_csindex(tr_code, start_date, end_date)` — total_return
2. **Sina Finance**: `fund_etf_hist_sina(symbol)` — close (fallback total_return if CSI fails)
3. **CNI** (国证 48xxxx): no CSI TR → always Sina chain
- ⚠️ CSI TR call MUST include `start_date`/`end_date` params. Without them, akshare returns stale ~2024 data.

#### CN_OTC (OTC funds / ETF联接基金)
- **Non-equity OTC** (bond/gold): 天天基金 only
  ```
  fund_open_fund_info_em(symbol, 累计净值走势) → total_return
  fund_open_fund_info_em(symbol, 单位净值走势) → close
  ```
- **Equity OTC with CSI TR**: hybrid
  ```
  stock_zh_index_hist_csindex(tr_code, start_date, end_date) → total_return (CSI TR)
  fund_open_fund_info_em(symbol, 单位净值走势) → close (fund unit NAV)
  ```
- **Equity OTC without CSI TR** (e.g. 国证 399xxx): fallback to 天天基金 cum_nav

#### Asset Type Pattern Reference
| Pattern | Type | Data Path |
|---------|------|-----------|
| `159xxx`, `51xxxx`, `56xxxx`, `58xxxx` | CN_ETF | CSI TR + Sina |
| `003xxx`-`009xxx`, `01xxxx`-`02xxxx` | CN_OTC | 天天基金 or CSI TR hybrid |
| `000218` (黄金ETF联接) | CN_OTC | 天天基金 cum_nav |
| `000xxx` (other), `399xxx`, `93xxxx` | CN_INDEX | CSI price index |
| `[A-Z]+` | US_ETF | yfinance |
| `xxxx.HK` | HK_ETF | yfinance |

### Bond Routing

- Short/medium bonds: species `BOND` → route to `dca-7s` (B&H, no timing)
- Long-duration bonds (TLT, 30Y CGB): reclassified as `VOLATILE` → routed to momentum strategies
- Yield percentile (`yield_pctile`) injected as S3 data column — any tactic YAML can reference it in rules
- See: `config/strategies/routing.yaml`, `docs/SIGNAL_ARCHITECTURE.md`

### Log Chart CLI (simplified)
```
log SYMBOL                  10Y 全局 LDev
log SYMBOL --rolling        10Y 图表 + 5Y 滚动 LDev
log SYMBOL --rolling 3      10Y 图表 + 3Y 滚动 LDev
log SYMBOL --years 5        5Y 图表 + 5Y 全局 LDev
```

### CSV Resolution Tiers (adhoc analyze)
```
Active symbols → knowledge/{region}/3_processed/
Non-active cached → adhoc/cache/
First-time → bootstrap from CSI TR or Sina → write to adhoc/cache/
```
No unnecessary API calls: active assets read from `knowledge/` directly.

---

## Cron Schedule (7S only — 3 jobs)

| Time | Job ID | Action |
|------|--------|--------|
| 4:00 AM 北京 Tue–Sat | `7s-daily-analyze-cn-active` | Daily update → analyze CN active → report |
| 6:00 AM 北京 Tue–Sat | `7s-daily-analyze-us-active` | Daily update → analyze US active → report |
| 6:15 AM 北京 Tue–Sat | `7s-update-position` | `update_position refresh` |

Full config → `~/.openclaw/cron/jobs.json`.

## ⚠️ Mandatory Rules

- Read SKILL.md first — never construct CLI args from memory
- Skill scripts are private — human never calls `skills/*/scripts/*.py` directly
- 7S never auto-trades — explicit human approval required for every position change
