from decimal import Decimal

from app.services.news_context import why_line


def test_data_line_uses_actual_consensus_and_tracking():
    item = {"news_type": "Data", "currencies": ["USD"],
            "actual": Decimal("3.7"), "consensus": Decimal("3.6")}
    line = why_line(item, tracking={"USD": "inflation above projections"})
    assert "3.7 vs 3.6 expected" in line and "inflation above projections" in line


def test_speaker_role_vote_and_focus_without_ungated_tone():
    item = {"news_type": "CB speaker", "speakers": [{"name": "Logan", "role": "President",
            "voter": True, "bank": "FED", "speaker_id": "logan"}]}
    line = why_line(item, speaker_focus={("FED", "logan"): "inflation"})
    assert "Logan" in line and "current voter" in line and "recent focus: inflation" in line
    assert "hawkish" not in line


def test_situation_evidence_and_commodity_drivers():
    situation = {"news_type": "Geopolitics", "situation_ids": ["energy_shock"]}
    line = why_line(situation, episodes={"energy_shock": {"name": "Energy shock",
                    "evidence": {"current": {"brent_3m_pct": 12.3}}}})
    assert "Energy shock" in line and "Brent 3m +12.3%" in line
    commodity = {"news_type": "Commodities", "drivers": [
        {"driver": "yields", "direction": "up"}, {"driver": "dollar", "direction": "down"}]}
    assert why_line(commodity) == "Drivers named: yields ↑, dollar ↓."


def test_no_line_without_evidence():
    assert why_line({"news_type": "Flows & market"}) is None
    assert why_line({"news_type": "Commodities", "drivers": []}) is None
