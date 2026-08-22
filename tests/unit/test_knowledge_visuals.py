from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.knowledge.visuals import (
    classify_visual_metadata,
    detect_table_blocks,
    detect_table_candidates_from_pages,
    is_table_like_line,
    parse_table_rows,
    sha256_bytes,
    write_figure_artifact,
)


def test_detects_table_like_lines():
    assert is_table_like_line("USDJPY    150.25    -0.4%    high")
    assert is_table_like_line("USDJPY | 150.25 | -0.4% | high")
    assert not is_table_like_line("This is ordinary narrative prose.")


def test_detects_contiguous_table_block():
    text = "\n".join(
        [
            "Intro text",
            "USDJPY    150.25    -0.4%    high",
            "EURUSD    1.0875    +0.2%    low",
            "XAUUSD    2410.0    +1.1%    high",
            "Conclusion text",
        ]
    )

    blocks = detect_table_blocks(text)

    assert len(blocks) == 1
    assert "EURUSD" in blocks[0]


def test_parse_table_rows_on_spacing():
    rows = parse_table_rows("USDJPY    150.25    -0.4%\nEURUSD    1.0875    +0.2%")

    assert rows == [["USDJPY", "150.25", "-0.4%"], ["EURUSD", "1.0875", "+0.2%"]]


def test_table_candidates_keep_page_provenance():
    pages = [
        SimpleNamespace(
            page_number=7,
            cleaned_text="\n".join(
                [
                    "Market table",
                    "USDJPY    150.25    -0.4%",
                    "EURUSD    1.0875    +0.2%",
                    "XAUUSD    2410.0    +1.1%",
                ]
            ),
            raw_text="",
        )
    ]

    candidates = detect_table_candidates_from_pages(pages)

    assert candidates[0].page_number == 7
    assert candidates[0].structured_rows[0] == ["USDJPY", "150.25", "-0.4%"]


def test_figure_artifact_path_is_hash_based(tmp_path: Path):
    image_bytes = b"fake-image-bytes"
    image_hash = sha256_bytes(image_bytes)

    path = write_figure_artifact(tmp_path, 12, image_hash, "png", image_bytes)

    assert path == tmp_path / "12" / f"{image_hash}.png"
    assert path.read_bytes() == image_bytes


def test_visual_classifier_keeps_market_chart_context():
    decision, reason = classify_visual_metadata(
        width_px=900,
        height_px=520,
        nearby_text="USDJPY yield chart shows Fed repricing and equity reaction",
        caption=None,
    )

    assert decision == "keep"
    assert reason is None


def test_visual_classifier_ignores_non_market_context():
    decision, reason = classify_visual_metadata(
        width_px=900,
        height_px=520,
        nearby_text="A song and movie reference, thanks for reading",
        caption=None,
    )

    assert decision == "ignore"
    assert "non-market" in reason
