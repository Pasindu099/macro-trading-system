# Step 13 newswire follow-up decisions

1. Geopolitics without a G10 mention or active situation: store and tag `Geopolitics`, `no_fx_link=true`, `Low`; hide from the default feed behind a “Show geopolitics without FX link” filter. Auto-promote when linked to an active situation or the tier-1 shock rule fires. Re-run the filter on existing rows and report new kept/dropped counts.
2. Keep Al Jazeera for geopolitics; `licensed_for_redistribution: false`.
3. Update `test_event_log.py` to expect the retained `8.25` precision from commit `9e54f16`. The full suite must be green.
4. Run `scripts/news_parity.py --hours 48` when 48 hours of overlap exist, locally or after deployment. Keep old collectors on until then.

Continue Step 13 Parts D–F in `DECISIONS_STEP13.md`; stop after F with a status of at most 10 lines.
