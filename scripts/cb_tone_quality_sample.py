"""Manual-review samples for tone methods in REPORT_STEP12_TONE_SAMPLE.md (Step 12 Parts F and decision 4).

- lexicon_v1 section: 20 non-zero + 10 zero-score sentences (seed '12'); kept verbatim once written.
- lexicon_v2 section: 30 non-zero sentences (seed 'v2'); written only if the section is missing,
  so labels already entered are never overwritten.
Label the Review column with ✓ (sign and policy relevance correct) or ✗; cb_tone_gate reads it.
"""

import asyncio
import json
import re
from pathlib import Path

from sqlalchemy import text

from app.db.session import get_sessionmaker

OUT = Path("REPORT_STEP12_TONE_SAMPLE.md")
QUERY = text("""SELECT s.bank, s.speaker_id, s.sentence_date, s.source_type, s.theme, s.sentence,
        c.score::float AS score, c.hits
    FROM cb_speech_sentences s JOIN cb_sentence_scores c ON c.sentence_id = s.id AND c.method = :method
    WHERE (c.score <> 0) = :nonzero
    ORDER BY md5(s.id::text || :seed) LIMIT :n""")
HEADER = ["| # | Bank | Speaker | Date | Src | Theme | Score | Phrases hit | Sentence | Review |",
          "| ---: | --- | --- | --- | --- | --- | ---: | --- | --- | --- |"]
TITLE = ["# Step 12 — tone quality samples (manual review)", "",
         "Label each row's **Review** cell with ✓ (sign and policy relevance correct) or ✗. Precision on non-zero",
         "scores per method gates whether tone is displayed (config/tone_lexicon.yaml `display_gate`).", ""]


def table(rows) -> list[str]:
    lines = list(HEADER)
    for i, r in enumerate(rows, start=1):
        hits = r["hits"] if isinstance(r["hits"], list) else json.loads(r["hits"] or "[]")
        phrases = "; ".join(f"{h['phrase']}{' (neg)' if h['negated'] else ''}{' (hedge)' if h['hedged'] else ''}"
                            f" {h['applied']:+g}" for h in hits if not h.get("dropped")) or "—"
        sentence = r["sentence"].replace("|", "/")[:300]
        lines.append(f"| {i} | {r['bank']} | {r['speaker_id'] or '—'} | {r['sentence_date']} | {r['source_type']} | "
                     f"{r['theme']} | {r['score']:+.2f} | {phrases} | {sentence} | |")
    return lines


async def rows(method: str, nonzero: bool, n: int, seed: str):
    async with get_sessionmaker()() as session:
        return list((await session.execute(QUERY, {"method": method, "nonzero": nonzero, "n": n, "seed": seed})).mappings())


async def main() -> None:
    existing = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if "## lexicon_v1" in existing:
        v1 = existing[existing.index("## lexicon_v1"):]
        v1 = v1[:v1.index("## lexicon_v2")] if "## lexicon_v2" in v1 else v1
    elif existing:                                    # first-generation file: keep its table verbatim
        body = [line for line in existing.splitlines() if line.startswith("|")]
        v1 = "\n".join(["## lexicon_v1 sample", "", "20 non-zero and 10 zero-score sentences (seed '12').", "", *body]) + "\n"
    else:
        sample = await rows("lexicon_v1", True, 20, "12") + await rows("lexicon_v1", False, 10, "12")
        v1 = "\n".join(["## lexicon_v1 sample", "", "20 non-zero and 10 zero-score sentences (seed '12').", "",
                        *table(sample)]) + "\n"
    if "## lexicon_v2" in existing:
        v2 = existing[existing.index("## lexicon_v2"):]
    else:
        v2 = "\n".join(["## lexicon_v2 sample", "",
                        "30 non-zero lexicon_v2 sentences (seed 'v2'); phrases dropped by the context gates are not shown.",
                        "", *table(await rows("lexicon_v2", True, 30, "v2"))]) + "\n"
    OUT.write_text("\n".join(TITLE) + "\n" + v1.rstrip("\n") + "\n\n" + v2, encoding="utf-8")
    print("v1 rows", len(re.findall(r"^\| \d+ \|", v1, re.M)), "v2 rows", len(re.findall(r"^\| \d+ \|", v2, re.M)))


if __name__ == "__main__":
    asyncio.run(main())
