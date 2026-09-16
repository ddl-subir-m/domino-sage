"""The browser half of #233: the offer is drawn where `behind` is drawn, and pressing it reverts.

Two harnesses, because there are two claims and they fail apart. `build_header_harness` answers
WHERE the sentence and the button are and what they say — the app rail row carries `resolvedMerge`,
so the header can draw it beside `Changes to pull` without a read of its own. `undo_merge_harness`
answers what the click DOES: one POST, then a reload of the app list whichever way the server
answered, because the offer is derived on the server and a refusal retires it just as an undo does.

See ADR-0053, and `test_a_resolved_merge_can_be_undone.py` for the server side.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HEADER = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"
_UNDO = Path(__file__).resolve().parent / "js" / "undo_merge_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)")

MERGE = {"sha": "abc123def456", "files": ["apps/app_d/src/App.tsx", "README.md", "src/x.ts"],
         "count": 3}


def _run(harness: Path, payload) -> list:
    out = subprocess.run(["node", str(harness)], input=json.dumps(payload), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# --- what the header says ------------------------------------------------------------------------

@needs_node
def test_a_merge_nobody_read_is_said_beside_the_app_it_is_about():
    """The sentence and the Undo are one offer. The file names are what make it one: "a merge
    happened" is nothing a person can weigh, and the names are the only part of it they can
    recognise as their own work or not."""
    plain, offered = _run(_HEADER, [
        {"build": "thr_one", "select": "app_d"},
        {"build": "thr_one", "select": "app_d", "stamps": {"app_d": {"resolvedMerge": MERGE}}},
    ])

    assert not any("combined incoming changes" in w for w in plain["words"])
    said = " ".join(offered["words"])
    assert ("combined incoming changes in apps/app_d/src/App.tsx, README.md and 1 more"
            " — it chose how") in said
    assert "Undo" in offered["words"]
    # Its own mark, not `behind`'s: work that already landed unread is a different fact from work
    # waiting to come in, and the two are said in the same 44px strip.
    assert "sw-build-state is-merged" in offered["classes"]


@needs_node
def test_the_full_list_and_what_undo_will_do_are_on_the_tooltip():
    """The strip is 44px and the app's name has to stay the heaviest thing in it, so the names are
    capped there and carried whole here — `sw-build-others` a few lines up does the same. A person
    deciding whether to undo needs the sha, because the Workbench cannot show them a diff (#366)."""
    offered = _run(_HEADER, [
        {"build": "thr_one", "select": "app_d", "stamps": {"app_d": {"resolvedMerge": MERGE}}},
    ])[-1]

    tip = next(t for t in offered["titles"] if "resolved the merge conflicts" in t)
    for named in MERGE["files"]:
        assert named in tip
    assert MERGE["sha"] in tip
    # What the undo COSTS, and the reason this assertion is here rather than a looser one:
    # reverting a merge does not re-arm the pull, so "the changes can be taken again with Pull and
    # build" reads well and is false. The person deciding whether to drop somebody else's work is
    # the one who must not be told it.
    assert "will not bring it back" in tip
    assert "can be taken again" not in tip


@needs_node
def test_an_app_with_no_unread_merge_is_not_told_it_has_one():
    """The paired assertion, and it has to be its own test: a header that draws the offer
    unconditionally passes the one above."""
    plain = _run(_HEADER, [{"build": "thr_one", "select": "app_a"}])[-1]

    assert "sw-build-state is-merged" not in plain["classes"]
    assert "Undo" not in plain["words"]


# --- what the click does -------------------------------------------------------------------------

@needs_node
def test_the_click_posts_once_and_rereads_the_row():
    pressed = _run(_UNDO, {"cases": [{"merge": MERGE}]})[-1]

    # Both reads, like `pullAndBuild`'s: the list retires the offer, and the build state is what
    # the revert just rewrote under the selected app.
    assert sorted(pressed["calls"]) == ["apps", "loadBuild", "undoMerge"]
    assert pressed["calls"][0] == "undoMerge"
    assert pressed["thrown"] == ""
    # The server stopped offering it, so the button goes with the server's answer rather than being
    # dismissed in the browser. Nothing here decides the offer is over.
    assert pressed["offerAfter"] is None


@needs_node
def test_a_refusal_is_reported_and_still_rereads_the_row():
    """A revert that will not apply refuses (rule five). The reload happens anyway: the offer is
    derived, so it may have gone out between the render and the click, and a refusal that skipped
    the reload would leave a button pointing at nothing."""
    refused = _run(_UNDO, {"cases": [{
        "merge": MERGE,
        "answer": {"ok": False, "sha": "abc123def456", "pushed": False, "rejected": False,
                   "detail": "Sage couldn't undo merge abc123def456 — work committed since then "
                             "changes the same code. Nothing here has changed.",
                   "pushDetail": ""},
        "rowsAfter": [{"id": "app_a", "name": "Desk dashboard", "selected": True,
                       "resolvedMerge": MERGE}],
    }]})[-1]

    assert sorted(refused["calls"]) == ["apps", "loadBuild", "undoMerge"]
    assert "abc123def456" in refused["thrown"]
    assert refused["offerAfter"] is not None      # still the merge nobody read


@needs_node
def test_a_rejected_push_reuses_the_sentence_pull_and_build_already_ships():
    """#347's sentence, not a second one. Both acts end in a push and the failure is the same fact
    under both: committed here, not on the remote."""
    rejected = _run(_UNDO, {"cases": [{
        "merge": MERGE,
        "answer": {"ok": True, "sha": "abc123def456", "pushed": False, "rejected": True,
                   "detail": "undid the merge", "pushDetail": "push failed: non-fast-forward"},
    }]})[-1]

    assert "committed locally and not on the remote" in rejected["thrown"]
    assert "non-fast-forward" in rejected["thrown"]
    # No control has ever been called "Pull latest" (CONTEXT.md).
    assert "Pull latest" not in rejected["thrown"]
    # The remedy is the undo's own. "Try Pull and build again" is the PULL's way out and is wrong
    # here: the revert is local, so the offer this came from is already gone and there is no undo
    # left to press. Sharing the whole sentence would have shipped that.
    assert "will carry the undo with it" in rejected["thrown"]
    assert "Try Pull and build again" not in rejected["thrown"]


@needs_node
def test_a_running_build_is_told_to_stop_first():
    """The server takes the turn lock and would answer 409, but that is a round trip to learn
    something already on screen."""
    busy = _run(_UNDO, {"cases": [{"merge": MERGE, "buildRunning": True}]})[-1]

    assert busy["calls"] == []
    assert "build is running" in busy["thrown"].lower()
