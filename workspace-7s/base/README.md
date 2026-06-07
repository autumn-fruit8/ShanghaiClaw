# Base Layer

Shared base classes for the 7S data pipeline.

| File | Purpose |
|------|---------|
| `calibration.py` | Historical data calibration and repair |
| `daily_update.py` | Daily data ingestion base class (CN + US) |
| `service_base.py` | Common service base class (logging, error handling, config loading) |

Skills inherit from these classes rather than reimplementing common patterns.
