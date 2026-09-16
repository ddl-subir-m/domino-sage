"""A complete Recall clear takes `findings.md` with it; a summary-scoped one does not (ADR-0055).

Clearing Recall drops the OpenCode session, and until #380 that was the whole of what a turn is
told. It is not any more: `.sage/threads/<threadId>/findings.md` is reachable from the turn's cwd,
the pinned prompt carves it out of the `.sage/` bans, and the turn prompt points the model at it
before it plans. So the clear stopped clearing — the session went, the model restarted from
`recall.seed` alone, and the very next turn read every measurement straight back off disk. Against
ADR-0022, which is the promise that the button means what it says.

The scope is the whole decision, and it cuts both ways. A complete clear is the person saying
START OVER and it has to mean it. A summary-scoped clear trims talk and seeds the model with what
was said — and a measurement log is not talk, so taking it there would throw away work nobody
asked to lose on the softer rung.

A delete is the third condition and it is inherited rather than written: `purge` already removes
every file beside the tombstone (ADR-0036). Pinned here anyway, because "the Conversation is gone
and its measurements are not" is the same failure wearing a different door.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator import recall
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore, findings_file

# The shape §2.3 of #378 requires: a measurement with its time, its denominator and the object it
# is about. What makes the file worth keeping for a week is exactly what makes it worth clearing.
MEASURED = "2026-09-16T11:04Z — DWH.MARTS.ACCOUNT.SFDC_CONTACT_ID populated 16,756/89,399 (18.7%)\n"


def _orch(tmp: Path) -> Orchestrator:
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("placeholder")
    (template / "package.json").write_text("{}")
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=template,
        gateway=object(),  # never called: no turn runs here
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
    )
    orch.project(start_preview=False, seed_app=False)
    return orch


def _store(orch: Orchestrator) -> ThreadStore:
    return ThreadStore(orch.project(start_preview=False, seed_app=False).record.path)


def _findings(orch: Orchestrator, thread_id: str) -> Path:
    """Through `findings_file`, the one definition of this path (#381) — so a test cannot pass by
    agreeing with a spelling the product stopped using."""
    return findings_file(orch.project(start_preview=False, seed_app=False).record.path, thread_id)


def _investigating(orch: Orchestrator, title: str = "Which accounts are at risk") -> str:
    """A Conversation part-way through an investigation: a session to clear and a file of
    measurements to decide about."""
    tid = _store(orch).create(title)["id"]
    _store(orch).write_session_id(tid, "ses_poisoned", directory="/mnt/code/.sage/chat-work")
    _findings(orch, tid).write_text(MEASURED)
    return tid


def test_a_complete_clear_takes_the_findings_with_it(tmp_path: Path):
    orch = _orch(tmp_path)
    tid = _investigating(orch)

    orch.clear_recall(tid, recall.EMPTY)

    assert not _findings(orch, tid).exists()


def test_a_complete_clear_still_drops_the_session(tmp_path: Path):
    """The half that was always there, pinned because the new branch is inserted directly above it.
    An edit that returned early out of the `EMPTY` branch, or moved the session drop under an
    `else`, leaves every other test here green while a complete clear stops clearing Recall."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _investigating(orch)

    orch.clear_recall(tid, recall.EMPTY)

    assert store.read_session(tid) is None


def test_an_unlink_that_raises_leaves_the_clear_undone_rather_than_half_done(tmp_path: Path):
    """Why the unlink runs AHEAD of the session drop.

    `missing_ok=True` covers the absent file and nothing else — a read-only volume, or a directory
    standing at the name, raises. The route catches only `KeyError` and `ValueError`, so either way
    the person gets a 500; what differs is what is left behind. Ordered the other way, that raise
    leaves the session dropped, the findings kept and NO `recall.CLEARED` row — and the ladder is
    derived from those rows (`recall.offer`), so the rung the person just spent is offered again
    and `recall.terminal` never fires. Ordered this way, nothing has changed and a retry is honest.

    The directory is the portable way to make `unlink` raise: Linux answers `IsADirectoryError`
    and macOS `PermissionError`, both `OSError`.
    """
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _investigating(orch)
    findings = _findings(orch, tid)
    findings.unlink()
    findings.mkdir()

    with pytest.raises(OSError):
        orch.clear_recall(tid, recall.EMPTY)

    assert store.read_session(tid) is not None          # Recall was not half-cleared
    assert list(store.read_history(tid)) == []          # and the ladder counted nothing


def test_a_summary_scoped_clear_keeps_the_findings(tmp_path: Path):
    """The rung below. It seeds the model with what was said rather than emptying it, and the
    measurement log is not what was said."""
    orch = _orch(tmp_path)
    tid = _investigating(orch)

    orch.clear_recall(tid, recall.SUMMARY)

    assert _findings(orch, tid).read_text() == MEASURED


def test_a_complete_clear_names_this_conversations_findings_and_no_other(tmp_path: Path):
    """Every Thread's record directory is a sibling of every other, and the id is what tells them
    apart. A clear that reached the wrong one would take an investigation nobody touched — and a
    test that only asked whether A FILE went away would pass while it did."""
    orch = _orch(tmp_path)
    cleared = _investigating(orch)
    bystander = _investigating(orch, title="Margin by desk")

    orch.clear_recall(cleared, recall.EMPTY)

    assert not _findings(orch, cleared).exists()
    assert _findings(orch, bystander).read_text() == MEASURED


def test_a_complete_clear_on_a_conversation_that_measured_nothing_still_clears(tmp_path: Path):
    """The common case: the ladder is climbed on a Conversation that never opened an
    investigation. There is no file, and that is not a failure — the row still lands."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = store.create("Why is this refused")["id"]
    store.write_session_id(tid, "ses_poisoned", directory="/mnt/code/.sage/chat-work")

    ev = orch.clear_recall(tid, recall.EMPTY)

    assert ev == {"type": recall.CLEARED, "scope": recall.EMPTY}
    assert [r["type"] for r in store.read_history(tid)] == [recall.CLEARED]


def test_a_deleted_conversation_takes_its_findings_with_it(tmp_path: Path):
    """Inherited from `purge` (ADR-0036) rather than written — pinned because the file is new and
    the rule it relies on is "everything but the tombstone", which a later exemption could quietly
    stop covering."""
    orch = _orch(tmp_path)
    store = _store(orch)
    tid = _investigating(orch)
    (store.thread_dir(tid) / "history.jsonl").write_text(json.dumps({"type": "user"}) + "\n")

    orch.delete_thread(tid)

    assert not _findings(orch, tid).exists()
    assert (store.thread_dir(tid) / "meta.json").exists()
