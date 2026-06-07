# Data Access Objects (DAO)

Persistence layer for workspace-7s. Each DAO maps to a specific data domain and storage location.

## Files

| File | Domain | Storage |
|------|--------|---------|
| `models.py` | Domain models (Plan, Position, PositionSnapshot, AssetCatalog, etc.) | In-memory |
| `config_dao.py` | Static/semi-static config (asset-master, plans, states, engine.yaml) | `config/` |
| `position_dao.py` | Position snapshots per plan per date | `logs/positions/` |
| `decision_dao.py` | Decision outputs | `logs/decisions/` |
| `review_dao.py` | Review outputs | `logs/review/` |
| `asset_dao.py` | Asset metadata and catalog queries | `config/assets/` + in-memory index |
| `state_dao.py` | Business state queries (void/watchlist/active) | `config/states/` |
| `holdings_dao.py` | Holdings data from broker/CSV sources | `logs/holdings/` |

## Calling convention

DAOs are **read by domain scripts** (`skills/*/scripts/`) and **written by their owning skill**. No cross-skill writes.

| Data | Written By | Read By |
|------|-----------|---------|
| `config/plans/` | decide | decide, update_position, review_plan |
| `config/states/` | human | decide, analyze |
| `logs/positions/` | update_position | decide |
| `logs/decisions/` | decide | — |
| `logs/review/` | review_plan | — |
