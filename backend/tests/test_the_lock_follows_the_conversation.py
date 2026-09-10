"""The sensitivity lock follows the conversation, not the Binding (ADR-0043).

The hole these tests close. `_sensitivity_for_turn` read the live Bindings and nothing else, while
`unbind` edits the Binding manifest and leaves the Chat Thread and the OpenCode session exactly
where they were. The transcript persists and is re-sent on every turn after. So turn 1 read a
declared Dataset under the lock, turn 5 unbound it, and turn 6 sent the same transcript — rows
included — to any vendor model the creator picked. Compaction is the sharpest form of it, because
it sends the whole conversation and `chat_compact.COMPACT_FALLBACK` is `gpt-5.4`.

The decision: once ANY turn of a conversation has run under the lock, that conversation stays
locked. The way out is a new chat, not an unbind.

What is deliberately NOT here: a Project-wide taint. A build under the lock can land rows in
`public/data/` or in code, and those outlive the conversation — the ADR records that bound. This
covers the models Sage itself calls with a transcript in hand.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import sage.orchestrator.app as app_module
from sage.assets.provider import Asset, FakeAssetProvider
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import ApprovedModels, FakeResourceProvider
from sage.resources.sensitivity import declared_turn_refusal
from sage.router.model_control import ModelControl
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

# The group `FakeResourceProvider.list_alias_groups` answers for, holding `qwen-2-5`.
GROUP = "sensitive-approved"
APPROVED = "qwen-2-5"


class ScriptedGateway:
    """One scripted word per routed request — the scope classifier is the only caller here."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport

        return FeedbackReport(ok=True, errors=[], raw="")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text("{}")
    return t


def _assets(root: Path) -> FakeAssetProvider:
    mount = root / "mounts"
    (mount / "claims").mkdir(parents=True)
    provider = FakeAssetProvider(root=mount)
    # After construction: `__post_init__` seeds its own demo Datasets over whatever was handed in.
    provider.assets = [
        Asset("ds_claims", "claims", tags=["sensitive"], project="Revenue",
              mount_path=str(mount / "claims")),
    ]
    return provider


def _catalog() -> ModelCatalog:
    """Every sovereign slot approved, so a locked turn has somewhere to go."""
    return ModelCatalog(sovereign_plan=APPROVED, sovereign_implement=APPROVED,
                        sovereign_ask=APPROVED, plan="gpt-5.4", implement="gpt-5.4", ask="gpt-5.4")


def _orch(tmp: Path, turns: list[Turn] | None = None):
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns or [Turn(text="Here it is.")])
    orch = Orchestrator(
        workspace_dir=ws, template=_template(tmp), gateway=ScriptedGateway(), catalog=_catalog(),
        project_id="Sage", feedback=OkFeedback(), opencode_client=oc,
        assets=_assets(tmp), resources=FakeResourceProvider(),
        domino_project_name="Revenue",
    )
    orch.project(start_preview=False)
    return orch, oc


def _bind_declared(orch: Orchestrator) -> None:
    orch.project(start_preview=False).workspace.update_bindings(
        lambda _: [{"kind": "dataset", "id": "ds_claims", "name": "claims",
                    "display_name": "claims"}])


def _unbind_everything(orch: Orchestrator) -> None:
    """What `unbind` leaves behind, without the app-resource rewrite around it: the manifest loses
    the Dataset and nothing touches the Thread or the OpenCode session. That gap IS the hole."""
    orch.project(start_preview=False).workspace.update_bindings(lambda _: [])


def _locked(orch: Orchestrator, conversation: str | None) -> bool:
    project = orch.project(start_preview=False)
    approved, refusal = orch._sensitivity_for_turn(project, conversation)
    return approved is not None or bool(refusal)


# ---- the bit itself ------------------------------------------------------------------------------


def test_the_bit_lands_beside_the_transcript_and_survives_a_restart(tmp_path: Path):
    """A flag in memory would clear on a server restart while the conversation history it is about
    came back — which re-opens the exact hole. So it goes on disk, in the Thread's own directory."""
    orch, _oc = _orch(tmp_path)
    ws = orch.project(start_preview=False).record

    assert ws.session_ran_locked("thr_a") is False
    ws.mark_session_locked("thr_a")

    # Beside the Thread's own session and history, on the Project volume rather than in an app:
    # one conversation can build several apps, and a taint one app could not see is no taint.
    assert ws.sensitivity_lock_path("thr_a").parent == ws.path / ".sage" / "threads" / "thr_a"
    assert ws.sensitivity_lock_path("thr_a").exists()
    # What a restarted server reads: a fresh record over the same volume, no memory carried.
    assert type(ws)(ws.project_id, ws.path).session_ran_locked("thr_a") is True
    assert ws.session_ran_locked("thr_a") is True
    assert ws.session_ran_locked("thr_b") is False


def test_the_unscoped_build_turn_gets_a_slot_too(tmp_path: Path):
    """A build driven from the CLI or a test names no conversation and still keeps a transcript
    (`.sage/session.json`). A lock it could not record would be a lock a restart lifts."""
    ws = _orch(tmp_path)[0].project(start_preview=False).record

    ws.mark_session_locked("")

    assert ws.session_ran_locked("") is True
    assert ws.sensitivity_lock_path("") == ws.path / ".sage" / "sensitivity.json"


def test_marking_twice_keeps_the_first_answer(tmp_path: Path):
    """Every locked turn marks, so this runs on turn 2 onwards. It must not rewrite the record of
    when the conversation was first tainted."""
    ws = _orch(tmp_path)[0].project(start_preview=False).record

    ws.mark_session_locked("thr_a")
    first = ws.sensitivity_lock_path("thr_a").read_text()
    ws.mark_session_locked("thr_a")

    assert ws.sensitivity_lock_path("thr_a").read_text() == first


# ---- the gate reads it ---------------------------------------------------------------------------


def test_a_turn_is_locked_by_a_live_declaration_as_before(tmp_path: Path, monkeypatch):
    """The unchanged half. Nothing below means anything if this stopped working."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path)
    _bind_declared(orch)

    approved, refusal = orch._sensitivity_for_turn(orch.project(start_preview=False), "thr_a")

    assert refusal == ""
    assert approved is not None and approved.names == frozenset({APPROVED})


def test_an_unbind_does_not_unlock_a_conversation_that_already_read_the_rows(
    tmp_path: Path, monkeypatch
):
    """The hole, closed. The Bindings are empty and the transcript is not."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path)
    _bind_declared(orch)
    orch.project(start_preview=False).record.mark_session_locked("thr_a")

    _unbind_everything(orch)

    approved, refusal = orch._sensitivity_for_turn(orch.project(start_preview=False), "thr_a")
    assert refusal == ""
    assert approved is not None and approved.names == frozenset({APPROVED})


def test_the_taint_is_one_conversations_and_a_new_chat_is_the_way_out(tmp_path: Path, monkeypatch):
    """The stated way out has to actually work, or the advice in the copy is a lie. A second
    Conversation has read nothing and runs unlocked once the Dataset is gone."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path)
    _bind_declared(orch)
    orch.project(start_preview=False).record.mark_session_locked("thr_a")
    _unbind_everything(orch)

    assert _locked(orch, "thr_a") is True
    assert _locked(orch, "thr_b") is False


def test_the_preview_proxy_asks_about_the_bindings_and_not_a_conversation(
    tmp_path: Path, monkeypatch
):
    """`None` is not `""`. The previewed app's own model call carries the app's current Bindings and
    no transcript, so a Conversation's sticky lock is not about it — and the unscoped Build slot
    must not be read as "no conversation" either."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path)
    _bind_declared(orch)
    orch.project(start_preview=False).record.mark_session_locked("")
    _unbind_everything(orch)

    assert _locked(orch, None) is False
    assert _locked(orch, "") is True


def test_the_lock_stays_off_where_the_deployment_never_opted_in(tmp_path: Path, monkeypatch):
    """A stray marker file must not be able to turn a feature on that the deployment never asked
    for. The group name is still the on-switch, checked before anything else is read."""
    monkeypatch.delenv("SAGE_SENSITIVE_MODEL_GROUP", raising=False)
    orch, _oc = _orch(tmp_path)
    orch.project(start_preview=False).record.mark_session_locked("thr_a")

    assert orch._sensitivity_for_turn(orch.project(start_preview=False), "thr_a") == (None, "")


# ---- both harnesses set it -----------------------------------------------------------------------


def test_a_chat_turn_under_the_lock_taints_its_thread(tmp_path: Path, monkeypatch):
    """Chat's half, through the real turn path rather than by calling the marker directly."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path)
    _bind_declared(orch)
    tid = orch.create_thread()["id"]

    list(orch.chat_stream(tid, "what is in the claims data?"))

    _unbind_everything(orch)
    assert _locked(orch, tid) is True


def test_a_build_turn_under_the_lock_taints_its_conversation(tmp_path: Path, monkeypatch):
    """Build's half. `_build_stream` reads `project.build_conversation`, which `approve_stream`
    pins too — so one arm site covers both entries."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path, turns=[Turn(writes={"src/App.tsx": "// a table\n"})])
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    _bind_declared(orch)

    list(orch.build_stream("show the claims", conversation="thr_build"))

    _unbind_everything(orch)
    assert _locked(orch, "thr_build") is True


def test_an_unlocked_turn_taints_nothing(tmp_path: Path, monkeypatch):
    """The ordinary case has to stay ordinary: no Dataset declared, no marker written, no file left
    behind to lock a Conversation the day somebody tags something."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]

    list(orch.chat_stream(tid, "hello"))

    assert orch.project(start_preview=False).record.session_ran_locked(tid) is False


def test_a_refused_turn_taints_nothing(tmp_path: Path, monkeypatch):
    """A turn refused before it ran sent no transcript anywhere, so there is nothing to carry. If
    this marked, an administrator fixing the group would leave the Conversation locked for good on
    the strength of a turn that never happened."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", "no-such-group")
    orch, oc = _orch(tmp_path)
    _bind_declared(orch)
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "what is in the claims data?"))

    assert any(e.get("type") == "error" for e in events)
    assert oc.prompts == []
    assert orch.project(start_preview=False).record.session_ran_locked(tid) is False


def test_a_turn_after_the_unbind_still_arms_what_compaction_rides_on(tmp_path: Path, monkeypatch):
    """Compaction is the sharpest form of the hole and the reason it is worth this much care: it
    sends the WHOLE conversation, and `chat_compact.COMPACT_FALLBACK` is the vendor alias `gpt-5.4`.

    `test_compaction_cannot_leave_the_lock` proves that a summarize request on an ARMED turn leaves
    the shim on an approved alias. It could not prove anything about a turn after an unbind, because
    nothing then armed. This is that missing half: the turn is still armed, so the invariant that
    file pins still applies to it.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    armed: list[list[str]] = []
    real = ModelControl.arm_sensitivity

    def spy(self, approved, order=()):
        armed.append(sorted(approved))
        return real(self, approved, order)

    monkeypatch.setattr(ModelControl, "arm_sensitivity", spy)
    orch, _oc = _orch(tmp_path, turns=[Turn(text="one"), Turn(text="two")])
    _bind_declared(orch)
    tid = orch.create_thread()["id"]

    list(orch.chat_stream(tid, "what is in the claims data?"))
    _unbind_everything(orch)
    list(orch.chat_stream(tid, "and the totals?"))

    assert armed == [[APPROVED], [APPROVED]]


# ---- the handoff carries it ----------------------------------------------------------------------


def test_the_chat_to_build_handoff_carries_the_taint(tmp_path: Path, monkeypatch):
    """A handoff hands the Build turn the SAME conversation, transcript included. A handoff that
    dropped the bit would be the same leak with one extra step in front of it — so this is asserted
    rather than left to follow from the two ids happening to match today.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path, turns=[Turn(text="Here it is."),
                                       Turn(writes={"src/App.tsx": "// a table\n"})])
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    _bind_declared(orch)
    tid = orch.create_thread()["id"]

    list(orch.chat_stream(tid, "what is in the claims data?"))
    _unbind_everything(orch)

    # The crossing: Build, same conversation, with nothing declared in the Bindings any more.
    approved, refusal = orch._sensitivity_for_turn(orch.project(start_preview=False), tid)
    assert refusal == ""
    assert approved is not None and approved.names == frozenset({APPROVED})


# ---- what the surfaces are told ------------------------------------------------------------------


def test_the_lock_state_reports_the_session_reason_with_no_dataset_bound(
    tmp_path: Path, monkeypatch
):
    """What the chip and the picker draw from. `locked` still true, `datasets` empty, and `reason`
    the only thing that can tell the browser which sentence to write."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path)
    _bind_declared(orch)
    orch.project(start_preview=False).record.mark_session_locked("thr_a")
    _unbind_everything(orch)

    state = orch.sensitivity_state("thr_a")

    assert state["locked"] is True
    assert state["reason"] == "session"
    assert state["datasets"] == []
    assert state["approved"] == [APPROVED]
    assert state["model"] == APPROVED and state["chat_model"] == APPROVED


def test_a_live_declaration_still_names_the_dataset(tmp_path: Path, monkeypatch):
    """The live reason wins while there is one: it names a row the creator can go and look at,
    which the session reason cannot."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path)
    _bind_declared(orch)
    orch.project(start_preview=False).record.mark_session_locked("thr_a")

    state = orch.sensitivity_state("thr_a")

    assert state["reason"] == "declared"
    assert state["datasets"] == ["claims"]


def test_a_state_read_naming_no_conversation_asks_about_the_bindings_alone(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path)
    _bind_declared(orch)
    orch.project(start_preview=False).record.mark_session_locked("thr_a")
    _unbind_everything(orch)

    state = orch.sensitivity_state()

    assert state["locked"] is False
    assert state["reason"] == ""


# ---- fail-closed edges, unchanged ----------------------------------------------------------------


def test_an_unusable_group_after_the_fact_still_refuses_the_turn(tmp_path: Path, monkeypatch):
    """The sticky lock is a lock, not a grandfather clause. A group that breaks after the
    Conversation was tainted refuses the turn exactly as it would with the Dataset still bound."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path)
    _bind_declared(orch)
    orch.project(start_preview=False).record.mark_session_locked("thr_a")
    _unbind_everything(orch)
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", "no-such-group")
    orch._gate = None   # the group name is read once per gate; this is a new deployment answer

    approved, refusal = orch._sensitivity_for_turn(orch.project(start_preview=False), "thr_a")

    assert approved is None
    assert "no-such-group" in refusal


def test_the_refusal_names_no_dataset_and_offers_a_new_chat(tmp_path: Path):
    """The copy. The existing sentence names the declared Dataset, and that sentence is false once
    the Dataset is unbound — it sends the creator to a panel row that is not there. The sticky one
    names the reading instead, and says the thing a creator will not guess: unbinding is the
    obvious move and it is the wrong one."""
    sentence = declared_turn_refusal(
        ApprovedModels(group_name=GROUP, group_found=True, members=2), [])

    assert "Dataset claims" not in sentence
    assert "sensitive data" in sentence
    assert "Start a new chat" in sentence


def test_the_way_out_is_in_the_refusal_exactly_once(tmp_path: Path):
    """The server appends it to the refusal, and the panel draws its own paragraph of it. Both fire
    when the lock is dead AND sticky — an emptied group after an unbind — and the drawer then said
    the same two sentences twice in a row. The panel suppresses its paragraph under `lockDead`;
    this pins the other half, that the server's sentence carries it once."""
    sentence = declared_turn_refusal(
        ApprovedModels(group_name=GROUP, group_found=True, members=2), [])

    assert sentence.count("Start a new chat") == 1


def test_the_declared_refusal_is_unchanged_and_says_nothing_about_a_new_chat(tmp_path: Path):
    """A creator whose Dataset IS bound has a better way out than abandoning the conversation:
    unbind it. Offering a new chat there would be worse advice, so it is not offered."""
    from sage.resources.bindings import Binding

    sentence = declared_turn_refusal(
        ApprovedModels(group_name=GROUP, group_found=True, members=2),
        [Binding(kind="dataset", id="ds_claims", name="claims", display_name="claims")])

    assert "claims" in sentence
    assert "new chat" not in sentence


def test_a_conversation_id_that_names_no_thread_cannot_take_the_lock_down(tmp_path: Path,
                                                                         monkeypatch):
    """`conversation` arrives on a URL, and `safe_id` refuses anything that could not name a path
    segment. Left to the route's own handler that refusal answers 200 with the lock reported OFF —
    which the browser stores over a good answer and un-greys every model the next turn will refuse.

    An id that could not name a Thread names no locked one either: nothing can ever have written a
    lock under it. So the honest answer is the Bindings alone, and the Bindings still say locked.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path)
    _bind_declared(orch)
    monkeypatch.setattr(app_module, "orchestrator", orch)

    body = json.loads(app_module.sensitivity_state(conversation="../../etc").body)

    assert body["enabled"] is True
    assert body["locked"] is True
    assert body["approved"] == [APPROVED]


# ---- a lock that cannot be written down ----------------------------------------------------------


def _break_the_lock_write(monkeypatch) -> None:
    """The Project volume refusing the write. Read-only mount, full disk, permissions on `.sage/`."""
    from sage.workspace.manager import ProjectRecord

    def boom(self, conversation: str = "") -> None:
        raise OSError("Read-only file system")

    monkeypatch.setattr(ProjectRecord, "mark_session_locked", boom)


def test_a_chat_turn_that_cannot_record_its_lock_is_refused(tmp_path: Path, monkeypatch):
    """Running unrecorded would leave declared rows in a conversation that the next turn — and every
    turn after a restart — reads as clean. So the write failing stops the turn."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, oc = _orch(tmp_path)
    _bind_declared(orch)
    tid = orch.create_thread()["id"]
    _break_the_lock_write(monkeypatch)

    events = list(orch.chat_stream(tid, "what is in the claims data?"))

    assert any(e.get("type") == "error" and "couldn't save" in e["message"] for e in events)
    assert oc.prompts == []


def test_a_refused_lock_write_leaves_nothing_armed(tmp_path: Path, monkeypatch):
    """The half a refusal is easy to get wrong. The Chat pin and the web grant are already armed
    when the write is attempted, and they are NOT inside the try/finally below it — so a raise walks
    out of the generator with both still live, and `ModelControl` is not per-turn state. The next
    turn in the process would inherit a Chat pin it never asked for."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, _oc = _orch(tmp_path)
    _bind_declared(orch)
    tid = orch.create_thread()["id"]
    _break_the_lock_write(monkeypatch)

    list(orch.chat_stream(tid, "read https://example.com and the claims data"))

    snapshot = orch.project(start_preview=False).control.snapshot()
    assert snapshot.chat_thread_id is None
    assert snapshot.web_allowed is False
    assert snapshot.approved_models is None


def test_a_build_turn_that_cannot_record_its_lock_is_refused(tmp_path: Path, monkeypatch):
    """Build's half, and its own disarm: `restore_mode()` puts back the turn-mode pin and the
    read-only and web grants that are armed by the time the write is attempted."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch, oc = _orch(tmp_path, turns=[Turn(writes={"src/App.tsx": "// a table\n"})])
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    _bind_declared(orch)
    _break_the_lock_write(monkeypatch)

    events = list(orch.build_stream("show the claims", conversation="thr_build"))

    assert any(e.get("type") == "error" and "couldn't save" in e["message"] for e in events)
    assert oc.prompts == []
    snapshot = orch.project(start_preview=False).control.snapshot()
    assert snapshot.web_allowed is False
    assert snapshot.approved_models is None
