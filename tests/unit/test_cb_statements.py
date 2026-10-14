"""Step 12 Part C: statement extraction, discovery helpers and sentence diff."""

from datetime import date
from pathlib import Path

from app.processing.cb_statements import html_text, rbnz_links
from app.services.cb_statement_diff import diff_statements, split_sentences, topic

FIX = Path("tests/fixtures/cb_statements")


def test_fed_fixture_diff_reports_only_changed_sentences_with_topics():
    old = (FIX / "fed_2026-07-29.txt").read_text(encoding="utf-8")
    new = (FIX / "fed_2026-09-16.txt").read_text(encoding="utf-8")
    changes = diff_statements(old, new)
    assert 0 < len(changes) < 20
    vote = next(c for c in changes if c["kind"] == "changed" and "vote" in c["new"])
    assert "9 – 3" in vote["old"] and "12 – 0" in vote["new"]
    rate = next(c for c in changes if c["new"] and "raise the target range" in c["new"])
    assert rate["topic"] == "policy stance" and "maintain the target range" in rate["old"]
    assert diff_statements(old, old) == []


def test_date_only_changes_are_not_reported():
    old = "Issued on July 29, 2026 by the Committee in Washington. Inflation remains elevated across the economy."
    new = "Issued on September 16, 2026 by the Committee in Washington. Inflation remains elevated across the economy."
    assert diff_statements(old, new) == []


def test_added_removed_and_unrelated_replacement():
    old = "The Committee held rates steady today. Exports weakened sharply over the quarter."
    new = "The Committee held rates steady today. The franc appreciated against the euro this month."
    kinds = [(c["kind"], c["topic"]) for c in diff_statements(old, new)]
    assert kinds == [("removed", "growth"), ("added", "FX")]


def test_sentence_split_protects_abbreviations_and_joins_pdf_wraps():
    body = "Bank of Japan\nThe U.S. economy grew at 2 p.m. today as\nexpected by most analysts. Wages rose 3 percent."
    assert split_sentences(body) == ["The U.S. economy grew at 2 p.m. today as expected by most analysts.",
                                     "Wages rose 3 percent."]


def test_topics():
    assert topic("The unemployment rate edged down to 4.1 percent.") == "labor"
    assert topic("The Committee will continue reducing its holdings of Treasury securities.") == "balance sheet"
    assert topic("The weather was pleasant for the meeting.") == "other"


def test_extraction_rbnz_rich_text_and_ecb_drops_qa():
    rbnz = ('<main><div>Browser issue banner text here</div><div class="rich-text"><p>The Monetary Policy '
            'Committee today increased the OCR.</p></div><div class="rich-text"><p>Inflation is 4.1 percent.</p></div></main>')
    assert html_text(rbnz, "RBNZ") == "The Monetary Policy Committee today increased the OCR.\nInflation is 4.1 percent."
    ecb = ('<main><div class="section"><p>Good afternoon, the decision follows.</p><p>We are now ready to take your '
           'questions.</p><p>Question: what about the euro?</p></div></main>')
    assert html_text(ecb, "ECB").endswith("We are now ready to take your questions")


def test_rbnz_links_match_decision_month():
    sitemap = ("<loc>https://www.rbnz.govt.nz/news-and-events/news/2026/09/ocr-increased-by-25-basis-points-to-2-75</loc>"
               "<loc>https://www.rbnz.govt.nz/news-and-events/news/2026/09/financial-stability-report</loc>"
               "<loc>https://www.rbnz.govt.nz/news-and-events/news/2026/06/ocr-held</loc>")
    links = rbnz_links(sitemap, [date(2026, 9, 2)])
    assert links == [(date(2026, 9, 2), "https://www.rbnz.govt.nz/news-and-events/news/2026/09/"
                                        "ocr-increased-by-25-basis-points-to-2-75", "html")]
