"""ADR-0061 — the Chat turn's terminal row says what it wrote and whether that was new.

#442, measured live: eight consecutive turns of a stalled investigation closed
`{"ok":true,"decision":"answered"}`, with no error, no warning and nothing in the UI. On every
surface Sage records, those eight were identical to eight that worked. The person's only signal
was their own patience.

So `finish()` stamps two fields beside the existing `dataUsed`:

- `reads` — the result-Artifact paths this turn wrote, sorted;
- `advanced` — false when the turn wrote at least one path and EVERY path it wrote was already
  there when the turn started.

The "wrote at least one" guard is load-bearing and is tested on its own below: an empty set is a
subset of everything, so without it every ordinary conversational turn would report
`advanced: false` and the field would mean nothing.

Both are stamped on EVERY turn, never only when the condition fires. A field that appears only on
failure cannot tell a turn that passed from one that ran before the field existed — which is the
same reading problem #442 is about, one layer down.

No new `decision` value, and `answered` is untouched: #435 measured what a new one costs, because
`store.js` keys `NO_PLATFORM_FAULT` and `ASKED_FOR` on `decision` and a value missing from either
list regresses in silence.

The turns here run through `orch.chat_stream`, so the paths come from `new_artifact_paths` and the
baseline from the snapshot the turn already took — not from a fixture that says what those two
would have said.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.resources.provider import FakeResourceProvider

from .fake_opencode import FakeOpenCode, Turn
from .test_chat_turn import OkFeedback, ScriptedGateway, _catalog


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)


def _orch(tmp: Path):
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, [])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=FakeResourceProvider())
    orch.project(start_preview=False)
    return orch, oc


def _card(n: int) -> str:
    return json.dumps({"columns": ["N"], "rows": [[n]]})


def _turn(orch, oc, tid: str, ask: str, writes: dict[str, str] | None = None) -> dict:
    """One turn, returning its terminal row.

    Appended rather than assigned: `FakeOpenCode` walks its script with a pointer that does not
    rewind, so a second Thread turn handed a fresh one-element list gets the empty default and
    writes nothing — which looks exactly like the answer these tests are asking about.
    """
    oc.turns.append(Turn(text="Here you go.", writes=writes or {}))
    events = list(orch.chat_stream(tid, ask))
    return next(e for e in events if e["type"] == "done")


# ---- what the two fields say -------------------------------------------------------------------


def test_a_turn_that_writes_a_new_card_advanced(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    path = f"examples/{tid}/accounts.table.json"

    done = _turn(orch, oc, tid, "how many accounts?", {path: _card(16916)})

    assert done["reads"] == [path]
    assert done["advanced"] is True


def test_a_turn_that_only_rewrites_an_earlier_turns_card_did_not_advance(tmp_path: Path):
    """#442's shape: turn two re-runs the same read, `result.record` names the same file because
    it carries no uniquifier, and the person is handed the same number a second time."""
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    path = f"examples/{tid}/accounts.table.json"

    first = _turn(orch, oc, tid, "how many accounts?", {path: _card(16916)})
    again = _turn(orch, oc, tid, "are you sure?", {path: _card(16917)})

    assert first["advanced"] is True
    assert again["reads"] == [path]
    assert again["advanced"] is False


def test_one_new_card_beside_a_rewritten_one_still_advanced(tmp_path: Path):
    """EVERY path, not any. A turn that re-ran one read and answered something new did work."""
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    old = f"examples/{tid}/accounts.table.json"
    new = f"examples/{tid}/calls.table.json"

    _turn(orch, oc, tid, "how many accounts?", {old: _card(16916)})
    done = _turn(orch, oc, tid, "and the calls?", {old: _card(16917), new: _card(65033)})

    assert done["reads"] == sorted([new, old])
    assert done["advanced"] is True


def test_a_turn_that_wrote_nothing_advanced(tmp_path: Path):
    """The guard, on its own, because it is the thing that makes the field mean anything.

    An empty set is a subset of everything. Without "wrote at least one", every ordinary
    conversational turn — every greeting, every question answered in prose — would close
    `advanced: false`, and a field that fires on almost every turn reports nothing at all.
    """
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]

    done = _turn(orch, oc, tid, "what can you do?")

    assert done["reads"] == []
    assert done["advanced"] is True


# ---- stamped on every shape of turn ------------------------------------------------------------


def test_both_fields_are_on_a_turn_that_never_reached_the_model(tmp_path: Path):
    """The early exits go through `finish` too, and they are exactly the turns somebody is looking
    at because something went wrong. A row missing the fields cannot be told from a row written
    before the fields existed."""
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]

    done = _turn(orch, oc, tid, "hello")
    assert set(done) >= {"reads", "advanced"}

    failed = _turn(orch, oc, tid, "make me a table",
                   {f"examples/{tid}/broken.table.json": ""})
    assert failed["reads"] == [], "an invalid card is not a result this turn wrote"
    assert failed["advanced"] is True
    assert set(failed) >= {"reads", "advanced"}


def test_the_decision_is_left_alone(tmp_path: Path):
    """No new `decision` value. #435 measured the cost of adding one: `store.js` keys
    `NO_PLATFORM_FAULT` and `ASKED_FOR` on `decision`, so a value missing from either list
    regresses in silence. `advanced` is a field beside it."""
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    path = f"examples/{tid}/accounts.table.json"

    _turn(orch, oc, tid, "how many accounts?", {path: _card(16916)})
    again = _turn(orch, oc, tid, "are you sure?", {path: _card(16917)})

    assert again["advanced"] is False
    assert again["decision"] == "answered"
    assert again["ok"] is True


# ---- what it cannot see ------------------------------------------------------------------------


def test_reads_is_measured_against_the_turns_own_snapshot(tmp_path: Path):
    """The baseline is what makes "this turn wrote it" mean anything — and it also sets the limit.

    `new_artifact_paths` compares the tree against the snapshot the turn took before it ran, so a
    path whose contents did not move is not a path this turn wrote. Measure against an empty
    baseline instead and every card in the Thread becomes this turn's work, which is the plausible
    way to break this and is what this test catches.

    The limit that falls out of it, named rather than left in a passing total: a byte-identical
    rewrite is invisible. With **Kept rows** OFF that cannot happen for a live read —
    `table_shape.shape_only` stamps `readAt` per second, so re-running the same statement writes
    different bytes and the repeat IS caught. With **Kept rows** ON the card is
    `{title, columns, rows}` with no timestamp, and a repeat of an unchanged table lands here and
    reports `advanced: true`. The error falls in the honest direction — a missed report, never a
    false one — but it is a real hole and it belongs in the record.
    """
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    path = f"examples/{tid}/accounts.table.json"

    _turn(orch, oc, tid, "how many accounts?", {path: _card(16916)})
    again = _turn(orch, oc, tid, "are you sure?", {path: _card(16916)})

    assert again["reads"] == []
    assert again["advanced"] is True, "not what happened, and this test exists to say so"
