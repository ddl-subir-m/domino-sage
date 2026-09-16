"""A Chat turn reaches its OWN Thread's records and no other Thread's.

`.sage/threads/<threadId>/` is not just `findings.md`. It also holds `meta.json`, `context.json`,
`artifacts.json`, `handoff.json` and `history.jsonl` — the Thread's whole transcript. Nothing gates
a READ of any of it: `chat_path_allowed` has two call sites, `revert_denied_writes` and
`write_path_from_tool_call`, and both ask about a write. So the link's granularity IS the read
boundary, and linking the `.sage/threads` tree would put every other Thread's transcript in this
turn's cwd behind nothing but a prompt sentence — one that sits in the same paragraph as the
admission that a turn already spent itself opening a Thread's `context.json`.

The second half is the one a link-scoping fix forgets. The chat workdir is per-Project, not
per-Thread, and it outlives the turn that made it. A link left standing from the Thread that ran
last turn leaves that Thread's transcript readable from this one, which is the same leak by
another route and reaches exactly the people most likely to have two Threads open.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace.threads import ensure_chat_workdir

from .fake_opencode import FakeOpenCode, Turn

MINE = "thr_mine01"
YOURS = "thr_yours1"


def _records(root: Path, tid: str, secret: str) -> None:
    d = root / ".sage" / "threads" / tid
    d.mkdir(parents=True, exist_ok=True)
    (d / "history.jsonl").write_text(secret)


def test_the_turn_cannot_read_another_threads_records(tmp_path: Path):
    _records(tmp_path, MINE, "mine\n")
    _records(tmp_path, YOURS, "the other Thread's transcript\n")

    work = ensure_chat_workdir(tmp_path, "# chat", thread_id=MINE)

    assert (work / ".sage" / "threads" / MINE / "history.jsonl").read_text() == "mine\n"
    assert not (work / ".sage" / "threads" / YOURS).exists()
    assert [p.name for p in (work / ".sage" / "threads").iterdir()] == [MINE]


def test_the_previous_threads_link_does_not_survive_into_this_turn(tmp_path: Path):
    """The workdir is shared across Threads. Without a prune, the scoping is theatre: the person
    asks one Thread a question, and the Thread they were in ten minutes ago is still mounted."""
    _records(tmp_path, MINE, "mine\n")
    _records(tmp_path, YOURS, "the other Thread's transcript\n")

    ensure_chat_workdir(tmp_path, "# chat", thread_id=YOURS)
    work = ensure_chat_workdir(tmp_path, "# chat", thread_id=MINE)

    assert not (work / ".sage" / "threads" / YOURS).exists()
    assert (work / ".sage" / "threads" / MINE / "history.jsonl").read_text() == "mine\n"


def test_a_caller_that_names_no_thread_reaches_no_records_at_all(tmp_path: Path):
    """`thread_id=None` links nothing rather than falling back to the tree. A caller that cannot
    say which Thread this is has no claim on any Thread's records, and handing over everything
    exactly when the caller has shown it does not know what it is asking for is the worst possible
    default. This fails at the read, where it is visible, instead of succeeding too broadly."""
    _records(tmp_path, MINE, "mine\n")
    _records(tmp_path, YOURS, "the other Thread's transcript\n")

    work = ensure_chat_workdir(tmp_path, "# chat")

    assert list((work / ".sage" / "threads").iterdir()) == []
    assert not (work / ".sage" / "threads" / MINE).exists()


def test_the_prune_never_unlinks_a_real_directory(tmp_path: Path):
    """`_ensure_dir_link` promises a real directory at a link site is left alone — that is what
    stops it deleting a person's `public/data`. A prune that broke the promise here would make it
    worthless everywhere it is relied on, so the prune is guarded on `is_symlink()`."""
    _records(tmp_path, MINE, "mine\n")
    real = tmp_path / ".sage" / "chat-work" / ".sage" / "threads" / "thr_real01"
    real.mkdir(parents=True)
    (real / "keep.txt").write_text("not ours to delete\n")

    ensure_chat_workdir(tmp_path, "# chat", thread_id=MINE)

    assert (real / "keep.txt").read_text() == "not ours to delete\n"


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def test_a_real_turn_stands_in_a_cwd_that_holds_its_own_threads_records(tmp_path: Path):
    """The scoping is only worth the name if the caller says which Thread this is. Nothing else
    covers that wire: `ensure_chat_workdir` defaults `thread_id` to None so the existing callers
    keep working, which means a caller that quietly stops passing it reaches no records at all and
    every unit test of the helper still passes. This runs a turn through the orchestrator and looks
    at the cwd the session was actually opened in."""
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    oc = FakeOpenCode(ws, [Turn(text="ok")])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "hi"))

    work = Path(oc.sessions[0]["directory"])
    link = work / ".sage" / "threads" / tid
    assert link.is_symlink(), sorted(p.name for p in (work / ".sage" / "threads").iterdir())
    assert link.resolve() == (orch.project(start_preview=False).record.path
                              / ".sage" / "threads" / tid).resolve()
