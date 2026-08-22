# Macro Dashboard

A macro economic dashboard for FX trading.

## Knowledge Bank

The durable market-research corpus foundation lives under `app/knowledge`.

```powershell
alembic upgrade head
python -m app.knowledge.ingestion scan
```

Then open `/knowledge-bank` in the dashboard. More detail is in `docs/knowledge_bank.md`.
