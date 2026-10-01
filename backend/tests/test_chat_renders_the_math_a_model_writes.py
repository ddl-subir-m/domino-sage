"""Chat typesets the TeX a model writes instead of showing its source.

A model reporting a KL divergence writes `$D_{\\text{KL}}(P \\parallel Q)$`, and the Workbench's
markdown had no idea what that was, so the reader got the backslashes. KaTeX is vendored beside
the other bundles and `SW.util.inline` hands it the four delimiters models use. The hard half is a
dollar sign that is money: Sage's own copy writes "$1.2M", and a reader asking about prices
writes "$5 to $10", neither of which may turn into italics.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "js" / "chat_math_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def render(text: str, katex: bool = True) -> dict:
    out = subprocess.run(
        ["node", str(HARNESS)], input=json.dumps({"text": text, "katex": katex}),
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(out.stdout)


def test_the_reported_line_is_typeset():
    line = (r"Empirical Binned KL Divergence $D_{\text{KL}}(P_{\text{recent}} \parallel "
            r"Q_{\text{prior}})$: `0.5816 nats` (0.8390 bits)")
    got = render(line)
    assert got["math"] == [{
        "display": False,
        "tex": r"D_{\text{KL}}(P_{\text{recent}} \parallel Q_{\text{prior}})",
        "error": False,
    }]
    assert got["code"] == ["0.5816 nats"]
    assert "$" not in got["plain"] and "\\" not in got["plain"]
    assert got["plain"] == "Empirical Binned KL Divergence :  (0.8390 bits)"


@pytest.mark.parametrize("text, display, tex", [
    (r"so $x^2$ grows", False, "x^2"),
    (r"so \(x^2\) grows", False, "x^2"),
    ("$$\n\\sum_i p_i \\log \\frac{p_i}{q_i}\n$$", True, "\\sum_i p_i \\log \\frac{p_i}{q_i}"),
    (r"\[ \sigma = 1 \]", True, r"\sigma = 1"),
])
def test_each_delimiter_a_model_uses(text, display, tex):
    got = render(text)
    assert [(m["display"], m["tex"].strip()) for m in got["math"]] == [(display, tex)]


@pytest.mark.parametrize("text", [
    "Prices run from $5 to $10 a seat.",
    "Revenue was $1.2M, against $3M last year.",
    "It moved $5-$10 overnight.",
    r"Escaped \$x\$ is not math.",
    "A lone $ sign.",
])
def test_a_dollar_that_is_money_stays_prose(text):
    got = render(text)
    assert got["math"] == []
    assert got["plain"] == text


def test_tex_quoted_as_code_stays_code():
    got = render("Write `$x^2$` to get a square.")
    assert got["math"] == []
    assert got["code"] == ["$x^2$"]


def test_math_inside_a_table_cell_and_a_list_item():
    got = render("| metric | value |\n|---|---|\n| $\\alpha$ | 0.05 |\n\n- $\\beta = 0.2$")
    assert [m["tex"] for m in got["math"]] == [r"\alpha", r"\beta = 0.2"]


def test_bad_tex_is_shown_as_an_error_not_a_crash():
    got = render(r"Broken $\frac{1}{$ here")
    assert [m["error"] for m in got["math"]] == [True]


def test_without_katex_on_the_page_the_source_is_left_as_written():
    text = r"Divergence $D_{\text{KL}}$ is 0.58."
    got = render(text, katex=False)
    assert got["math"] == []
    assert got["plain"] == text


def test_the_page_loads_katex_from_its_own_origin():
    page = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "index.html").read_text()
    assert './vendor/katex/katex.min.js' in page
    assert './vendor/katex/katex.min.css' in page


def test_katex_fonts_are_served_as_fonts():
    """mimetypes has no answer for .woff2 on a bare Linux image, as `font()` already notes."""
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    r = TestClient(appmod.control_app).get("/vendor/katex/fonts/KaTeX_Main-Regular.woff2")
    assert r.status_code == 200
    assert r.headers["content-type"] == "font/woff2"
    assert r.headers["cache-control"] == "no-cache"
