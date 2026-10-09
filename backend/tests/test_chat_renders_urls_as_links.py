"""A URL in model text renders as a link that opens in a new tab (#734).

A Chat answer listing its sources gave the reader plain text, to be copied one URL at a time into
a browser. `SW.util.inline` is the one renderer for model text — Chat answers, Build transcripts,
plan bodies and choice-card prompts all reach it through `SW.util.markdown` — so it now draws a
bare http(s) URL and a markdown link as an anchor. Any other scheme stays text: an anchor whose
href a model wrote is a click away from `javascript:`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "js" / "chat_links_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

NEW_TAB = {"target": "_blank", "rel": "noopener noreferrer"}


def render(text: str) -> dict:
    out = subprocess.run(
        ["node", str(HARNESS)], input=json.dumps({"text": text}),
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(out.stdout)


def link(href: str, text: str | None = None) -> dict:
    return {"href": href, "text": href if text is None else text, **NEW_TAB}


def test_a_bare_url_and_a_markdown_link_both_render_as_anchors():
    got = render("Sources: https://www.reuters.com/deals/acme-1 and "
                 "[Bloomberg](https://www.bloomberg.com/news/acme)")
    assert got["links"] == [
        link("https://www.reuters.com/deals/acme-1"),
        link("https://www.bloomberg.com/news/acme", "Bloomberg"),
    ]
    assert got["plain"] == "Sources:  and "


def test_a_plain_http_url_is_a_link_too():
    assert render("see http://example.com/a?b=1&c=2#d")["links"] == [
        link("http://example.com/a?b=1&c=2#d")]


@pytest.mark.parametrize("text", [
    "javascript:alert(document.cookie)",
    "[click me](javascript:alert(document.cookie))",
    "[click me](JavaScript:alert(1))",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "[open](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    "[mail](mailto:a@b.com)",
    "[relative](/api/secrets)",
    "[file](file:///etc/passwd)",
])
def test_a_url_that_is_not_http_stays_text_as_written(text):
    got = render(text)
    assert got["links"] == []
    assert got["plain"] == text


@pytest.mark.parametrize("text, href, plain", [
    ("Read https://example.com/a.", "https://example.com/a", "Read ."),
    ("(see https://example.com/a)", "https://example.com/a", "(see )"),
    ("Is it https://example.com/a?", "https://example.com/a", "Is it ?"),
    ("At https://example.com/a, then", "https://example.com/a", "At , then"),
    ('"https://example.com/a"', "https://example.com/a", '""'),
    ("Wiki https://en.wikipedia.org/wiki/Merger_(business).",
     "https://en.wikipedia.org/wiki/Merger_(business)", "Wiki ."),
])
def test_trailing_punctuation_is_not_part_of_a_bare_url(text, href, plain):
    got = render(text)
    assert got["links"] == [link(href)]
    assert got["plain"] == plain


def test_a_markdown_link_with_parens_in_its_url_keeps_them():
    got = render("[Merger](https://en.wikipedia.org/wiki/Merger_(business)).")
    assert got["links"] == [link("https://en.wikipedia.org/wiki/Merger_(business)", "Merger")]
    assert got["plain"] == "."


def test_a_url_quoted_as_code_stays_code():
    got = render("Call `https://api.example.com/v1` with a token.")
    assert got["links"] == []


def test_links_inside_a_list_a_table_and_bold():
    got = render("- **[Reuters](https://reuters.com/x)**\n- https://ft.com/y\n\n"
                 "| Source | URL |\n|---|---|\n| WSJ | https://wsj.com/z |")
    assert [x["href"] for x in got["links"]] == [
        "https://reuters.com/x", "https://ft.com/y", "https://wsj.com/z"]
    assert all(x["target"] == "_blank" and x["rel"] == "noopener noreferrer" for x in got["links"])


def test_text_with_no_url_is_untouched():
    text = "Revenue was $1.2M [est.] (approx), see appendix: https or http alone is not a link."
    got = render(text)
    assert got["links"] == []
    assert got["plain"] == text
