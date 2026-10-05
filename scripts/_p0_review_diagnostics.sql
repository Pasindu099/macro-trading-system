\pset pager off
\set ON_ERROR_STOP on

DROP TABLE IF EXISTS pg_temp.p0_scale;
CREATE TEMP TABLE p0_scale AS
WITH ordered AS (
    SELECT o.source_release_id, o.indicator_id, o.country_code, o.currency_code,
           o.indicator_key, o.release_date_utc, o.actual_value,
           i.unit,
           CASE
             WHEN o.indicator_key LIKE '%yoy%' THEN 'YoY %'
             WHEN o.indicator_key LIKE '%mom%' OR o.indicator_key LIKE '%qoq%' THEN 'MoM/QoQ %'
             WHEN i.unit = 'index' THEN 'level index'
             WHEN i.unit = 'balance' THEN 'balance'
             WHEN coalesce(i.unit, '') ~ '^(bn_|mn_|millions|thousands|trillions_)' THEN 'signed flow'
             ELSE 'other'
           END AS unit_type,
           avg(o.actual_value) OVER w6 AS mean6,
           stddev_samp(o.actual_value) OVER w6 AS std6,
           avg(o.actual_value) OVER w24 AS mean24,
           stddev_samp(o.actual_value) OVER w24 AS std24,
           stddev_samp(o.actual_value) OVER wlong AS longstd
    FROM processed.macro_observations o
    JOIN processed.indicator_feature_map fmap ON fmap.indicator_id=o.indicator_id
    JOIN indicators i ON i.id=o.indicator_id
    WHERE o.actual_value IS NOT NULL AND o.is_latest IS TRUE
    WINDOW
      w6 AS (PARTITION BY o.indicator_id ORDER BY o.reference_period_start NULLS LAST,
             o.release_timestamp_utc,o.source_release_id ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING),
      w24 AS (PARTITION BY o.indicator_id ORDER BY o.reference_period_start NULLS LAST,
              o.release_timestamp_utc,o.source_release_id ROWS BETWEEN 24 PRECEDING AND 1 PRECEDING),
      wlong AS (PARTITION BY o.indicator_id ORDER BY o.reference_period_start NULLS LAST,
                o.release_timestamp_utc,o.source_release_id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
)
SELECT *,
       greatest(std6, 0.5, abs(mean6)*0.05) AS old_scale,
       greatest(std24, longstd*0.25) AS new_scale,
       (actual_value-mean6)/nullif(greatest(std6,0.5,abs(mean6)*0.05),0) AS old_z,
       (actual_value-mean6)/nullif(greatest(std24,longstd*0.25),0) AS new_z
FROM ordered;

\echo 'OLD FLOOR BINDING BY UNIT TYPE'
SELECT unit_type, count(*) FILTER (WHERE std6 IS NOT NULL) eligible,
       count(*) FILTER (WHERE std6 IS NOT NULL AND old_scale>std6) binds,
       count(*) FILTER (WHERE std6 IS NOT NULL AND old_scale=0.5) absolute_floor,
       count(*) FILTER (WHERE std6 IS NOT NULL AND old_scale=abs(mean6)*0.05 AND abs(mean6)*0.05>0.5) relative_floor,
       round(100.0*count(*) FILTER (WHERE std6 IS NOT NULL AND old_scale>std6)/nullif(count(*) FILTER (WHERE std6 IS NOT NULL),0),1) bind_pct
FROM p0_scale GROUP BY unit_type ORDER BY eligible DESC;

\echo 'OLD FLOOR BINDING BY INDICATOR (BINDING ROWS ONLY)'
SELECT currency_code, indicator_key, unit_type,
       count(*) FILTER (WHERE std6 IS NOT NULL) eligible,
       count(*) FILTER (WHERE std6 IS NOT NULL AND old_scale>std6) binds,
       round(100.0*count(*) FILTER (WHERE std6 IS NOT NULL AND old_scale>std6)/nullif(count(*) FILTER (WHERE std6 IS NOT NULL),0),1) bind_pct
FROM p0_scale GROUP BY currency_code,indicator_key,unit_type
ORDER BY binds DESC, currency_code,indicator_key;

\echo 'NEW LONG-RUN ANCHOR BINDING BY UNIT TYPE'
SELECT unit_type,count(*) FILTER (WHERE std24 IS NOT NULL) eligible,
       count(*) FILTER (WHERE std24 IS NOT NULL AND longstd*0.25>std24) anchor_binds,
       round(100.0*count(*) FILTER (WHERE std24 IS NOT NULL AND longstd*0.25>std24)/nullif(count(*) FILTER (WHERE std24 IS NOT NULL),0),1) bind_pct
FROM p0_scale GROUP BY unit_type ORDER BY eligible DESC;

\echo 'OLD CAP COUNTS BY CURRENCY'
SELECT currency_code,count(*) caps FROM p0_scale WHERE abs(old_z)>5 GROUP BY currency_code ORDER BY caps DESC;

\echo 'OLD CAP TOP SERIES'
SELECT currency_code,indicator_key,unit_type,count(*) caps,
       min(release_date_utc) first_date,max(release_date_utc) last_date,
       round(max(abs(old_z))::numeric,2) max_abs_z
FROM p0_scale WHERE abs(old_z)>5
GROUP BY currency_code,indicator_key,unit_type ORDER BY caps DESC,currency_code LIMIT 20;

\echo 'STORED FINAL BUILD CAP BREAKDOWN'
SELECT s.currency_code,s.indicator_key,s.unit_type,count(*) caps,
       min(s.release_date_utc) first_date,max(s.release_date_utc) last_date
FROM p0_scale s JOIN processed.indicator_features f USING(source_release_id)
WHERE abs(f.standardized_surprise_score)=5 AND abs(s.old_z)>5
GROUP BY s.currency_code,s.indicator_key,s.unit_type
ORDER BY caps DESC,s.currency_code,s.indicator_key;

\echo 'OLD CAP DATES'
SELECT currency_code,indicator_key,release_date_utc,round(old_z::numeric,3) raw_z
FROM p0_scale WHERE abs(old_z)>5 ORDER BY currency_code,indicator_key,release_date_utc;

\echo 'WATCHLIST OLD V NEW'
SELECT currency_code,indicator_key,release_date_utc,actual_value,mean6,std6,old_scale,
       round(old_z::numeric,4) old_z,mean24,std24,longstd,new_scale,round(new_z::numeric,4) new_z
FROM p0_scale
WHERE (currency_code='EUR' AND indicator_key='cpi_headline_yoy' AND release_date_utc BETWEEN DATE '2026-09-01' AND DATE '2026-10-31')
   OR (currency_code IN ('USD','CHF','NZD','GBP') AND indicator_key IN ('cpi_headline_yoy','core_cpi_yoy'))
ORDER BY currency_code,indicator_key,release_date_utc DESC;
