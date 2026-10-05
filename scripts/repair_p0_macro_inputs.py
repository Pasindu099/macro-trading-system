"""Quarantine premature actuals and split two EU expectations series.

Dry-run by default. Run --apply only against the intended database.
"""

import argparse
import asyncio
from sqlalchemy import text

from app.db.session import session_scope


async def main(apply: bool) -> None:
    async with session_scope() as session:
        future = (await session.execute(text("""
            SELECT r.id, i.country_code, i.canonical_name, r.released_at, r.actual
            FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            WHERE r.released_at > CURRENT_TIMESTAMP AND r.actual IS NOT NULL
            ORDER BY r.id
        """))).all()
        balance = (await session.execute(text("""
            SELECT r.id FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            WHERE i.country_code = 'EU' AND i.canonical_name = 'consumer_inflation_expectation'
              AND (date_trunc('month', r.released_at AT TIME ZONE 'UTC')::date
                       = r.period_start_date
                   OR GREATEST(abs(coalesce(r.actual, 0)), abs(coalesce(r.previous, 0)),
                               abs(coalesce(r.estimate, 0))) >= 10)
            ORDER BY r.id
        """))).scalars().all()
        print(f"future actuals to quarantine: {len(future)}")
        for row in future:
            print(f"  id={row.id} {row.country_code}/{row.canonical_name} {row.released_at} actual={row.actual}")
        print(f"EU survey-balance releases to split: {len(balance)}")
        print(f"EU survey-balance release IDs: {','.join(map(str, balance))}")
        if not apply:
            print("dry-run only; database unchanged")
            return
        await session.execute(text("""
            UPDATE indicator_releases
            SET actual = NULL,
                raw_payload = raw_payload || jsonb_build_object(
                    'quarantine_reason', 'future_actual_before_release')
            WHERE released_at > CURRENT_TIMESTAMP AND actual IS NOT NULL
        """))
        old = (await session.execute(text("""
            SELECT id FROM indicators WHERE country_code='EU'
              AND canonical_name='consumer_inflation_expectation'
        """))).scalar_one()
        new = (await session.execute(text("""
            INSERT INTO indicators (canonical_name, display_name, country_code,
                primary_category, secondary_categories, comparison, frequency,
                unit, is_higher_better_for_currency, importance, notes)
            SELECT 'consumer_price_expectations_balance',
                'Consumer price expectations (survey balance)', country_code,
                primary_category, secondary_categories, comparison, frequency,
                'balance', is_higher_better_for_currency, importance, notes
            FROM indicators WHERE id=:old
            ON CONFLICT (canonical_name, country_code) DO UPDATE SET unit='balance'
            RETURNING id
        """), {"old": old})).scalar_one()
        await session.execute(text("""
            UPDATE indicator_releases SET indicator_id=:new,
                raw_payload=raw_payload || jsonb_build_object(
                    'series_definition_inferred', 'ec_dg_ecfin_survey_balance')
            WHERE id = ANY(:ids)
        """), {"new": new, "ids": list(balance)})
        await session.execute(text("""
            UPDATE indicators SET unit='%',
                display_name='ECB consumer inflation expectation (12-month median)'
            WHERE id=:old
        """), {"old": old})
        fixed = await session.execute(text("""
            WITH ranked AS (
                SELECT id, is_latest,
                    row_number() OVER (
                        PARTITION BY indicator_id,
                            coalesce(period_start_date::text, period, released_at::date::text)
                        ORDER BY retrieved_at DESC, id DESC
                    ) = 1 AS winner
                FROM indicator_releases WHERE indicator_id IN (:old, :new)
            )
            UPDATE indicator_releases r SET is_latest=ranked.winner
            FROM ranked WHERE r.id=ranked.id
                AND r.is_latest IS DISTINCT FROM ranked.winner
        """), {"old": old, "new": new})
        print(f"latest flags corrected: {fixed.rowcount}")
        print("repair committed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="preview only (default)")
    action.add_argument("--apply", action="store_true", help="commit the repair")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
