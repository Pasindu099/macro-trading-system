"""Step 12 Part F: lexicon_v1 scoring (incl. negation/hedging), speaker maths and committee balance."""

from datetime import date, timedelta

import pytest

from app.processing.tone_lexicon import score_sentence
from app.services.cb_tone import committee_balance, lean, speaker_profile, week_start


@pytest.mark.parametrize("sentence, expected", [
    ("We are not yet confident that inflation is on track to return to target.", 2.0),   # clause-scope negation
    ("Inflation is on track to return to target.", -1.0),
    ("It would not be appropriate to cut rates at this meeting.", 1.5),                  # negated dovish
    ("It is premature to cut rates.", 1.5),
    ("Further tightening may be appropriate.", 2.0),                                     # hedge inside phrase
    ("We could raise rates again.", 0.75),                                               # hedged
    ("Risks have become more balanced.", -1.0),
    ("We don't see room to cut and we are in no hurry.", 2.0),                         # n't negation; clip
    ("The labour market has softened, inflation has eased and further easing is likely.", -2.0),
    ("The weather was pleasant.", 0.0),
])
def test_lexicon_v1_scores(sentence, expected):
    result = score_sentence(sentence)
    assert result["score"] == expected and result["method"] == "lexicon_v1" and -2 <= result["score"] <= 2


def test_longest_phrase_consumes_inner_words():
    hits = score_sentence("Additional tightening is not off the table.")["hits"]
    assert [h["phrase"] for h in hits] == ["additional tightening"]


TODAY = date(2026, 10, 14)


def test_tone_baseline_shift_lean_and_focus_mix():
    recent = [(TODAY - timedelta(days=d), 1.0, "inflation") for d in (1, 5, 10)] + [(TODAY - timedelta(days=2), 0.0, "labor")]
    base = [(TODAY - timedelta(days=d), 0.0 + s, "growth") for d, s in ((60, 0.5), (90, -0.5), (120, 0.3))]
    profile = speaker_profile(recent + base, TODAY)
    assert profile["tone_30d"] == 1.0 and profile["tone_sentences"] == 3 and profile["lean"] == "hawkish"
    assert profile["baseline_6m"] == 0.1 and profile["shift"] == 0.9 and profile["shifting"] is True
    assert profile["shift_direction"] == "hawkish"
    assert profile["focus_mix"] == {"inflation": 0.75, "labor": 0.25}


def test_insufficient_sentences_and_lean_thresholds():
    profile = speaker_profile([(TODAY, 1.5, "inflation"), (TODAY, 1.5, "inflation")], TODAY)
    assert profile["tone_30d"] is None and profile["shift"] is None and profile["shifting"] is False
    assert (lean(0.5), lean(-0.5), lean(0.49), lean(None)) == ("hawkish", "dovish", "neutral", None)


def test_committee_balance_weekly_shares_and_voters():
    monday = week_start(TODAY)
    points = [("a", monday, 1.0), ("a", monday, 0.5), ("b", monday + timedelta(days=1), -1.0),
              ("c", monday, 0.2), ("d", monday - timedelta(weeks=1), -0.8), ("e", monday - timedelta(weeks=20), 1.0)]
    weeks = committee_balance(points, TODAY, voter_of=lambda s, y: s == "a")
    assert len(weeks) == 12 and weeks[-1]["week"] == monday
    assert weeks[-1]["active"] == 3 and weeks[-1]["shares"] == {"hawkish": 0.333, "neutral": 0.333, "dovish": 0.333}
    assert [e["voter"] for e in weeks[-1]["speakers"]] == [True, False, False]
    assert weeks[-2]["shares"] == {"hawkish": 0.0, "neutral": 0.0, "dovish": 1.0}
    assert weeks[0]["active"] == 0 and weeks[0]["shares"] is None


@pytest.mark.parametrize("sentence, expected, reason", [
    ("That said, the recent tightening cycle showed our commitment.", 0.0, "descriptive/past"),
    ("Countries around the world tilt toward rate hikes.", 0.0, "third party"),
    ("Should we cut rates?", 0.0, "question"),
    ("Markets are pricing two rate cuts this year.", 0.0, "market pricing"),
    ("A rate cut has its own trade-offs.", 0.0, "no forward/normative context"),
    ("Rates were cut and the tightening of credit standards followed.", 0.0, "descriptive/past"),
    ("I believe further easing is warranted at coming meetings.", -1.5, None),
    ("It would not be appropriate to cut rates at this meeting.", 1.5, None),          # negation kept
    ("We could raise rates if needed.", 0.75, None),                                    # hedge kept
    ("We are not yet confident that inflation is on track to return to target.", 2.0, None),
    ("It is premature to cut.", 1.5, None),                                             # self-normative phrase
])
def test_lexicon_v2_context_gates(sentence, expected, reason):
    from app.processing.tone_lexicon import score_sentence_v2
    result = score_sentence_v2(sentence)
    assert result["method"] == "lexicon_v2" and result["score"] == expected
    if reason:
        assert {h["dropped"] for h in result["dropped"]} == {reason}


SAMPLE = """# t
## lexicon_v1 sample
| # | Bank | Speaker | Date | Src | Theme | Score | Phrases hit | Sentence | Review |
| ---: | --- | --- | --- | --- | --- | ---: | --- | --- | --- |
| 1 | FED | a | d | speech | x | +1.50 | p | s | ✓ |
| 2 | FED | a | d | speech | x | -0.50 | p | s | ✗ |
| 3 | FED | a | d | speech | x | +0.00 | — | s | ✗ |
| 4 | FED | a | d | speech | x | +1.00 | p | s |  |
## lexicon_v2 sample
| # | Bank | Speaker | Date | Src | Theme | Score | Phrases hit | Sentence | Review |
| ---: | --- | --- | --- | --- | --- | ---: | --- | --- | --- |
| 1 | FED | a | d | speech | x | +1.50 | p | s | y |
"""


def test_label_parsing_counts_non_zero_rows_only():
    from app.services.cb_tone_gate import parse_labels
    assert parse_labels(SAMPLE) == {"lexicon_v1": {"correct": 1, "wrong": 1, "unlabelled": 1},
                                    "lexicon_v2": {"correct": 1, "wrong": 0, "unlabelled": 0}}


def test_display_gate_needs_min_labels_and_precision(tmp_path, monkeypatch):
    from app.services import cb_tone_gate as gate
    monkeypatch.setattr(gate, "gate_config", lambda: {"min_precision": 0.75, "min_labels": 4, "sample_path": "x"})
    head = "| # | Bank | Speaker | Date | Src | Theme | Score | Phrases hit | Sentence | Review |\n| -: | - | - | - | - | - | -: | - | - | - |\n"
    path = tmp_path / "s.md"
    for ok, expected in ((4, "lexicon_v1"), (3, None)):
        rows = "\n".join(f"| {i} | FED | a | d | speech | x | +1.00 | p | s | {'✓' if i <= ok else '✗'} |" for i in range(1, 6))
        path.write_text("## lexicon_v1 sample\n" + head + rows + "\n", encoding="utf-8")
        method, info = gate.display_method(path)
        assert method == expected and info["status"] == ("passed" if expected else "failed")
    path.write_text("## lexicon_v1 sample\n" + head + "| 1 | FED | a | d | speech | x | +1.00 | p | s |  |\n", encoding="utf-8")
    assert gate.display_method(path) == (None, gate.display_method(path)[1]) and gate.display_method(path)[1]["status"] == "awaiting labels"
