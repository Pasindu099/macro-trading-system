# Config files

This folder contains YAML configuration that drives the app's behavior at runtime.
Changes here do NOT require code changes — just restart the app (or re-run the
relevant seed script).

## Files

### `countries.yaml`
Static reference data for the 8 tracked countries. Loaded into the `countries`
table by `scripts/seed_countries.py`.

**To change:** Edit the file, then run:
```
python scripts/seed_countries.py
```
Seed is idempotent — safe to run multiple times.

### `indicator_mapping.yaml`
Maps raw EODHD event types to canonical indicators. The canonicalizer reads
this file at app startup.

**To add a new indicator:**
1. Find the raw EODHD event type (in admin dashboard unmapped list, or logs)
2. Add an entry following the structure in the file header
3. Restart the ingestion service
4. Historical unmapped events won't be backfilled automatically — run
   `scripts/remap_unmapped.py` (Step 2 Section 5) to reprocess them.

**Rules:**
- `canonical_name` is immutable once used. Never rename.
- Multiple raw EODHD types can map to the same canonical_name (aliases).
- Matching is case-sensitive on `eodhd_type`.
- `eodhd_comparison: null` matches events where EODHD's comparison field is null.

## Conventions

**Categories (exactly 8):**
- Inflation, Growth, Labor, Monetary Policy, Trade, Sentiment, Housing, Other

**Frequencies:**
- daily, weekly, monthly, quarterly, irregular

**Importance:**
- 1 = top-tier (CPI, NFP, GDP, rate decisions)
- 2 = notable (sub-indicators, leading indicators, regional surveys)
- 3 = minor (weekly data, auxiliary series)

**is_higher_better_for_currency:**
- true (default): higher reading → stronger currency (e.g. GDP, NFP, CPI when near target)
- false: higher reading → weaker currency (e.g. unemployment, jobless claims)
- This is only a default for UI color-coding; not a scoring rule yet (that's Phase 2).