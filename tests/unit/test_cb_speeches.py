"""Step 12 Part E: official name/alias matching, point extraction and source parsers."""

from datetime import date

from app.processing.cb_officials import extract_points, is_voter, match_officials, theme
from app.processing.cb_speeches import attribute_headline, parse_ecb_dataset, parse_fed_index, parse_rss

DAY = date(2026, 10, 1)


def test_alias_matching_word_boundaries_and_case():
    assert attribute_headline("Fed's Waller: Policy is in a good place", DAY) == [("FED", "waller")]
    assert attribute_headline("waller says nothing", DAY) == []                      # case-sensitive
    assert attribute_headline("Lagarde and Schnabel speak in Frankfurt", DAY) == [("ECB", "lagarde"), ("ECB", "schnabel")]


def test_ambiguous_surnames_need_bank_context():
    assert attribute_headline("Williams says the economy is strong", DAY) == []
    assert attribute_headline("Fed's Williams: rates are restrictive", DAY) == [("FED", "williams_ny")]
    assert attribute_headline("RBNZ's Rebecca Williams joins the MPC", date(2026, 10, 20)) == [("RBNZ", "williams_nz")]
    assert attribute_headline("BoE's Taylor: more cuts likely", DAY) == [("BOE", "taylor")]


def test_tenure_dates_respected():
    assert attribute_headline("Fed's Bostic: inflation too high", date(2026, 1, 15)) == [("FED", "bostic")]
    assert attribute_headline("Fed's Bostic: inflation too high", date(2026, 6, 1)) == []
    assert attribute_headline("RBNZ's Breman: OCR on hold", date(2025, 6, 1)) == []


def test_boj_upper_case_surnames_and_voters():
    assert match_officials("Dec. 25, 2025 UEDA Kazuo, Governor", bank="BOJ") == [("BOJ", "ueda", 14)]
    assert is_voter("FED", "hammack", 2026) is True and is_voter("FED", "hammack", 2027) is False
    assert is_voter("FED", "waller", 2027) is True and is_voter("ECB", "nagel", 2026) is None


def test_point_extraction_keeps_policy_sentences_with_themes():
    text = ("Thank you for having me today. Inflation remains well above our 2 percent goal. "
            "The labor market has cooled but unemployment is low. I enjoyed the conference dinner very much. "
            "The dollar has appreciated sharply against most currencies.")
    assert [(p, th) for p, _, th in extract_points(text)] == [(1, "inflation"), (2, "labor"), (4, "FX")]
    assert theme("Credit spreads widened and financial conditions tightened.") == "financial conditions"


def test_rss_two_and_rdf_feeds():
    rss2 = ("<rss><channel><item><title>Speech by Andrew Bailey</title><link>https://boe/s1</link>"
            "<pubDate>Thu, 08 Oct 2026 10:00:00 GMT</pubDate><description>Bailey on growth</description></item>"
            "<item><title>Old</title><link>https://boe/s0</link><pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate></item>"
            "</channel></rss>")
    items = parse_rss(rss2, "BOE RSS", date(2025, 10, 1))
    assert [(i["date"], i["url"]) for i in items] == [(date(2026, 10, 8), "https://boe/s1")]
    rdf = ('<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" '
           'xmlns:dc="http://purl.org/dc/elements/1.1/"><item><title>Macklem speech</title>'
           '<link>https://boc/s</link><dc:date>2026-10-01T12:00:00-04:00</dc:date></item></rdf:RDF>')
    assert parse_rss(rdf, "BOC RSS", date(2025, 10, 1))[0]["date"] == date(2026, 10, 1)


def test_ecb_dataset_and_fed_index():
    csv = ("date|speakers|title|subtitle|contents\n"
           "2026-09-30|Isabel Schnabel|Overlapping shocks|Speech by Isabel Schnabel|  SPEECH  Inflation is back.\n"
           "2024-01-01|Christine Lagarde|Old|Old|old\n")
    rows = parse_ecb_dataset(csv, date(2025, 10, 1))
    assert len(rows) == 1 and rows[0]["kind"] == "inline" and "Isabel Schnabel" in rows[0]["speaker_text"]
    html = ('<div><a href="/newsevents/speech/waller20261008a.htm">The Outlook</a> Governor Christopher J. Waller</div>'
            '<div><a href="/newsevents/speech/cook20240101a.htm">Old</a></div>')
    items = parse_fed_index(html, date(2025, 10, 1))
    assert [(i["date"], i["url"].rsplit("/", 1)[-1]) for i in items] == [(date(2026, 10, 8), "waller20261008a.htm")]
