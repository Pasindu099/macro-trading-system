-- Step 8 Part 0: top 40 unmapped G10 EODHD event types by stored count (read-only).
SELECT raw_payload->>'country' AS country, raw_payload->>'type' AS event_type,
       coalesce(raw_payload->>'comparison', '-') AS comparison, count(*) AS n
FROM indicator_releases
WHERE indicator_id IS NULL
GROUP BY 1, 2, 3
ORDER BY n DESC
LIMIT 40;
