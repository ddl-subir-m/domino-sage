"""A Chat turn re-read the whole workspace's bytes at its end to undo writes it was not allowed to
make, on every turn including ones that ran nothing at all (#418, #419).

**What ships: #418.** A turn where no tool ran wrote no file, so that read finds nothing. It is a
direct observation of this turn, from two witnesses — the `tool_run` event stream and the
transcript's tool parts — because either can be the only one that sees a call. It cannot help the
BEFORE snapshot: learning it lazily on the first `tool_run` is a race, since the event is seen a
poll later (p50 1000ms) and a `bash` step can write inside that window.

**What does not ship: both of #419's skips.** #419 rests on "an `answer_only` turn provably holds
no tool that can write". That premise is false, and it is false in a way that matters twice.
`READ_ONLY_DENIED` is a DENYLIST, so every tool it does not name is allowed by default, and
`live_read_table`/`live_read_query`/`live_read_files` are deliberately not named (#402, ADR-0058).
They write `examples/<threadId>/*.table.json`.

- The BEFORE snapshot skip lost a card. `new_artifact_paths` needs the baseline to tell a new file
  from an old one and is the only thing that makes such a file a card. Measured against main: the
  table reached disk both ways, `read_artifacts` returned the card on main and nothing with the
  skip. That has its own test here.
- The END-of-turn skip reddened `test_a_looping_chat_session_that_will_not_stop_is_still_cleaned
  _up_after`, which pins the scan on a wedged session because the turn's `finally` commits the tree
  either way — a scan that does not run is writes committed, not a scan deferred.

Both failures are the same shape: an inference about a boundary enforced in another process, over
a list nobody maintains as exhaustive, used to skip a safety net. `any_tool_ran` is evidence about
this turn instead, which is why it is the only condition left.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator, _revert_scan_owed
from sage.workspace.threads import ThreadStore, revert_denied_writes, snapshot_files

from .fake_opencode import FakeOpenCode, Turn
from .test_chat_turn import IntentGateway, OkFeedback, _catalog, _orch

_SERVICE = Path(__file__).resolve().parents[1] / "sage" / "orchestrator" / "service.py"
_THREAD = "t_write"


# --------------------------------------------------------------------------------------------
# The gate.
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "any_tool_ran, owed, why",
    [
        (True, True, "a turn that ran a tool owes the scan"),
        (False, False, "#418: no tool ran, so nothing was written"),
    ],
)
def test_the_scan_is_owed_only_by_a_turn_that_ran_something(any_tool_ran, owed, why):
    assert _revert_scan_owed(any_tool_ran=any_tool_ran) is owed, why


def test_being_armed_read_only_is_not_a_reason_to_skip_the_scan():
    """#419's condition, deliberately absent. Wiring it in reddened
    `test_a_looping_chat_session_that_will_not_stop_is_still_cleaned_up_after`, which pins the scan
    on a wedged session because the turn's `finally` commits the tree either way — a scan that does
    not run is writes committed, not a scan deferred.

    The premise behind it is an inference about a boundary in another process, over a DENYLIST that
    already leaks (`live_read_*`, #402). `any_tool_ran` is evidence about this turn. Only one of
    those is safe to skip a safety net on, so the predicate takes one argument and this test is
    what says the other is not coming back quietly."""
    import inspect

    params = set(inspect.signature(_revert_scan_owed).parameters)
    assert params == {"any_tool_ran"}, (
        f"_revert_scan_owed grew a condition: {params}. If that is `answer_only`, read the wedged "
        "session test before going further.")


def test_the_call_site_reverts_only_under_the_gate():
    """A green predicate does not prove the call site asks it. Structural rather than a grep: the
    question is whether `revert_denied_writes` has an ancestor `if` testing `_revert_scan_owed`,
    which is about the tree and not about the spelling of a line."""
    tree = ast.parse(_SERVICE.read_text())
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "revert_denied_writes"]
    assert calls, "the revert call has moved or been renamed; this test cannot see it any more"

    for call in calls:
        guarded, node = False, call
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.If) and any(
                    isinstance(t, ast.Call) and getattr(t.func, "id", "") == "_revert_scan_owed"
                    for t in ast.walk(node.test)):
                guarded = True
                break
        assert guarded, "a revert_denied_writes call is not under `_revert_scan_owed`"


def test_an_empty_baseline_deletes_every_file_the_allowlist_does_not_cover(tmp_path: Path):
    """#419's named plant, kept although the skip it guards against was withdrawn — because the
    next attempt at #419 is the reader who needs it. `revert_denied_writes` reads
    `prev = before.get(rel)` and unlinks on `None`, so a baseline that is empty (or merely partial,
    which is the tempting version) makes files it never saw look newly created. The skip must skip
    the CALL, and this is what the alternative costs."""
    (tmp_path / "app").mkdir(parents=True)
    (tmp_path / "app" / "main.py").write_text("print('the person wrote this')\n")

    assert "app/main.py" in revert_denied_writes(tmp_path, _THREAD, {})
    assert not (tmp_path / "app" / "main.py").exists(), (
        "the person's file, deleted by a turn that handed the scan an empty baseline")


def test_a_real_baseline_leaves_that_same_file_alone(tmp_path: Path):
    """The other half, so the one above cannot be read as `revert_denied_writes` being broken."""
    (tmp_path / "app").mkdir(parents=True)
    (tmp_path / "app" / "main.py").write_text("print('the person wrote this')\n")
    before = snapshot_files(tmp_path)

    assert revert_denied_writes(tmp_path, _THREAD, before) == []
    assert (tmp_path / "app" / "main.py").read_text() == "print('the person wrote this')\n"


# --------------------------------------------------------------------------------------------
# The turn itself.
# --------------------------------------------------------------------------------------------

def _counted(monkeypatch):
    """Count the workspace reads a Chat turn's own setup and teardown make.

    `service.snapshot_files` is the `setup.snapshot` read and nothing else — `new_artifact_paths`
    takes its own from the `threads` namespace, so a turn walks the tree three times and not the
    two #418 measured. That third walk is out of scope here and is reported on the issue."""
    from sage.orchestrator import service as svc

    counts = {"snapshot": 0, "revert": 0}
    real_snapshot, real_revert = svc.snapshot_files, svc.revert_denied_writes

    def snapshot(*a, **k):
        counts["snapshot"] += 1
        return real_snapshot(*a, **k)

    def revert(*a, **k):
        counts["revert"] += 1
        return real_revert(*a, **k)

    monkeypatch.setattr(svc, "snapshot_files", snapshot)
    monkeypatch.setattr(svc, "revert_denied_writes", revert)
    return counts


def test_a_read_only_turn_still_takes_its_baseline(tmp_path, monkeypatch):
    """The line the withdrawal drew, on the lane #419 was about. This turn is armed
    `arm_read_only("question")` and still snapshots, because a Live read on this same lane writes a
    table that only the baseline can turn into a card. It skips the scan for #418's reason and not
    #419's — nothing ran."""
    counts = _counted(monkeypatch)
    orch, _ = _orch(tmp_path, [Turn(text="Plain answer.")],
                    gateway=IntentGateway({"label": "plain_answer", "confidence": 0.95}))
    tid = orch.create_thread()["id"]

    list(orch.chat_stream(tid, "Explain how rainbows form."))

    assert counts == {"snapshot": 1, "revert": 0}


def test_a_turn_that_can_write_but_ran_nothing_still_takes_its_baseline(tmp_path, monkeypatch):
    """#418, end to end. A greeting classifies `other_chat` and is not a question, so it keeps
    `bash` and is NOT answer-only — its baseline must be taken, because at that moment nothing
    knows the turn will run nothing. Only the end-of-turn scan is skipped, and only once the turn
    is over and no tool was ever seen."""
    counts = _counted(monkeypatch)
    orch, _ = _orch(tmp_path, [Turn(text="Hello.")],
                    gateway=IntentGateway({"label": "other_chat", "confidence": 0.95}))
    tid = orch.create_thread()["id"]

    list(orch.chat_stream(tid, "hi"))

    assert counts == {"snapshot": 1, "revert": 0}


def test_a_turn_that_ran_a_tool_still_undoes_the_write_it_was_not_allowed(tmp_path, monkeypatch):
    """The guard that must go red if the flag is ever wired backwards. A turn that both COULD and
    DID write still scans, and a write outside `examples/<threadId>/` is undone.

    It is also the plant for the TRANSCRIPT witness, and that is the half worth keeping.
    `FakeOpenCode` has no `session_events`, so `_EventTap.ok` is False here and not one `tool_run`
    event is emitted — this turn's tools are visible only as transcript parts. That is not a
    harness quirk standing in for production: the tap is best-effort there too, and a stream that
    never connects or dies mid-turn puts a live turn on exactly this path. Keyed on `tool_run`
    alone, this test fails, and what it would be describing is a denied write left on disk.
    """
    counts = _counted(monkeypatch)
    orch, _ = _orch(tmp_path, [Turn(text="Done.", writes={"notes.txt": "the model wrote this\n"})],
                    gateway=IntentGateway({"label": "other_chat", "confidence": 0.95}))
    tid = orch.create_thread()["id"]
    ws = orch.project(start_preview=False).workspace.path

    list(orch.chat_stream(tid, "write notes.txt for me"))

    assert counts == {"snapshot": 1, "revert": 1}
    assert not (ws / "notes.txt").exists(), (
        "the denied write survived — the scan was skipped for a turn that ran a tool")


def test_a_tool_seen_only_on_the_event_stream_still_owes_the_scan(tmp_path, monkeypatch):
    """The other witness, isolated, because the two fakes are disjoint — `FakeOpenCode` streams
    nothing and cannot exercise `tool_run` at all. The transcript here carries a plain text answer
    and NO tool part, so the only evidence a tool ran is the `tool_run` frame. A live turn reaches
    this shape when it is stopped or times out with the transcript not yet re-read, which is when a
    half-written file is most likely to be sitting in the tree."""
    from .test_chat_turn import StreamingFake, _live

    events = [
        _live("tool_run", tool="bash", input={"command": "echo hi > notes.txt"},
              call_id="c1", status="called"),
        _live("tool_run", tool="bash", call_id="c1", status="completed"),
        _live("message", text="Done.", final=True),
        _live("phase", finish="stop"),
    ]
    counts = _counted(monkeypatch)
    orch, _ = _orch(tmp_path,
                    gateway=IntentGateway({"label": "other_chat", "confidence": 0.95}),
                    client=lambda ws: StreamingFake(ws, [Turn(text="Done.")], events))
    tid = orch.create_thread()["id"]

    list(orch.chat_stream(tid, "run that for me"))

    assert counts == {"snapshot": 1, "revert": 1}, (
        "a tool visible only on the event stream left the turn owing no scan — the `tool_run` "
        "witness is not wired, and a stopped turn's denied write would survive")


# --------------------------------------------------------------------------------------------
# Why the before-snapshot skip was withdrawn.
# --------------------------------------------------------------------------------------------

def test_a_read_only_turn_that_read_live_data_still_draws_its_card(tmp_path: Path):
    """The measurement that withdrew half of #419, kept as the guard for the next attempt.

    An `answer_only` turn is not a turn that cannot write. `READ_ONLY_DENIED` is a denylist and
    `live_read_*` survives it on purpose, so a `data_answer` turn reads the warehouse and
    `liveread.result.record` writes `examples/<threadId>/<slug>.table.json`. `new_artifact_paths`
    is the ONLY thing that turns that file into a card, and it needs the baseline to tell it from a
    file an earlier turn left. Skipped, the file reaches disk and is committed while
    `read_artifacts` stays empty — no card, no `artifacts` event, and nothing for Read again to
    hang on.

    `test_a_live_read_reaches_the_person_end_to_end` cannot catch this: its prompt is "show me 1
    sample conversation", which `_looks_like_question` rejects, so `answer_only` is False there and
    the read-only lane is never the one under test.
    """
    from .test_a_live_read_reaches_the_person_end_to_end import ReadingOpenCode, Warehouse

    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    oc = FakeOpenCode(ws, [Turn(text="ok")])
    orch = Orchestrator(workspace_dir=ws, template=template,
                        gateway=IntentGateway({"label": "data_answer", "confidence": 0.95}),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=Warehouse())
    orch.project(start_preview=False)

    oc.__class__ = ReadingOpenCode
    oc.args = {"source": "Snowflake-Data-Warehouse", "database": "DWH", "schema": "MARTS",
               "table": "GONG__CALLS", "limit": 1}
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})
    oc.orch = orch

    list(orch.chat_stream(tid, "how many calls did we log?"))

    root = orch.project(start_preview=False).record.path
    assert (root / "examples" / tid / "gong-calls.table.json").exists(), (
        "the Live read did not write its table, so this test is no longer about what it says")
    paths = [a["path"] for a in ThreadStore(root).read_artifacts(tid)]
    assert f"examples/{tid}/gong-calls.table.json" in paths, (
        "the table is on disk and has no card — the baseline this turn needed was skipped")
