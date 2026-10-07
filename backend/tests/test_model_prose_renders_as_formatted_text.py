"""Model prose renders as formatted text through a renderer Sage owns, and never unsanitised (#681).

Live (2026-10-07): a deal brief drew `## Deal brief` and `**at risk**` as literal characters, because
the app put the model's markdown into a text node. The obvious repair is `dangerouslySetInnerHTML`
over a markdown parser, and marked passes HTML straight through, so a model answer quoting
`<img onerror>` would run in the viewer's page. `sage.Markdown` / `<Markdown>` parse with the
vendored marked and sanitise with the vendored DOMPurify, and fall back to plain text whenever the
sanitiser cannot run — including DOMPurify without a DOM, which returns its input unchanged.

What Node cannot show is DOMPurify cleaning real HTML; that needs a browser. These pin the wiring
(every byte of marked's output goes through `sanitize`) and the fallback.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.resources.app_helpers import FASTAPI, TEMPLATE
from sage.resources.bindings import Binding
from sage.resources.pinned_model import agents_block
from sage.workspace.stack import FASTAPI_ANTD, REACT_VITE

ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).parent / "js" / "app_markdown_harness.mjs"
TEMPLATES = ["fastapi-antd", "react-vite"]
HOSTILE = '## Deal brief\n\n**At risk** <img src=x onerror="alert(1)">'

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def _render(template: str, purify: str = "vendored", marked: str = "vendored", text: str = HOSTILE) -> dict:
    out = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=30, check=True,
                         input=json.dumps({"template": template, "text": text, "purify": purify,
                                           "marked": marked}))
    return json.loads(out.stdout)


@needs_node
@pytest.mark.parametrize("template", TEMPLATES)
def test_marked_output_reaches_the_page_only_through_the_sanitiser(template: str):
    got = _render(template, purify="stub")
    html = got["element"]["props"]["dangerouslySetInnerHTML"]["__html"]
    assert html.startswith("[sanitized]<h2>Deal brief</h2>")
    assert "<strong>At risk</strong>" in html
    assert got["html"] == html


@needs_node
@pytest.mark.parametrize("template", TEMPLATES)
def test_without_a_dom_the_vendored_sanitiser_is_not_trusted_and_the_text_is_plain(template: str):
    got = _render(template)
    assert "dangerouslySetInnerHTML" not in got["element"]["props"]
    assert got["element"]["props"]["children"] == [HOSTILE]
    assert "<img" not in got["html"] and "&lt;img" in got["html"]


@needs_node
@pytest.mark.parametrize("template", TEMPLATES)
def test_a_sanitiser_that_reports_itself_unsupported_is_not_trusted(template: str):
    # DOMPurify's own contract: unsupported, `sanitize` hands back its input unchanged.
    got = _render(template, purify="unsupported")
    assert "dangerouslySetInnerHTML" not in got["element"]["props"]
    assert "<img" not in got["html"]


@needs_node
@pytest.mark.parametrize(("purify", "marked"), [("missing", "vendored"), ("stub", "missing")])
def test_a_missing_library_falls_back_to_plain_text(purify: str, marked: str):
    got = _render("fastapi-antd", purify=purify, marked=marked)
    assert "dangerouslySetInnerHTML" not in got["element"]["props"]
    assert got["element"]["props"]["children"] == [HOSTILE]


def test_the_page_loads_both_libraries_before_the_renderer_and_the_renderer_before_the_app():
    page = (ROOT / "template/fastapi-antd/static/index.html").read_text()
    order = re.findall(r'<script src="static/([^"]+)"', page)
    for lib in ("vendor/marked.umd.js", "vendor/purify.min.js"):
        assert order.index(lib) < order.index("sage/markdown.js")
    assert order.index("sage/markdown.js") < order.index("app.js")


@pytest.mark.parametrize(("path", "banner"), [
    ("template/fastapi-antd/static/vendor/marked.umd.js", "marked v18.1.0"),
    ("template/react-vite/src/vendor/marked.esm.js", "marked v18.1.0"),
    ("template/fastapi-antd/static/vendor/purify.min.js", "DOMPurify 3.4.16"),
    ("template/react-vite/src/vendor/purify.es.mjs", "DOMPurify 3.4.16"),
])
def test_the_vendored_libraries_are_the_pinned_releases(path: str, banner: str):
    assert banner in (ROOT / path).read_text()[:400]


def test_the_react_vendor_folder_is_not_shown_to_the_agent_as_app_source():
    assert REACT_VITE.vendored == ("src/vendor/",)
    assert FASTAPI_ANTD.vendored == ("static/vendor/",)


def test_the_react_instructions_forbid_editing_the_renderer_and_its_libraries():
    agents = (ROOT / "template/react-vite/AGENTS.md").read_text()
    assert "`src/Markdown.tsx`" in agents and "`src/vendor/`" in agents


@pytest.mark.parametrize("names", [TEMPLATE, FASTAPI], ids=["react-vite", "fastapi-antd"])
def test_the_agent_is_told_to_render_model_prose_through_the_renderer(names):
    block = agents_block([Binding("llm_alias", "id-sonnet", "sonnet", "Claude Sonnet 4.6")], [], names)
    if names is FASTAPI:
        assert "sage.Markdown" in block and "static/sage/markdown.js" in block
    else:
        assert "<Markdown text=" in block and "src/Markdown.tsx" in block
    assert "dangerouslySetInnerHTML" in block and "pre-wrap" in block
