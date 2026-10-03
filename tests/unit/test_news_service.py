from app.services.news import parse_rss_articles


def test_investinglive_rss_normalization_and_category():
    xml = """<rss><channel><item><title>ECB rate decision</title>
    <link>https://example.com/story</link><pubDate>Sat, 03 Oct 2026 10:00:00 GMT</pubDate>
    <description><![CDATA[<p>Policy update</p>]]></description>
    </item></channel></rss>"""
    assert parse_rss_articles(xml) == [{
        "title": "ECB rate decision", "link": "https://example.com/story",
        "pubDate": "Sat, 03 Oct 2026 10:00:00 GMT",
        "description": "Policy update", "category": "Central banks",
        "source": "investinglive.com",
    }]
