# Configuration — workspace-7s

## Structure

```
config/
├── active-regions.json     # Region enablement & scheduling (CN/US)
├── engine.yaml             # Core runtime settings (paths, API keys, logging)
├── assets/
│   ├── asset-master.json       # Canonical asset registry (all known symbols)
│   └── asset-master.schema.json
├── states/
│   ├── active.json             # Currently held assets
│   ├── watchlist.json          # Assets under observation
│   └── void.json               # Retired/rejected assets
├── plans/                      # Named stake plans by region
│   ├── cn_anti_lost_30/
│   ├── cn_bond/
│   ├── cn_hb/
│   ├── us_anti_inflation/
│   ├── us_bond/
│   └── us_hb/
├── strategies/                 # Strategy definitions (YAML)
│   ├── dca-7s.yaml
│   ├── deep-value-7s.yaml
│   ├── momentum-follow.yaml
│   ├── profiles/               # Strategy profile overrides
│   └── tactics/                # Execution tactics
└── symbol_resolution/          # Symbol type detection & data source routing
    └── README.md
```

## Data flow

```
User input (symbol/region)
    → symbol_resolution/  (classify & route)
    → assets/asset-master.json  (resolve metadata)
    → states/  (check business state)
    → strategies/  (select strategy)
    → plans/  (apply allocation plan)
```

## Ownership

- **Human-managed**: `states/`, `plans/`, `active-regions.json`
- **Agent-managed**: `strategies/`, `symbol_resolution/`
- **Admin-managed**: `assets/asset-master.json`, `engine.yaml`
