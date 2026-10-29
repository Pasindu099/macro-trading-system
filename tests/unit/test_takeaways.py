"""Shared contract, safe accessible glossary, writing rules and switch semantics."""
import re
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services import positioning_page
from app.services import takeaways


def test_coverage_and_glossary():
    assert set(takeaways.REGISTRY) == set(positioning_page.PANELS)
    assert set(takeaways.COVERAGE["positioning"]) == set(positioning_page.PANELS)
    assert set(takeaways.glossary()) >= {"hedge funds", "fast money", "asset managers", "slow money", "dealers",
        "COT report", "open interest", "net position", "percentile", "crowded", "squeeze", "retail traders", "% by volume"}


@pytest.mark.parametrize("panel", list(takeaways.REGISTRY))
@pytest.mark.parametrize("state", ["ok", "unavailable", "stale"])
def test_parts_are_plain_descriptive_and_explicit(panel, state):
    output = takeaways.REGISTRY[panel]({"state": state})
    for key in ("shows", "now", "watch"):
        text = output[key]
        assert not re.search(r"\b(will|likely|expected to|forecast)\b", text, re.I)
        assert len(re.findall(r"[.!?](?:\s|$)", text)) <= 2
        assert "contracts" not in text and "σ" not in text
        assert "".join(segment["text"] for segment in output["parts"][key]) == text
    assert "unavailable" in output["now"] if state != "ok" else "not available yet" in output["now"]
    if state == "stale":
        assert "stale" in output["now"]


def test_glossary_is_safe_and_keyboard_touch_accessible():
    env = Environment(loader=FileSystemLoader("app/web/templates"), autoescape=select_autoescape())
    module = env.get_template("_takeaway.html").module
    html = str(module.takeaway(takeaways.crowding({"state": "unavailable"})))
    assert "What this shows" in html and "What it says now" in html and "what to watch" in html
    assert '<details class="glossary-term"><summary>hedge funds</summary>' in html
    assert 'role="note"' in html and 'data-explanation' in html
    hostile = str(module.term("<script>bad</script>", '<img onerror="bad()">'))
    assert "<script>" not in hostile and "<img" not in hostile and "&lt;script&gt;" in hostile


def test_explanations_switch_default_cookie_and_reload():
    # Execute the real vanilla JS in node with DOM/cookie event boundaries.
    script = r"""
const fs=require('fs'),vm=require('vm');
const code=fs.readFileSync('app/web/static/js/explanations.js','utf8');
let cookie='',root={};
function boot(){let ready,click;const button={setAttribute(k,v){this[k]=v},addEventListener(k,v){click=v}};
 const document={documentElement:{dataset:root},querySelectorAll(){return [button]},addEventListener(k,v){ready=v},
 get cookie(){return cookie},set cookie(v){cookie=v.split(';')[0]}};
 vm.runInNewContext(code,{document});ready();return {button,click};}
let first=boot();if(root.explanations!=='on'||first.button['aria-pressed']!=='true')throw Error('default');
first.click();if(cookie!=='explanations=off'||root.explanations!=='off')throw Error('cookie');
let reload=boot();if(root.explanations!=='off'||reload.button.textContent!=='Explanations: Off')throw Error('reload');
reload.click();if(cookie!=='explanations=on')throw Error('on');
"""
    import shutil
    if not shutil.which("node"):
        pytest.skip("Node is not installed in this container; switch also covered by browser check")
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    css = Path("app/web/static/css/takeaways.css").read_text()
    assert '[data-explanations="off"] [data-explanation]' in css
