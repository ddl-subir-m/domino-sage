"""#121 — a platform error Sage passed through is drawn as a quotation.

Text Sage did not write keeps its words (ADR-0014), so a screen will sometimes show two
vocabularies at once: the pack's words in Sage's own sentence, the platform's words in the body it
handed back. That is correct, and the quotation is what makes it read as attribution rather than as
a half-finished rename. It is the design system's own split — a system error human-readable with a
reason and a resolution, raw output shown as it came.

The alternative, rewriting the platform's words inside the body, is the response-time filter
ADR-0014 rejects: nothing downstream can tell our word for the platform from a Data Source a user
named after the company, or from a line of their own SQL echoed back inside the error.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
_CSS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "css"
COMPONENT = (_JS / "components" / "platform-error.js").read_text()
GALLERY = (_JS / "modes" / "gallery.js").read_text()
TREE = (_JS / "components" / "resource-tree.js").read_text()
DOOR = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "door.html").read_text()
TOKENS = (_CSS / "tokens.css").read_text()
BOOT = (_JS / "app.js").read_text()

# What a real one looks like: Domino's own words, its own nouns, and a body long enough to widen a
# page if nothing contains it.
UPSTREAM = (
    'POST /v4/projects -> 500: {"error":"Domino could not create the project",'
    '"dataset":"domino-demo","trace":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
)


def _render(component: str, props: dict) -> list[dict]:
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH (it is in the Sage image)")
    harness = Path(__file__).resolve().parent / "js" / "platform_error_harness.mjs"
    out = subprocess.run(
        ["node", str(harness)],
        input=json.dumps({"component": component, "props": props}),
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_platform_body_lands_in_its_own_marked_block():
    nodes = _render("PlatformError", {"body": UPSTREAM})
    quoted = [n for n in nodes if n["className"] == "sw-passthrough"]
    assert len(quoted) == 1
    assert quoted[0]["tag"] == "blockquote"
    assert nodes[nodes.index(quoted[0]) + 1]["tag"] == "pre"


def test_the_platform_keeps_its_own_words():
    """The one thing the overlay must not do here. A filter would rewrite `domino-demo` too."""
    nodes = _render("PlatformError", {"body": UPSTREAM})
    assert [n["text"] for n in nodes if n["tag"] == "pre"] == [UPSTREAM]


def test_sages_sentence_stays_outside_the_quotation():
    nodes = _render("PlatformError", {
        "reason": "Acme couldn’t create the project.",
        "body": UPSTREAM,
        "fix": "Try again, then check your permissions in Acme Cloud.",
    })
    quoted = [n for n in nodes if n["className"] == "sw-passthrough"]
    assert len(quoted) == 1
    ours = " ".join(n["text"] for n in nodes if n["tag"] != "pre")
    assert "Acme couldn’t create the project." in ours  # the reason
    assert "check your permissions in Acme Cloud." in ours  # the resolution step
    assert UPSTREAM not in ours


def test_nothing_is_quoted_when_the_platform_said_nothing():
    """An empty quotation is a box that means the platform was silent when it was not asked."""
    for empty in ("", None):
        nodes = _render("PlatformError", {"reason": "Acme couldn’t list them.", "body": empty})
        assert not [n for n in nodes if n["className"] == "sw-passthrough"]
        assert not [n for n in nodes if n["tag"] == "pre"]


def test_the_quotation_is_contained_rather_than_widening_the_page():
    """Laptop and wide monitor both: a 500-character body scrolls inside its own box."""
    block = TOKENS.split(".sw-passthrough")[1].split("}")[0]
    assert "max-width: 100%" in block
    body = TOKENS.split(".sw-passthrough pre")[1].split("}")[0]
    assert "overflow-x: auto" in body
    assert "overflow-wrap: anywhere" in body


def test_every_surface_that_draws_a_platform_error_inline_uses_the_one_treatment():
    """Not one component: the same block wherever a passed-through body is drawn to be read.

    A toast is deliberately not one of them. It is a corner that auto-dismisses in seconds, which
    is the wrong placement for output somebody has to read, so those stay one-line summaries.
    """
    assert "SW.PlatformError" in GALLERY
    assert "SW.PlatformError" in BOOT  # the shell's own "could not load", same shape
    assert TREE.count("treeFailure(") == 3  # one shape, called by both trees
    assert "sw-passthrough" in DOOR  # the door carries its own copy — it loads no shell JS
    assert '"./js/components/platform-error.js"' in (
        (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "index.html").read_text()
    )


def test_the_copy_around_the_quotation_comes_from_the_pack():
    """Sage's half re-brands even though the platform's half must not."""
    assert "Sage couldn’t list the Built Apps" not in GALLERY
    assert "SW.brand.text(" in GALLERY
    assert "Could not list files." not in TREE
    assert "Could not look inside this Data Source." not in TREE
    # The door loads no shell JS, so it reads the pack it already fetches for its logo and colours.
    assert "'Sage couldn’t open your workspace'" not in DOOR
    assert "pack.assistantName" in DOOR
