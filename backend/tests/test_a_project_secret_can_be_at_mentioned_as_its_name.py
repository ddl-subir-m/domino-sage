"""A Project secret is offered by the composer's @ menu and goes in as `{env:NAME}` (#643).

Picking one attaches nothing: the model sees the name, and the value never leaves Domino. The
reference is drawn as a chip naming the secret, in the box and in a sent message. Driven through
`tests/js/mention_secret_harness.mjs`, which records every request the composer sends.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "mention_secret_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(mode: str, query: str, available: bool = True, **kw) -> dict:
    payload = {"mode": mode, "query": query, "available": available, **kw}
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(payload), capture_output=True,
                         text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("mode", ["chat", "build"])
def test_the_menu_offers_a_secret_and_picking_it_writes_its_name(mode):
    got = _run(mode, "OPEN")
    names = [r["name"] for r in got["rows"]]
    assert "OPENAI_API_KEY" in names and "openings" in names and "CRM_TOKEN" not in names
    assert got["inserted"] == "Call it with {env:OPENAI_API_KEY}"
    assert got["posts"] == []


def test_where_secrets_cannot_be_kept_none_is_offered():
    got = _run("chat", "OPEN", available=False)
    assert "OPENAI_API_KEY" not in [r["name"] for r in got["rows"]]


def test_the_box_draws_the_reference_as_a_chip_over_the_same_characters():
    got = _run("chat", "OPEN")
    assert got["field"] == "sw-composer-field has-secret-refs"
    # Every character of the draft is still in the mirror, so it lines up with the box over it.
    assert got["mirror"] == {"text": "Call it with {env:OPENAI_API_KEY}", "chips": ["OPENAI_API_KEY"]}


def test_a_draft_with_no_reference_draws_no_mirror():
    got = _run("chat", "nothing-matches-this")
    assert got["field"] == "sw-composer-field" and got["mirror"] is None


def test_a_sent_message_shows_the_secret_by_name_not_as_braces():
    got = _run("chat", "OPEN", markdown="Use {env:OPENAI_API_KEY} here. Literally: `{env:RAW}`")
    assert got["message"]["chips"] == ["OPENAI_API_KEY"]
    assert "{env:OPENAI_API_KEY}" not in got["message"]["text"]
    # Quoted in backticks it is code, and stays as written.
    assert got["message"]["code"] == ["{env:RAW}"]
