# Step 4 Part I: source availability

Availability checked against EODHD GBOND and FOREX exchange symbol listings on 2026-10-03 (2 listing calls). GB/CH use provider prefixes UK/SW.

| Country | 2Y | 10Y | 20Y | 30Y |
| --- | --- | --- | --- | --- |
| US | yes | yes | yes | yes |
| DE | yes | yes | no | yes |
| FR | yes | yes | no | no |
| GB | yes | yes | no | yes |
| JP | yes | yes | no | yes |
| AU | yes | yes | no | yes |
| NZ | yes | yes | no | no |
| CA | yes | yes | yes | yes |
| CH | yes | yes | no | no |

- Available new yield symbols: FR2Y, FR10Y, US30Y, DE30Y, UK30Y, JP30Y, AU30Y, CA30Y. NZ, CH and FR have no 30Y or 20Y symbol in the listing.
- All 14 missing pair symbols are listed: EUR/AUD, EUR/NZD, EUR/CAD, GBP/AUD, GBP/NZD, GBP/CAD, GBP/CHF, AUD/CAD, AUD/CHF, NZD/CAD, NZD/CHF, NZD/JPY, CAD/CHF, CHF/JPY. Direct EODHD ingestion is indicated for all; synthetic derivation is not indicated by symbol availability.
- Source lookup command: `docker compose exec app python -m scripts.check_step4_sources`.

## Backfill and verification

- Start date: 2023-08-21, matching the existing backfill history. End date: 2026-10-03. Existing checkpoint files were reused; dry-runs did not advance them.
- Yield backfill: 20 requests, 16,190 rows seen, 6,851 inserted, 0 missing. FX backfill: 14 requests, 12,493 rows seen and inserted, 0 errors, 0 synthetic pairs.
- Part I commands used 54 EODHD calls: 6 symbol-listing calls and 48 history calls, including 14 FX dry-run history calls. This excludes unrelated application traffic and remains below 50,000 calls/day.
- `job:rates_derived` after backfill: 0 new yield flags, 0 FX flags, 60,476 spread rows.
- 10s30s is available for US, DE, UK, JP, AU, CA; unavailable with a reason for NZ and CH. FR−DE 2Y and 10Y are available; FR−DE 30Y remains unavailable. Drivers now return values for all 14 newly sourced FX pairs at both 2Y and 10Y.
- Configured 30Y tenors now match the six sourced countries; FR, NZ and CH remain explicitly unavailable because neither 30Y nor 20Y is listed.
