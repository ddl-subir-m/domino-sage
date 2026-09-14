"""#234: a rejected push is committed, not pushed — and must not be DRAWN like a plain save.

Both halves of the bug live in the browser: `store.js` turns the `saved` event into a status
block, and `message-blocks.js` turns that block into a className. `saved_status_harness.mjs`
drives a real `saved` event through `store.loadBuild()` and mounts the real `MessageBlock`
renderer on the block it produced, so a fix to only one file cannot pass this alone.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "saved_status_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


def _drawn(saved_event: dict) -> dict:
    history = [{"type": "user", "text": "add a chart"}, saved_event]
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"history": history}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    rows = json.loads(out.stdout.strip().splitlines()[-1])["drawn"]
    assert len(rows) == 1
    return rows[0]


@needs_node
def test_a_rejected_push_is_not_styled_as_a_plain_save():
    row = _drawn({"type": "saved", "ok": True, "pushed": False, "rejected": True,
                  "detail": "push failed: ! [rejected] main -> main (non-fast-forward)"})

    assert row["className"] == "sw-status-line is-warn"
    assert row["className"] != "sw-status-line"  # not drawn as a plain, unstyled save


@needs_node
def test_a_rejected_push_tells_the_person_their_work_is_local_and_what_to_do():
    row = _drawn({"type": "saved", "ok": True, "pushed": False, "rejected": True,
                  "detail": "push failed: ! [rejected] main -> main (non-fast-forward)"})

    # Committed locally, not on the remote, with a real next step — not just the raw git error.
    assert "not pushed" in row["text"] or "not on" in row["text"]
    assert "pull" in row["text"].lower()
    # The raw git stderr is still reachable, but it must not be the lead of the sentence.
    assert "rejected" in row["text"]
    assert not row["text"].startswith("push failed")


@needs_node
def test_no_changes_to_commit_still_reads_as_a_plain_save():
    """Regression pin (#234): this is the trap named in the ticket — "ok cannot simply become
    pushed" must not make the genuinely-fine "nothing to commit" case draw as a warning either."""
    row = _drawn({"type": "saved", "ok": True, "pushed": False,
                  "detail": "no changes to commit"})

    assert row["className"] == "sw-status-line"
    assert "no changes" in row["text"]


@needs_node
def test_a_successful_push_is_unaffected():
    row = _drawn({"type": "saved", "ok": True, "pushed": True, "detail": "pushed"})

    assert row["className"] == "sw-status-line"
    assert row["text"] == "Saved and pushed"


@needs_node
def test_a_sync_conflict_still_reads_as_an_error():
    row = _drawn({"type": "saved", "ok": False, "pushed": False,
                  "detail": "couldn't sync with the repo — markers left in App.tsx"})

    assert row["className"] == "sw-status-line is-err"
