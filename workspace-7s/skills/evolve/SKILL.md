---
name: Evolve
description: "Use when conducting a monthly retrospective review of 7S runs. This is the S7 review layer — a human-led review process, not an automated engine. Do NOT use for live trigger judgment, position decisions, or profile changes."
---

# Evolve Skill

The evolve loop is a **human-led periodic review**, not an automated skill engine.

**Non-negotiable**: Evolve proposals are not policy until explicitly adopted by the human.
Automated retrospective analysis is not the goal — disciplined human review is.

## Human Triggers

**Keyword**: `evolve`

**When to trigger**: Human wants to conduct a periodic S7 review — monthly retrospective or after significant market events.

**Natural Language** (exclusive territory — rare, human-led only):

| Intent | What to say |
|--------|-------------|
| Monthly review | *"开始 S7 进化"* / *"月度复盘"* / *"进化 review"* |
| Post-event review | *"市场大跌后做一次进化回顾"* |
| Ad-hoc retrospective | *"复盘一下这阶段的表现"* |
| Check proposals | *"看看有什么进化建议"* |

**Cross-skill routing**:
- Want **live signals**? → use `analyze`: *"今天有什么信号"*
- Want **historical metrics**? → use `review`: *"看看历史业绩"*
- Want **drift decision**? → use `decide`: *"要不要调仓"*
- `evolve` is NOT for live judgment, position decisions, or profile changes

## Lightweight Process

1. (Optional) Run monthly backtest: *"跑一下月度回测"* → generates `logs/backtest/monthly/` with trend comparison
2. Human reviews `logs/positions/` and position evidence from the previous period
3. Human reviews backtest trend warnings (if any) as S7 input
4. Human fills out review template (maintained outside this workspace)
5. Adopted proposals are moved to `MEMORY.md`
6. Open questions are resolved before the next proposal is made

## When to Trigger

- Monthly: First Sunday of each month
- After a significant market event that affected portfolio outcomes
- After a pipeline failure or data quality issue

## SKILL.md vs Template

This SKILL.md documents the review process. The actual review work is recorded in:

- `MEMORY.md` — where adopted proposals are recorded

## What Is NOT Evolve

- NOT a market performance analysis tool
- NOT a live trigger or position decision layer
- NOT an automated lesson extraction engine
- NOT a way to silently change state without human approval

## Output Contract

A completed review session produces:

- Updated `MEMORY.md` if any proposals were adopted
- No changes to `config/state_db/`, `config/asset-master.json`, or any position plan

## State Isolation

Evolve MUST NOT write to:
- `config/state_db/` — lifecycle state is human-owned
- `config/asset-master.json` — asset identity is canonical, not mutable by review
- `config/plans/<name>/v<ver>.json` — position plans change only via explicit human approval

## Error Handling

- No snapshots in review period → note that in template as "no evidence", do not fabricate observations
- Conflicting outcomes across runs → document the conflict, don't collapse it into one conclusion
- Missing baseline for comparison → note as "first review" or "insufficient history"
