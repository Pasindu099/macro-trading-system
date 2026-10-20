from pathlib import Path


def test_pair_links_use_pending_route():
    panel = Path("app/web/templates/overview/panel.html").read_text(encoding="utf-8")
    detail = Path("app/services/calendar_detail.py").read_text(encoding="utf-8")
    assert "/pairs?pair=" not in panel + detail
    assert "/pairs/{{ cell.pair|replace('/','') }}" in panel
    assert '"href": "/pairs/" + pair.replace("/", "")' in detail
