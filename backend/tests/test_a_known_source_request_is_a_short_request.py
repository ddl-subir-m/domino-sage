"""A turn that already knows it must ask for a missing source sends a short request (#566).

Measured in one Xiaomi run: a person asked which customers requested ARM support before attaching
a source. Sage knew at the top of the turn that the question needed a source it did not have
(`chat_task.resolve` wrote `pendingTask.awaiting = "source"`), and then sent the model the whole
general Chat prompt with the general Chat tools. The model loaded an investigation skill and
inspected local folders for 232 seconds before it asked for the source.

The decision here is the EXISTING one and it is deterministic: the pending task says what the
turn is waiting for, and nothing in it is read off model prose. On that turn the request carries
a short prompt that asks for the missing source and keeps the question, and no tools. Every other
turn keeps today's behaviour, which the profiles below pin beside it.

The capture is synthetic. `FakeOpenCode` never calls the shim back, so the tool list is taken by
feeding a representative OpenCode tool list through the production `EnforcementShim` at the
moment `send_prompt` runs, while the turn's arming is up. Names only; bytes are the prompt text.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator import chat_task
from sage.workspace.threads import ThreadStore

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, ObservedControlOpenCode, _orch

ASK = "Fuse data from sfdc cases and gong transcripts and tell me which of our customers have asked for ARM support?"
REPLY = "the data warehouse is here @Snowflake-Data-Warehouse"
SOURCE = {"kind": "data_source", "id": "ds1", "name": "Snowflake-Data-Warehouse"}
ONE_TABLE = "How many users joined last month?"

# What OpenCode 1.18.4 offers `sage-chat` before the shim reads it, by name. A representative list
# rather than a recording: the bare coding tools, the loop tools, the web tool, the Chat-only
# tools and the three Live read tools under their MCP prefix.
OFFERED = ("bash", "edit", "write", "read", "glob", "grep", "list", "skill", "task", "todowrite",
           "webfetch", "artifact_write", "delegated_model_call",
           "sage-live-read_live_read_table", "sage-live-read_live_read_files",
           "sage-live-read_live_read_query")


class CapturingOpenCode(ObservedControlOpenCode):
    """Records, per send, the prompt bytes and the tool names the shim would let through."""

    def __init__(self, workspace: Path, turns: list[Turn] | None = None) -> None:
        super().__init__(workspace, turns)
        self.shim = None
        self.profiles: list[dict] = []

    def send_prompt(self, session_id: str, text: str, *args, **kwargs) -> None:
        names = None
        if self.shim is not None:
            request = {"model": "a", "messages": [{"role": "user", "content": "x"}],
                       "tools": [{"type": "function", "function": {"name": n}} for n in OFFERED]}
            list(self.shim.handle(request, "p"))
            sent = self.shim.gateway.seen[-1][0]
            names = (sorted(t["function"]["name"] for t in sent["tools"])
                     if "tools" in sent else [])
        self.profiles.append({"bytes": len(text.encode("utf-8")), "tools": names, "text": text})
        super().send_prompt(session_id, text, *args, **kwargs)


def _setup(tmp_path: Path, turns: list[Turn] | None = None, label: str = "data_answer"):
    turns = turns or [Turn(text="Please attach the warehouse."), Turn(text="Answered.")]
    # A label is a confident verdict; anything with a space is sent raw, as an unparseable reply.
    verdict = label if " " in label else {"label": label, "confidence": 0.93}
    orch, oc = _orch(tmp_path, turns, gateway=IntentGateway(verdict),
                     client=lambda ws: CapturingOpenCode(ws, list(turns)))
    project = orch.project(start_preview=False)
    oc.control = project.control
    oc.shim = project.shim
    tid = orch.create_thread()["id"]
    return orch, oc, tid


def _model_calls(orch, oc) -> int:
    """Classifier calls through the gateway plus turns dispatched to OpenCode."""
    gateway = orch.project(start_preview=False).shim.gateway
    classifier = len([1 for _, labels in gateway.seen
                      if getattr(labels, "component", "") == "chat-intent"])
    return classifier + len(oc.prompts)


# ---- the three profiles, measured (item 1 of the ticket) ----------------------------------------

def test_profiles_are_measured(tmp_path: Path, capsys):
    """Prints the request profile of the three turns the ticket names. Not an assertion on the
    numbers; the report carries them. Runs green before and after the change."""
    rows = []
    # (a) source clarification: the ARM question with nothing attached.
    orch, oc, tid = _setup(tmp_path / "a")
    list(orch.chat_stream(tid, ASK))
    rows.append(("source clarification", oc.profiles[-1], _model_calls(orch, oc)))
    # (b) accepted catalog discovery: attach, offer, accept, replay.
    orch, oc, tid = _setup(tmp_path / "b")
    list(orch.chat_stream(tid, ASK))
    orch.add_thread_context(tid, SOURCE)
    offer = next(e for e in orch.chat_stream(tid, REPLY) if e["type"] == "investigation-offer")
    orch.decide_thread_investigation(tid, "open", task_id=offer["taskId"])
    before = _model_calls(orch, oc)
    list(orch.chat_stream(tid, ASK, skip_investigation_gate=True, task_id=offer["taskId"]))
    rows.append(("accepted discovery", oc.profiles[-1], _model_calls(orch, oc) - before))
    # (c) normal data answering: one-table question against an attached source.
    orch, oc, tid = _setup(tmp_path / "c", turns=[Turn(text="Answered.")])
    orch.add_thread_context(tid, SOURCE)
    list(orch.chat_stream(tid, ONE_TABLE))
    rows.append(("data answer", oc.profiles[-1], _model_calls(orch, oc)))
    with capsys.disabled():
        print()
        for name, profile, calls in rows:
            tools = profile["tools"]
            print(f"PROFILE {name}: prompt_bytes={profile['bytes']} "
                  f"tool_count={len(tools) if tools is not None else 'n/a'} "
                  f"model_calls={calls} tools={tools}")


# ---- the ARM question with no source: a source request, nothing else --------------------------

def test_the_known_source_request_carries_no_tools_and_a_short_prompt(tmp_path: Path):
    orch, oc, tid = _setup(tmp_path)
    list(orch.chat_stream(tid, ASK))

    profile = oc.profiles[-1]
    assert profile["tools"] == [], profile["tools"]
    # Short: the general Chat prompt for the same question is several thousand bytes.
    assert profile["bytes"] < 1200, profile["bytes"]
    assert profile["text"].endswith(ASK), "the question is kept, and it is the last thing said"
    for general in ("live_read_query", "Answer general questions directly",
                    "Write Artifacts under", "delegated_model_call"):
        assert general not in profile["text"], general
    assert "attach" in profile["text"].lower()
    root = orch.project(start_preview=False).record.path
    pending = ThreadStore(root).read_context(tid)["pendingTask"]
    assert pending == {**pending, "question": ASK, "awaiting": "source"}


def test_the_known_source_request_spends_the_classifier_and_one_toolless_turn(tmp_path: Path):
    """Two model calls, as before: the classifier is the second signal the lane needs (the pending
    task alone admits "Investigate why the sky is blue"), and then one send with no tools, so the
    turn cannot spend further calls on tool results. No repair ran."""
    orch, oc, tid = _setup(tmp_path)
    events = list(orch.chat_stream(tid, ASK))
    assert _model_calls(orch, oc) == 2
    assert len(oc.prompts) == 1
    assert oc.profiles[-1]["tools"] == []
    done = next(e for e in events if e["type"] == "done")
    assert done["ok"] is True and "recoveries" not in done


def test_the_source_request_is_read_only_with_a_reason_of_its_own(tmp_path: Path):
    orch, oc, tid = _setup(tmp_path)
    list(orch.chat_stream(tid, ASK))
    state = oc.snapshots[-1]
    assert state.read_only_turn is True
    assert state.read_only_reason == "source"
    assert state.chat_artifact_turn is False


# ---- attach, one offer, accept, the task continues across tables ---------------------------------

def test_attach_offer_accept_continues_the_original_task(tmp_path: Path):
    orch, oc, tid = _setup(tmp_path)
    list(orch.chat_stream(tid, ASK))
    orch.add_thread_context(tid, SOURCE)

    events = list(orch.chat_stream(tid, REPLY))

    offers = [e for e in events if e["type"] == "investigation-offer"]
    assert len(offers) == 1 and offers[0]["prompt"] == ASK
    assert len(oc.prompts) == 1, "the reply drew the card and ran no model"
    task_id = offers[0]["taskId"]
    granted = orch.decide_thread_investigation(tid, "open", task_id=task_id)
    assert orch.decide_thread_investigation(tid, "open", task_id=task_id) == granted
    calls = _model_calls(orch, oc)
    list(orch.chat_stream(tid, ASK, skip_investigation_gate=True, task_id=task_id))
    profile = oc.profiles[-1]
    assert profile["text"].endswith(ASK)
    assert "Investigation is open for this conversation" in profile["text"]
    assert not oc.snapshots[-1].read_only_turn
    assert {"bash", "sage-live-read_live_read_query", "skill", "task"} <= set(profile["tools"])
    assert _model_calls(orch, oc) - calls == 2, "classifier and the turn; nothing extra"
    # Accepting twice makes no model call: the replay is refused before any send.
    replay = list(orch.chat_stream(tid, ASK, skip_investigation_gate=True, task_id=task_id))
    assert replay[-1]["decision"] == "stale question"
    assert len(oc.prompts) == 2


def test_close_revokes_through_the_existing_door(tmp_path: Path):
    orch, oc, tid = _setup(tmp_path)
    list(orch.chat_stream(tid, ASK))
    orch.add_thread_context(tid, SOURCE)
    offer = next(e for e in orch.chat_stream(tid, REPLY) if e["type"] == "investigation-offer")
    orch.decide_thread_investigation(tid, "open", task_id=offer["taskId"])
    list(orch.chat_stream(tid, ASK, skip_investigation_gate=True, task_id=offer["taskId"]))
    closed = orch.decide_thread_investigation(tid, "close")
    assert closed["investigation"]["state"] == "closed"
    oc.turns.append(Turn(text="Bounded again."))
    list(orch.chat_stream(tid, ONE_TABLE))
    assert oc.snapshots[-1].read_only_turn is True
    assert oc.snapshots[-1].read_only_reason == "question"


# ---- the neighbours keep their behaviour ----------------------------------------------------------

def test_a_source_already_attached_keeps_the_general_turn(tmp_path: Path):
    """The same question with the store on the Thread is the investigation card, not a request."""
    orch, oc, tid = _setup(tmp_path)
    orch.add_thread_context(tid, SOURCE)
    events = list(orch.chat_stream(tid, ASK))
    assert any(e["type"] == "investigation-offer" for e in events)
    assert oc.profiles == []


def test_an_open_investigation_keeps_its_shell(tmp_path: Path):
    orch, oc, tid = _setup(tmp_path, turns=[Turn(text="Answered.")])
    orch.add_thread_context(tid, SOURCE)
    orch.decide_thread_investigation(tid, "open")
    list(orch.chat_stream(tid, ASK, skip_investigation_gate=True))
    assert not oc.snapshots[-1].read_only_turn
    assert "bash" in oc.profiles[-1]["tools"]


@pytest.mark.parametrize("prompt, items, label, pending", [
    # Investigative words, a file attached: `needs_source` is false, so no pending task at all.
    ("Investigate which accounts in @desk.csv look like adopters",
     [{"kind": "file", "id": "f1", "name": "desk.csv", "path": ".sage/scratch/desk.csv"}],
     "data_answer", False),
    # Investigative words and no data noun: the pending task IS written (measured: `needs_source`
    # reads the imperative as a data ask), and the classifier's `plain_answer` is what keeps this
    # turn general. One signal is not enough.
    ("Investigate why the sky is blue", [], "plain_answer", True),
    # The ARM question itself when the classifier did not answer: today's general turn.
    (ASK, [], "not json", True),
])
def test_an_ambiguous_source_need_keeps_the_general_prompt_and_tools(tmp_path: Path, prompt, items,
                                                                      label, pending):
    orch, oc, tid = _setup(tmp_path, turns=[Turn(text="Answered.")], label=label)
    for item in items:
        orch.add_thread_context(tid, item)
    list(orch.chat_stream(tid, prompt))
    assert bool(orch.thread_context(tid).get("pendingTask")) is pending
    profile = oc.profiles[-1]
    assert oc.snapshots[-1].read_only_reason != "source"
    assert "Answer general questions directly" in profile["text"]
    assert profile["tools"], "the general lane keeps a tool list"


def test_a_one_table_question_keeps_the_data_answer_lane(tmp_path: Path):
    """Negative control: the lane that answers from a store keeps the tool it requires and loses
    the ones it is denied, exactly as before."""
    orch, oc, tid = _setup(tmp_path, turns=[Turn(text="Answered.")])
    orch.add_thread_context(tid, SOURCE)
    list(orch.chat_stream(tid, ONE_TABLE))
    profile = oc.profiles[-1]
    assert oc.snapshots[-1].read_only_reason == "question"
    tools = set(profile["tools"])
    assert {"sage-live-read_live_read_query", "read", "skill"} <= tools
    assert {"bash", "task", "todowrite", "write", "webfetch"}.isdisjoint(tools)
    assert "live_read_query" in profile["text"]


def test_the_helper_reads_the_pending_task_and_nothing_else(tmp_path: Path):
    orch, _, tid = _setup(tmp_path)
    root = orch.project(start_preview=False).record.path
    store = ThreadStore(root)
    assert chat_task.awaiting_source(store, tid, ASK) is False
    store.update_context(tid, lambda ctx: {**ctx, "pendingTask": {
        "id": "task_1", "question": ASK, "awaiting": "source", "sourceIds": [],
        "offerDecision": ""}})
    assert chat_task.awaiting_source(store, tid, ASK) is True
    assert chat_task.awaiting_source(store, tid, "another question") is False
    store.update_context(tid, lambda ctx: {**ctx, "items": [SOURCE]})
    assert chat_task.awaiting_source(store, tid, ASK) is False, "a source on the Thread ends it"
