"""Write a reproducible 30-sentence lexicon_v1 sample for manual review (Step 12 Part F).

20 tone-bearing (non-zero) and 10 zero-score sentences, seed 12, into REPORT_STEP12_TONE_SAMPLE.md.
"""

import asyncio
import json
from pathlib import Path

from sqlalchemy import text

from app.db.session import get_sessionmaker

OUT = Path("REPORT_STEP12_TONE_SAMPLE.md")
QUERY = text("""SELECT s.bank, s.speaker_id, s.sentence_date, s.source_type, s.theme, s.sentence,
        c.score::float AS score, c.hits
    FROM cb_speech_sentences s JOIN cb_sentence_scores c ON c.sentence_id = s.id AND c.method = 'lexicon_v1'
    WHERE (c.score <> 0) = :nonzero
    ORDER BY md5(s.id::text || '12') LIMIT :n""")


async def main() -> None:
    async with get_sessionmaker()() as session:
        await session.execute(text("SELECT 1"))
        rows = list((await session.execute(QUERY, {"nonzero": True, "n": 20})).mappings())
        rows += list((await session.execute(QUERY, {"nonzero": False, "n": 10})).mappings())
    lines = ["# Step 12 Part F — lexicon_v1 quality sample (manual review)", "",
             "Reproducible sample (ordered by md5(id || '12')): 20 non-zero and 10 zero-score sentences.",
             "Review column left blank for the reviewer: ✓ agree / ✗ disagree.", "",
             "| # | Bank | Speaker | Date | Src | Theme | Score | Phrases hit | Sentence | Review |",
             "| ---: | --- | --- | --- | --- | --- | ---: | --- | --- | --- |"]
    for i, r in enumerate(rows, start=1):
        hits = r["hits"] if isinstance(r["hits"], list) else json.loads(r["hits"] or "[]")
        phrases = "; ".join(f"{h['phrase']}{' (neg)' if h['negated'] else ''}{' (hedge)' if h['hedged'] else ''}"
                            f" {h['applied']:+g}" for h in hits) or "—"
        sentence = r["sentence"].replace("|", "/")[:300]
        lines.append(f"| {i} | {r['bank']} | {r['speaker_id'] or '—'} | {r['sentence_date']} | {r['source_type']} | "
                     f"{r['theme']} | {r['score']:+.2f} | {phrases} | {sentence} | |")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
