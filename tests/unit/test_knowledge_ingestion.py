from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.knowledge.ingestion import (
    ExtractedPage,
    clean_page_text,
    detect_duplicate_hashes,
    discover_pdf_files,
    parse_publication_date,
    segment_pages,
    sha256_file,
)


def test_pdf_discovery_is_recursive_and_pdf_only(tmp_path: Path):
    nested = tmp_path / "nested"
    nested.mkdir()
    pdf = nested / "report.pdf"
    txt = tmp_path / "note.txt"
    pdf.write_bytes(b"%PDF-1.4")
    txt.write_text("ignore me")

    assert discover_pdf_files(tmp_path) == [pdf.resolve()]


def test_sha256_hashing(tmp_path: Path):
    sample = tmp_path / "sample.pdf"
    sample.write_bytes(b"macro research")

    assert sha256_file(sample) == "ff58f19d80ae0677a61ae0549776b0868f203a31040870c332ea89644c4579e2"


def test_duplicate_detection_by_hash(tmp_path: Path):
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    third = tmp_path / "c.pdf"

    duplicates = detect_duplicate_hashes(
        {
            first: "same",
            second: "same",
            third: "different",
        }
    )

    assert duplicates == {"same": [first, second]}


@pytest.mark.parametrize(
    ("filename", "text", "expected", "confidence"),
    [
        (
            "Jobs, BCOM, Calendar - Spectra Markets-January 9, 2026.pdf",
            "",
            date(2026, 1, 9),
            "medium",
        ),
        (
            "Data Trifecta - Spectra Markets.pdf",
            "HIGHLIGHTS Data Trifecta\nFebruary 9, 2026\nCurrent Views",
            date(2026, 2, 9),
            "high",
        ),
    ],
)
def test_publication_date_parsing(filename: str, text: str, expected: date, confidence: str):
    parsed, parsed_confidence = parse_publication_date(filename, text, {})

    assert parsed == expected
    assert parsed_confidence == confidence


def test_publication_date_missing_is_flagged():
    parsed, confidence = parse_publication_date("No Date.pdf", "Current Views", {})

    assert parsed is None
    assert confidence == "missing"


def test_cleaning_preserves_raw_elsewhere_but_removes_footer_noise():
    raw = "\n".join(
        [
            "HIGHLIGHTS",
            "Data Trifecta",
            "https://www.spectramarkets.com/amfx/data-trifecta/",
            "1/16",
        ]
    )

    cleaned = clean_page_text(raw, "Data Trifecta - Spectra Markets")

    assert "HIGHLIGHTS" in cleaned
    assert "spectramarkets.com" not in cleaned
    assert "1/16" not in cleaned


def test_page_level_provenance_in_sections():
    sections = segment_pages(
        [
            ExtractedPage(
                page_number=3,
                raw_text="Raw page",
                cleaned_text="JPY intervention risk\nPolicy consequences",
            )
        ]
    )

    assert sections[0]["page_start"] == 3
    assert sections[0]["page_end"] == 3
    assert sections[0]["section_title"] == "JPY intervention risk"
