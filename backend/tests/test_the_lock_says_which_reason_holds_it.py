"""What the Workbench says when the lock is the conversation's and not the Bindings' (ADR-0043).

The lock has two reasons and only one of them can name a Dataset. While a declared Dataset is
bound, every sentence points at a row on the panel — which is the whole reason the Datasets are
named. Once a turn has run under the lock the transcript carries the rows, so the creator can unbind
the Dataset and the lock stays; from that moment every one of those sentences is false in the worst
available way. "This app reads the Dataset claims" sends them to a panel with no `claims` on it, to
remove something already gone, and they come back to a lock that has not moved.

So the sticky case gets its own sentences and, more importantly, the way out — which is not the one
anybody guesses. Unbinding is the obvious move and it is deliberately the wrong one.

What must NOT differ is who is refused: the reason changes the copy, never the approved set.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "sensitivity_session_lock_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

DECLARED = {"enabled": True, "locked": True, "approved": ["opus"], "datasets": ["claims"],
            "group": "sensitive-approved", "reason": "declared"}
# The same lock after the creator unbound the Dataset: still on, nothing left to name.
SESSION = {**DECLARED, "datasets": [], "reason": "session"}
# What an older server sends, and what a failed read leaves behind. Neither is the sticky lock.
NO_REASON = {**DECLARED, "datasets": []}
OFF = {"enabled": False, "locked": False, "approved": [], "datasets": [], "group": "",
       "reason": ""}


def _read(cases: list[dict]) -> list[dict]:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"cases": cases}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_sticky_lock_names_the_reading_and_not_a_dataset():
    """THE point. A Dataset named here is one the creator cannot find and cannot remove."""
    got = _read([{"sensitivity": SESSION}])[0]

    assert got["bySession"] is True
    assert got["phrase"] == "sensitive data"
    assert "claims" not in got["reason"]
    # The WHOLE sentence, not a substring of it. The phrase is an object and every sentence supplies
    # its own subject, and the first version of this carried one too — "this chat already used this
    # chat already used", which every substring assertion passed.
    assert got["reason"] == (
        "gpt-5.4 isn't allowed in this chat — it already used sensitive data. Ask an admin to "
        "approve it, or start a new chat."
    )


@needs_node
def test_the_sticky_lock_gives_the_only_way_out_there_is():
    """Not a guessable one. Every instinct says unbind the Dataset, and unbinding it does nothing —
    the rows are in the transcript, which is re-sent on every turn after them."""
    got = _read([{"sensitivity": SESSION}])[0]

    assert "Start a new chat" in got["wayOut"]
    assert "won't unlock" in got["wayOut"]
    assert "start a new chat" in got["reason"]


@needs_node
def test_a_bound_dataset_still_names_itself_and_offers_the_better_way_out():
    """Unchanged, and it has to stay unchanged: a creator whose Dataset IS bound has a way out that
    keeps the conversation, so offering them a new chat would be worse advice."""
    got = _read([{"sensitivity": DECLARED}])[0]

    assert got["bySession"] is False
    assert got["phrase"] == "the Dataset claims"
    assert got["reason"] == (
        "gpt-5.4 isn't allowed with the Dataset claims. Ask an admin to approve it, or remove "
        "the Dataset claims."
    )
    assert got["wayOut"] == ""


@needs_node
def test_the_reason_changes_the_sentence_and_never_the_approved_set():
    """The copy is the only thing that forks. A sticky lock that quietly let a barred model through
    would be the hole with better manners."""
    got = _read([{"sensitivity": DECLARED, "picked": "gpt-5.4"},
                 {"sensitivity": SESSION, "picked": "gpt-5.4"},
                 {"sensitivity": SESSION, "picked": "opus"}])

    assert [g["locked"] for g in got] == [True, True, True]
    assert [g["approved"] for g in got] == [False, False, True]


@needs_node
def test_an_empty_dataset_list_is_not_read_as_the_sticky_lock():
    """`reason` is the evidence, not an empty `datasets`. An older server and a failed read both
    look like that, and guessing draws the sticky sentence on a Project that never bound one."""
    got = _read([{"sensitivity": NO_REASON}, {"sensitivity": OFF}])

    assert [g["bySession"] for g in got] == [False, False]
    assert got[0]["wayOut"] == ""
    assert "already read" not in got[0]["phrase"]


# ---- the way out has to work ---------------------------------------------------------------------


def _way_out(session: dict, declared: dict) -> dict:
    out = subprocess.run(["node", str(_HARNESS)],
                         input=json.dumps({"wayOut": {"session": session, "declared": declared}}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_every_door_out_of_a_conversation_drops_its_session_lock_before_reading():
    """Three doors change which conversation is open, and a lock left behind by any of them draws
    conversation A's sentence — about rows A read — over conversation B.

    BEFORE the read, not left to it: `refreshSensitivity` deliberately leaves the last answer
    standing when a request fails, which is right for a Bindings lock (dropping it un-greys the
    picker while the Dataset is still attached) and wrong for this one, where the stale answer is
    about a different conversation entirely.
    """
    doors = _way_out(SESSION, DECLARED)["doors"]

    assert [d["name"] for d in doors] == ["newThread", "openThread", "clearConversation"]
    assert all(d["found"] for d in doors), doors      # the window was located, so the flags mean something
    assert all(d["drops"] for d in doors)
    assert all(d["beforeRead"] for d in doors)
    # `beforeRead` passes when a door makes no read at all, which is true of `clearConversation` and
    # is why the clause is there. So the two that DO read are named: without this, a read that moved
    # out of the harness's reach reads as a door in the clear. That happened — a comment grew and
    # pushed the read past a character window — and every assertion above stayed green.
    assert [d["reads"] for d in doors] == [True, True, False]


@needs_node
def test_an_armed_lock_loads_the_app_its_way_out_names():
    """The Chat lock's way out is the selected app's dependency list, so the notice has to name that
    app and find its Bindings. Neither was loaded on the Chat route: `init` reads `appAttachments`
    through `loadScopeData`, while `activeApp` and `bindings` had only ever been written by Build's
    own path. The notice drew, named the Dataset, and pointed nowhere.

    Loaded off the LOCK and not off the doors that open a Conversation, which is where it went
    first. Both doors read the lock, so either place worked for the pointer — but a door loads
    unconditionally, and `test_split_leaves_chat_exactly_as_it_is_today` is a promise that opening a
    Chat reads the Chat half and no rail list it has never needed. That promise holds for every
    deployment with the gate off, which is nearly all of them, so the cost belongs on the one state
    that has a use for the data.

    Awaited before the state is applied, so the notice arrives whole. Applied first, it would draw
    its sentence and sprout a way out a beat later — and `noticeKey` carries the app name, so a
    dismissal taken in that window would be undone by the arrival.
    """
    read = _way_out(SESSION, DECLARED)["lockRead"]
    assert read["found"], "refreshSensitivity was not found, so nothing below is about it"

    assert read["loadsApp"], "an armed lock loads nothing, so the pointer has no app to name"
    assert read["gatedOnLock"], "the load is unconditional, which taxes every Chat open"
    assert read["beforeApply"]
    # And no door does it, so the promise above cannot be broken from the other side. Read through
    # `found` for the same reason as the doors test: a renamed method scans a wrong region of
    # `store.js`, where `loadsApp` comes back False for a reason this line is not about.
    doors = _way_out(SESSION, DECLARED)["doors"]
    assert all(d["found"] for d in doors), doors
    assert [d["loadsApp"] for d in doors] == [False, False, False]


@needs_node
def test_starting_a_new_chat_takes_the_session_lock_off_the_screen():
    """The copy's one instruction, driven through the store that runs it. A lock still drawn over an
    empty screen makes "Start a new chat" look like it did nothing, and the picker stays greyed on
    the surface the person acts from.

    `clearConversation` is where it happens because that is what the button runs first, and it is
    synchronous and reaches no network — every other caller of it depends on that.
    """
    assert _way_out(SESSION, DECLARED)["session"] is None


@needs_node
def test_closing_a_conversation_leaves_a_bindings_lock_exactly_where_it_was():
    """The half that must NOT be dropped. The Dataset barring those models is still attached, so
    clearing here would put non-approved models back in the picker with nothing changed about the
    data — the stale-unlocked direction the store calls the dangerous one."""
    assert _way_out(SESSION, DECLARED)["declared"] == DECLARED
