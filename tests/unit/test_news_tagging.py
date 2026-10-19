from datetime import UTC, date, datetime

import pytest

from app.services.news_tagging import classify, detect_currencies, detect_drivers, same_story

T = datetime(2026, 10, 19, 12, tzinfo=UTC)
ENERGY = {"id": 7, "situation_id": "energy_shock", "scope_key": "GLOBAL", "severity": "high",
          "started_at": date(2026, 10, 1), "ended_at": None}
LABOR_ENDED = {"id": 8, "situation_id": "labor_deterioration", "scope_key": "USD", "severity": "medium",
               "started_at": date(2026, 9, 1), "ended_at": date(2026, 9, 30)}


def tag(title, source="investinglive", category="all", summary="", episodes=(ENERGY,), when=T):
    return classify(title=title, summary=summary, source=source, source_category=category,
                    published_at=when, episodes=list(episodes))


@pytest.mark.parametrize("title,source,reason", [
    ("Bitcoin hits record high above $150k", "investinglive", "crypto_only"),
    ("Tesla shares jump after earnings beat", "investinglive", "single_stock"),
    ("Celebrity wedding draws crowds", "al_jazeera", "unrelated"),
    ("Scheduled economic release: CPI", "fred", "excluded_source"),
])
def test_relevance_filter_drops(title, source, reason):
    result = tag(title, source=source)
    assert result.status == "dropped" and result.reason == reason


@pytest.mark.parametrize("title,source", [
    ("EUR/USD rises after ECB holds rates", "investinglive"),
    ("Bitcoin slides as dollar strengthens", "investinglive"),  # not crypto-only
    ("Gold steadies near record", "kitco"),
    ("Kashkari: no rush to cut", "investinglive"),
    ("Japan September trade balance -¥200bn vs -¥150bn expected", "investinglive"),
    ("US stocks close higher, Nasdaq leads", "investinglive"),  # index level, not single stock
    ("Iran threatens to close Strait of Hormuz", "al_jazeera"),  # active situation link
])
def test_relevance_filter_keeps(title, source):
    assert tag(title, source=source).status == "kept"


def test_currency_detection_pairs_words_and_no_war_in_warsh():
    assert detect_currencies("USDJPY spikes as yen slides") == ["JPY", "USD"]
    assert detect_currencies("Sterling firms; gilts rally") == ["GBP"]
    result = tag("Warsh says inflation fight not over")
    assert result.news_type == "CB speaker" and result.news_type != "Geopolitics"


def test_types_and_impact():
    decision = tag("Bank of Canada cuts rates by 25 bp to 2.25%")
    assert (decision.news_type, decision.impact, decision.cb_decision) == ("CB speaker", "High", True)
    data = tag("US September CPI +0.4% vs +0.3% expected")
    assert (data.news_type, data.impact, data.data_tier, data.dominance_tier) == ("Data", "High", "high", "Economic data")
    china = tag("China September CPI 0.9% y/y (expected 1%, prior 0.8%)")
    assert (china.news_type, china.impact) == ("Data", "Medium")  # non-G10 print is not High
    medium = tag("Germany October ZEW 39.3 vs 41.0 expected")
    assert (medium.news_type, medium.impact) == ("Data", "Medium")
    analysis = tag("EUR/USD Price Forecast: bulls eye 1.1700", source="fxstreet", category="analysis")
    assert (analysis.news_type, analysis.impact) == ("Analysis", "Low")
    ing = tag("FX Daily: Dollar finds its feet", source="ing_think", category="analysis")
    assert ing.news_type == "Analysis"
    gold = tag("Gold extends gains", source="kitco", category="news")
    assert (gold.news_type, gold.impact, gold.is_commodity) == ("Commodities", "Medium", True)
    geo = tag("US and China agree tariff truce", source="al_jazeera", category="all_news")
    assert geo.news_type == "Geopolitics" and geo.currencies == ["USD"]
    # Geopolitics alone is not on the keep list: no G10 / CB / data / commodity / active situation -> dropped.
    assert tag("Ukraine and Russia agree ceasefire talks", source="al_jazeera").reason == "unrelated"
    fiscal = tag("UK budget: chancellor confirms fiscal rules")
    assert fiscal.news_type == "Politics & fiscal" and fiscal.currencies == ["GBP"]


def test_speaker_voter_and_profile_link():
    voter = tag("Fed's Logan: policy is only slightly restrictive")  # 2026 voter
    assert voter.speakers[0]["speaker_id"] == "logan" and voter.speakers[0]["voter"] is True
    assert voter.speakers[0]["profile_url"] == "/central-banks?bank=FED&speaker=logan"
    assert voter.impact == "High"
    non_voter = tag("Fed's Goolsbee: inflation progress has stalled")  # not a 2026 voter
    assert non_voter.speakers[0]["voter"] is False and non_voter.impact == "Medium"


def test_situation_link_only_while_episode_active():
    linked = tag("Oil jumps as Hormuz tanker traffic halts")
    assert linked.situation_ids == ["energy_shock"] and linked.impact == "High"
    assert linked.dominance_tier == "Risk sentiment" and linked.episode_ids == [7]
    ended = tag("US payrolls disappoint again: +12K vs +80K expected", episodes=[LABOR_ENDED])
    assert ended.situation_ids == []


def test_gold_drivers_with_direction():
    drivers = detect_drivers("Gold rises as Treasury yields fall and dollar weakens")
    assert drivers == [{"driver": "yields", "direction": "down"}, {"driver": "dollar", "direction": "down"}]
    more = {d["driver"]: d["direction"] for d in detect_drivers(
        "Gold slips on firmer dollar, higher real yields; safe-haven demand fades, ETF outflows continue "
        "as rate cut bets grow; central bank buying remains strong; oil prices climb")}
    assert more == {"dollar": "up", "real_yields": "up", "safe_haven": "down", "etf_flows": "down",
                    "fed_rates": "dovish", "cb_buying": "up", "oil": "up"}
    assert "yields" not in more  # real yields not double counted
    no_direction = detect_drivers("Gold traders watch inflation data")
    assert no_direction == [{"driver": "inflation", "direction": None}]
    assert detect_drivers("Oil rises on supply worries") == []  # oil is not its own driver
    kitco = tag("Gold PM report", source="kitco", summary="Gold gained as falling yields and a softer dollar helped.")
    assert {d["driver"]: d["direction"] for d in kitco.drivers} == {"yields": "down", "dollar": "down"}


def test_dedupe_title_similarity():
    assert same_story("ECB's Lagarde: inflation close to target",
                      "Lagarde says euro zone inflation is close to target")
    assert same_story("Gold hits record high as dollar slides", "Gold hits a record high as the dollar slides")
    assert not same_story("US CPI 0.4% vs 0.3% expected", "US PPI 0.4% vs 0.3% expected")
    assert not same_story("Fed's Waller backs cut", "ECB's Lane backs hold")
