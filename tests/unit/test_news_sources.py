from datetime import UTC, datetime

import httpx
import pytest

from app.services import news_collector as nc

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>EUR/USD climbs after ECB</title><link>https://example.com/a</link>
<guid>g1</guid><pubDate>Mon, 19 Oct 2026 10:00:00 GMT</pubDate><description>Body</description></item>
<item><title>No link item</title></item>
</channel></rss>"""


def test_config_flags_intervals_and_kitco_pending():
    config = nc.load_config()
    sources = config["sources"]
    assert all(s["licensed_for_redistribution"] is False for s in sources.values())
    assert sources["investinglive"]["interval_seconds"] == 45
    assert len(sources["investinglive"]["feeds"]) == 3
    for sid in ("kitco", "fxstreet", "ing_think"):
        assert sources[sid]["interval_seconds"] >= 300
    assert sources["kitco"]["enabled"] is False and sources["kitco"]["feeds"][0]["url"] == ""
    assert {f["url"] for f in sources["fxstreet"]["feeds"]} == {
        "https://www.fxstreet.com/rss/news", "https://www.fxstreet.com/rss/analysis"}
    assert sources["ing_think"]["feeds"][0]["url"] == "https://think.ing.com/rss/"
    assert "kitco" not in nc.enabled_sources()


def test_validation_rejects_impolite_interval_missing_license_and_empty_url():
    base = {"label": "x", "enabled": True, "licensed_for_redistribution": False,
            "feeds": [{"category": "a", "url": "https://x/rss"}]}
    with pytest.raises(ValueError, match="polite"):
        nc.validate_config({"sources": {"fxstreet": {**base, "interval_seconds": 60}}})
    with pytest.raises(ValueError, match="licensed"):
        nc.validate_config({"sources": {"x": {k: v for k, v in base.items() if k != "licensed_for_redistribution"}
                                              | {"interval_seconds": 300}}})
    with pytest.raises(ValueError, match="empty feed URL"):
        nc.validate_config({"sources": {"x": {**base, "interval_seconds": 300,
                                              "feeds": [{"category": "a", "url": ""}]}}})


def test_one_switch_hides_unlicensed_sources():
    assert "fxstreet" in nc.visible_source_ids(show_unlicensed=True)
    assert nc.visible_source_ids(show_unlicensed=False) == set()


def test_parse_feed_normalizes_rows():
    fetched = datetime(2026, 10, 19, 10, 5, tzinfo=UTC)
    rows = nc.parse_feed("fxstreet", "news", "https://www.fxstreet.com/rss/news", RSS, fetched)
    assert len(rows) == 1
    row = rows[0]
    assert row["published_at"] == datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
    assert row["raw_payload"]["url_hash"] == nc.url_hash("https://example.com/a")
    assert row["raw_payload"]["collector"] == "app" and row["source_category"] == "news"


async def test_robots_check_uses_our_user_agent_and_blocks_disallowed(monkeypatch):
    nc._robots_cache.clear()
    seen_agents = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_agents.append(request.headers.get("user-agent"))
        if not request.headers.get("user-agent", "").startswith("ForexCompass/"):
            return httpx.Response(403)
        return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 headers=nc.source_identity.headers()) as client:
        assert await nc.robots_allowed(client, "https://www.fxstreet.com/rss/news") is True
        assert await nc.robots_allowed(client, "https://www.fxstreet.com/private/feed") is False
    assert all(agent.startswith("ForexCompass/") for agent in seen_agents)


async def test_collect_source_skips_feed_when_robots_disallows(monkeypatch):
    nc._robots_cache.clear()
    health = []

    async def fake_health(name, last, checked, healthy):
        health.append((name, healthy))

    async def deny(client, url, now=None):
        return False

    monkeypatch.setattr(nc, "_health", fake_health)
    monkeypatch.setattr(nc, "robots_allowed", deny)
    counts = await nc.collect_source("ing_think")
    assert counts["skipped_robots"] == 1 and counts["inserted"] == 0
    assert health == [("ing_think:analysis", False)]
