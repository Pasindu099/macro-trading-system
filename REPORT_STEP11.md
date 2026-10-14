# Step 11 — Calendar, event panel, Event Log (complete, local only)

| Part | Result | Commit |
|---|---|---|
| A — data service | Day-bounded ranges, one row per release, impact overrides, CN calendar ingestion | `e3fafcb` |
| A — CN exclusion | `calendar_only` countries filtered from Macro State sources | `19a80a9` |
| A — follow-up | CN trade variants mapped; "China trade balance" calendar bundle | `528bac5` |
| B — fast actuals | 60 s polling for 10 min after High releases | `7eab218` |
| C — calendar list | Presets, filters, UTC/local, sticky days, progressive loading | `9e63a46` |
| D — event panel | Header, why-it-matters, History/Components/Reactions, nightly stats | `c147cf4` |
| E — Event Log | `/calendar/event-log` CRUD, raw vs final AI note | `54072de` |
| F — visuals | Tokens, colour rules, sheet layout, bundle headlines | `bb4f949` |

## Verification (in-container)
| Check | Result |
|---|---|
| Full suite (final) | 603 passed, 7 skipped; no pre-existing failures |
| CN mapped / unmapped | Before follow-up: 9 / 60. After: 20 / 51 (10 trade rows reclassified) |
| Top remaining unmapped CN | Prime Rate 3, M2 2, Industrial Profits 2, FDI 2, House Price Index 2, FAI 2, FX Reserves 2, Inflation Rate (mom) 2, LPR 1Y/5Y 2 each |
| CN in Macro State | 0 CN/CNY rows in metadata, observations, features, themes, stance, CB score after rebuild |
| `/calendar` | Default today; last 30 days High: 30 days, 105 rows, 63 surprise chips |
| Reaction stats job | 1,206 indicator×pair rows; 1,111 with ≥ 8 releases; about 7 s |
| Event Log live | Create (linked NFP, 3 auto moves), edit, delete (400 without confirm); AI call returned OpenAI 429 "no credits", which was shown and the form stayed usable |
| Playwright 1440 / 390 px | No horizontal overflow on calendar, sheet, log or form; sheet fills viewport; no JS errors |

## Where
- CN trade bundle: `config/calendar_overrides.yaml` (`calendar_bundles`), `app/services/calendar_service.py:19`; mappings in `config/indicator_mapping.yaml:51`.
- Bundle headline = top-weighted Event Innovation member: `app/services/calendar_service.py:22`.
- Panel: `app/services/calendar_detail.py:107`, route `app/api/routes/calendar.py` (`/calendar/event/{release_id}`), template `app/web/templates/calendar/detail.html`.
- Reactions: `app/services/event_reactions.py:55` (signed pips), `:62` (stats), `:96` (job); pairs in `config/pairs.yaml` (`event_reaction_pairs`); 00:20 UTC job `app/ingestion/scheduler.py:196`; migration 0030.
- Event Log: routes `app/api/routes/event_log.py:64`; service `app/services/event_reaction_log.py:497`; AI `:242`; migration 0031; admin alias `app/db/models.py:829`.

## Decisions taken
- Move = close on the release's UTC date vs the prior available close. Positive = event currency up; CNY reads as the pair base (AUD, NZD).
- Hit rate counts |σ| ≥ 0.5 only, and a zero move is not a hit. Beat/miss uses |σ| > 1. Stats are hidden below 8 releases.
- CN has no Event Innovation σ, so CN beat/miss/hit stats stay empty; only avg |move| shows once there are ≥ 8 releases.
- `ai_note_raw` is written once. "Generate AI note" is hidden once a raw note exists, and a second call is refused. Edits change only `ai_note_final`.
- `ai_interpretation` was dropped as a column. The admin API reads and writes it as an alias of `ai_note_final`, so its response shape and 502 behaviour are unchanged.
- Event Log writes need the analyst role. `/event-log` now redirects (307) to `/calendar/event-log`.
- Amber/blue are reserved for surprises: impact labels and history bars are now neutral. UI chrome (nav, buttons) keeps the site accent.
- Bundle rows now take their values from the headline member (e.g. payrolls), not the largest-|σ| member, so the list and panel agree.

## Open items
- CN countries row needed `scripts.seed_countries` (FK failure otherwise). Production deploy must run the seed before CN ingestion.
- `/data` and `/pairs` are still placeholder pages; the panel links pass `?indicator=` / `?pair=` for when they exist.
- Production untouched. Deploy needs migrations 0030–0031, the seed, CN ingest + `reclassify_unmapped --country CN`, then one stats job run.
