"""A slow older answer never replaces a newer selection, and a remote search waits for a pause (#699).

Symptom: in a generated app, typing in a remote search sent one query per keystroke, and a slow
older response for the same key could overwrite a newer one. Region A, then B, then A again: the
first A's request was aborted, but an answer can still arrive after its abort, and it landed last —
on screen and in the page cache, so the next visit drew it too. Aborting the old fetch is not
enough; only the request a hook is still waiting on may change what it shows.

The hooks run from the templates' own files through a minimal hooks runtime, with each request held
until the test releases it and with fake time. Every Node process here is `subprocess.run`, which
waits for it to exit.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.resources.app_helpers import FASTAPI
from sage.resources.app_helpers import TEMPLATE as TEMPLATE_NAMES
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.resources.bound_schema import BoundSource, agents_block

HARNESS = Path(__file__).parent / "js" / "app_query_records_harness.mjs"
TEMPLATES = ["fastapi-antd", "react-vite"]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def _answer(owner: str) -> dict:
    return {"body": {"columns": ["OWNER"], "rows": [[owner]], "truncated": False}}


def _drive(template: str, steps: list, *, answers=(), hook="useQuery", abort_rejects=False) -> dict:
    out = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps({"template": template, "steps": steps, "answers": list(answers),
                          "hook": hook, "abortRejects": abort_rejects}),
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(out.stdout)


def _owners(snapshot: dict) -> list | None:
    return None if snapshot["records"] is None else [r["OWNER"] for r in snapshot["records"]]


# ---- request identity ----------------------------------------------------------------------------

# Region A, then B, then A again. Requests 0 (old A), 1 (B) and 2 (new A) are all in flight; each
# answers with its own marker, and an aborted request still answers.
A_B_A = [{"mount": ["q", {"region": "A"}]}, {"props": ["q", {"region": "B"}]},
         {"props": ["q", {"region": "A"}]}]
A_B_A_ANSWERS = [_answer("old A"), _answer("B"), _answer("new A")]


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_first_a_answering_last_does_not_replace_the_newest_a(template: str):
    got = _drive(template, [*A_B_A, {"resolve": 2}, {"resolve": 1}, {"resolve": 0}],
                 answers=A_B_A_ANSWERS)
    assert [_owners(s) for s in got["snapshots"][3:]] == [["new A"], ["new A"], ["new A"]]
    assert got["snapshots"][-1]["status"] == "ready"


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_page_cache_keeps_the_newest_a_too(template: str):
    """The cache is what the next visit draws, so a late answer written there is a stale screen
    that outlives this one."""
    got = _drive(template, [*A_B_A, {"resolve": 2}, {"resolve": 1}, {"resolve": 0}, "unmount",
                            {"mount": ["q", {"region": "A"}]}],
                 answers=A_B_A_ANSWERS)
    assert _owners(got["snapshots"][-1]) == ["new A"]
    assert len(got["requests"]) == 3          # the remount drew from the cache


@pytest.mark.parametrize("template", TEMPLATES)
def test_an_answer_that_arrives_after_its_abort_does_not_reach_the_screen(template: str):
    got = _drive(template, [{"mount": ["q", {"region": "A"}]}, {"props": ["q", {"region": "B"}]},
                            {"resolve": 0}, {"props": ["q", {"region": "A"}]}],
                 answers=[_answer("A"), _answer("B"), _answer("A again")])
    late, back = got["snapshots"][2:]
    assert got["requests"][0]["aborted"] is True
    assert (late["status"], _owners(late)) == ("loading", None)
    # Nor into the cache: going back to A asks again rather than drawing the abandoned answer.
    assert (back["status"], len(got["requests"])) == ("loading", 3)


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_superseded_request_that_fails_is_not_the_screens_error(template: str):
    """An abort can surface as something other than an AbortError — a body read cut short is a
    TypeError — and it is still the request going stale, not the data failing."""
    got = _drive(template, [{"mount": ["q"]}, "refresh", {"reject": [0, "body stream aborted"]},
                            {"resolve": 1}],
                 answers=[_answer("first"), _answer("second")])
    during, after = got["snapshots"][2], got["snapshots"][3]
    assert (during["status"], during["error"]) == ("loading", None)
    assert (after["status"], _owners(after)) == ("ready", ["second"])


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_current_requests_failure_is_still_shown(template: str):
    """The other direction: ignoring stale answers must not swallow a real failure."""
    got = _drive(template, [{"mount": ["q"]}, {"reject": [0, "Failed to fetch"]}])
    assert got["snapshots"][-1]["status"] == "error"
    assert got["snapshots"][-1]["error"]


# ---- useDebouncedValue ---------------------------------------------------------------------------


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_initial_value_is_there_on_the_first_render(template: str):
    """A value restored from the URL must query at once, not 300 ms later."""
    got = _drive(template, [{"mount": ["acme"]}], hook="useDebouncedValue")
    assert got["snapshots"] == ["acme"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_rapid_changes_settle_once_after_the_pause(template: str):
    steps = [{"mount": [""]}]
    for text in ("a", "ac", "acm", "acme"):
        steps += [{"props": [text]}, {"advance": 100}]
    steps += [{"advance": 199}, {"advance": 1}, {"advance": 1000}]
    got = _drive(template, steps, hook="useDebouncedValue")
    *typing, before, settled, later = got["snapshots"][1:]
    assert set(typing) == {""}
    assert (before, settled, later) == ("", "acme", "acme")


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_delay_can_be_given(template: str):
    got = _drive(template, [{"mount": ["", 50]}, {"props": ["x", 50]}, {"advance": 49},
                            {"advance": 1}], hook="useDebouncedValue")
    assert got["snapshots"][2:] == ["", "x"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_unmounting_leaves_no_timer_behind(template: str):
    got = _drive(template, [{"mount": [""]}, {"props": ["a"]}, "unmount"], hook="useDebouncedValue")
    assert got["timers"] == 0


# ---- the canonical guidance ----------------------------------------------------------------------


def _block(names) -> str:
    binding = Binding(KIND_DATA_SOURCE, "ds-dwh", "warehouse", "warehouse",
                      "DWH", "MARTS", None, "SnowflakeConfig")
    return " ".join(agents_block([BoundSource(binding, [], [], None)], [], 5000, names=names).split())


STACKS = pytest.mark.parametrize("names", [FASTAPI, TEMPLATE_NAMES], ids=TEMPLATES)


@STACKS
def test_the_guidance_delays_a_remote_search_and_applies_other_controls_at_once(names):
    block = _block(names)
    call = "sage.useDebouncedValue(" if names.ext == "js" else "useDebouncedValue("
    assert call in block
    assert "Selects and toggles apply immediately" in block
    assert "Enter" in block and "composition" in block


@STACKS
def test_the_guidance_says_only_the_newest_answer_reaches_the_screen(names):
    block = _block(names)
    assert "only the newest request's answer reaches the screen or the cache" in block
    assert "an aborted request is not an error" in block


@STACKS
def test_the_guidance_shapes_each_query_for_what_it_feeds(names):
    block = _block(names)
    assert "tie-breaker" in block and "OFFSET :offset" in block
    assert "aggregate over every matching row in SQL" in block
    assert "`SELECT DISTINCT`" in block
    assert "rows shown" in block
    # "All" stays the sentinel; no unfiltered twin of a query.
    assert "WHERE (:region = '__all__' OR region = :region)" in block
