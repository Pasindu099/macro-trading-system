"""Step 7 Part E: frontend libraries are vendored and pinned; no runtime CDN scripts."""

import hashlib
import re
from pathlib import Path

TEMPLATES = Path("app/web/templates")
VENDOR = Path("app/web/static/vendor")
PINNED = {
    "echarts-5.6.0.min.js": "bf4a223524e40b77c304bec67e1222cf551f14880cf42c69dc046558e11c07b1",
    "htmx-1.9.12.min.js": "449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452",
}


def test_vendored_files_match_pinned_checksums():
    for name, digest in PINNED.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest


def test_templates_load_no_external_scripts_or_styles_except_google_fonts():
    for template in TEMPLATES.rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        for src in re.findall(r'<script[^>]+src="(https?://[^"]+)"', text):
            raise AssertionError(f"{template}: external script {src}")
        for href in re.findall(r'<link[^>]+href="(https?://[^"]+)"', text):
            assert href.startswith(("https://fonts.googleapis.com", "https://fonts.gstatic.com")), (template, href)


def test_pages_reference_vendored_versions():
    assert "vendor/echarts-5.6.0.min.js" in (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "vendor/htmx-1.9.12.min.js" in (TEMPLATES / "desk" / "page.html").read_text(encoding="utf-8")
