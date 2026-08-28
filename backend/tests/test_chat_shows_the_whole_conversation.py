"""A Conversation is one thing, and the control API can now hand over the whole of it (#56).

Chat's half and Build's half live in different files — a Thread's `history.jsonl` and one per Built
App (#68) — and until this ticket nothing put them back together. Typing straight into Build made a
Conversation whose Chat half was empty forever, and opening that row in Chat showed the landing
screen as if the Conversation had never happened.

Three claims are held here, and they are the three the server owns:

- The merged read is a SCAN. `history()` reads the selected app's log, so a merge built on it would
  let whichever app is on screen decide what the Conversation appears to have done. One Thread can
  hand off more than once (#72), so its build turns are spread across every app it drove.
- The order is the `at` stamp both writers apply (#51), and turns written before that stamp existed
  fall back to Chat first, then Build.
- The app card has a producer. `app_change` was a renderer with nothing emitting it; a build turn
  that changes an app now leaves one, carrying the app's name AS IT STOOD THEN. Publish state is
  deliberately not in it — that is a now-question, and it is answered by the rail row, which needed
  a publish time before `Published · <when>` had anything to say.

What is asserted about the two views is only that the block is blind to them: `app_change` is
emitted by the build turn, server-side, and both views render it. The FOLDING — the collapsed run
row, the cross-app merged read on screen — is the unified arm's, and it is held by the JS harness in
`test_the_conversation_view.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.provision.domino import FakeControlPlane
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word for every routed request. The scope classifier is the only caller on this
    path, so this controls exactly one decision."""

    def __init__(self, verdict: str = "BUILD") -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")
    return t


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The two waits a scripted turn can only ever spend — see test_turn_path for the reasoning."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _orch(tmp: Path, turns: list[Turn] | None = None, *, control_plane=None) -> Orchestrator:
    ws = tmp / "mnt" / "code"
    orch = Orchestrator(
        workspace_dir=ws,
        template=_template(tmp),
        gateway=ScriptedGateway(),
        catalog=_catalog(),
        project_id="Sage",
        feedback=OkFeedback(),
        opencode_client=FakeOpenCode(ws, turns) if turns is not None else None,
        control_plane=control_plane,
        domino_project_id="proj-1",
        domino_project_name="Sales dashboard",
    )
    orch.project(start_preview=False)  # attach and seed the first app, without starting Vite
    return orch


def _rows(path: Path, rows: list[dict]) -> None:
    """Write a log by hand. The writers stamp `at` themselves and stamp it to the second, so a test
    about ORDER has to say the times out loud — and a test about entries written before the stamp
    existed can only get one by writing the file the way that version did."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _texts(history: list[dict]) -> list[str]:
    return [f"{row['half']}:{row.get('text') or row.get('type')}" for row in history]


# ---- the app card has a producer -------------------------------------------------------------

def _built(orch: Orchestrator) -> None:
    """The real first-build flow: the gate fires on every project's first build, so plan, approve."""
    list(orch.build_stream("build me a dashboard"))
    list(orch.approve_stream())


def test_a_build_turn_leaves_a_record_of_the_app_it_changed(tmp_path: Path):
    """`app_change` had a renderer, styles and a route, and nothing anywhere emitted one. The whole
    card was a drawing until a build turn started writing this."""
    orch = _orch(tmp_path, [
        Turn(text="A dashboard."),  # the gate turn plans; writing here is a violation
        Turn(text="Built it.", writes={"src/App.tsx": "// built\n"}),
        Turn(text="Filtered it.", writes={"src/App.tsx": "// filtered\n"}),
    ])
    _built(orch)
    app_id = orch.project(start_preview=False).workspace.app_id

    events = list(orch.build_stream("add a date filter"))

    card = next(e for e in events if e["type"] == "app_change")
    assert card["appId"] == app_id
    # The name travels with the block. Read live it would be the app's name TODAY, which is not
    # what this run built.
    assert card["name"]
    # And it is on the record, in the app's own log, so a reload still has it.
    logged = [r for r in orch.history() if r["type"] == "app_change"]
    assert [r["appId"] for r in logged] == [app_id, app_id]  # the first build, then this one


def test_the_card_says_nothing_about_whether_the_app_is_published(tmp_path: Path):
    """Publish state is a now-question. A six-week-old run carrying the answer it had then would
    tell the reader the app is unpublished long after somebody published it."""
    orch = _orch(tmp_path, [
        Turn(text="A dashboard."),  # the gate turn plans; writing here is a violation
        Turn(text="Built it.", writes={"src/App.tsx": "// built\n"}),
    ])
    _built(orch)

    card = next(r for r in orch.history() if r["type"] == "app_change")
    assert set(card) >= {"type", "appId", "name", "app", "at"}
    assert "published" not in card and "publishedAt" not in card


def test_a_turn_that_changed_nothing_leaves_no_card(tmp_path: Path):
    """A question is not a change. The card is the receipt for work, and one after every turn would
    make the receipt worthless."""
    orch = _orch(tmp_path, [
        Turn(text="A dashboard."),  # the gate turn plans; writing here is a violation
        Turn(text="Built it.", writes={"src/App.tsx": "// built\n"}),
        Turn(text="It uses Highcharts."),
    ])
    _built(orch)
    before = len([r for r in orch.history() if r["type"] == "app_change"])
    assert before == 1  # the build that came first did leave one, so "still 1" is a claim

    list(orch.build_stream("what charting library does this use?"))

    assert len([r for r in orch.history() if r["type"] == "app_change"]) == before


# ---- the rail row can say when ----------------------------------------------------------------

def test_an_app_that_was_never_published_has_no_publish_time(tmp_path: Path):
    orch = _orch(tmp_path)
    row = orch.list_apps()[0]

    assert row["published"] is False
    assert row["publishedAt"] == ""


def test_publishing_stamps_the_row_the_card_reads(tmp_path: Path):
    """`published` was a bool and `builtAt` was the only date, so `Published · <when>` had nothing
    to say."""
    orch = _orch(tmp_path, control_plane=FakeControlPlane())

    orch.publish()

    row = orch.list_apps()[0]
    assert row["published"] is True
    assert row["publishedAt"]


def test_a_republish_moves_the_publish_time(tmp_path: Path, monkeypatch):
    """`record_domino_app` runs on the first publish only, so it cannot be where the time lives: a
    re-publish ships a new version to the same App and moves the code behind the URL."""
    import sage.workspace.manager as manager

    orch = _orch(tmp_path, control_plane=FakeControlPlane())
    workspace = orch.project(start_preview=False).workspace
    orch.publish()
    first = workspace.published_at()

    # The clock only reads to the second, so two publishes in one test run share a stamp and the
    # assertion would pass on a re-publish that wrote nothing.
    monkeypatch.setattr(manager, "_now", lambda: "2099-01-01T00:00:00Z")
    again = orch.publish()

    assert again["republished"] is True
    assert workspace.published_at() == "2099-01-01T00:00:00Z" != first


# ---- the merged read --------------------------------------------------------------------------

def _thread_log(orch: Orchestrator, thread_id: str) -> Path:
    return ThreadStore(orch.project(start_preview=False).record.path).history_path(thread_id)


def test_the_merged_read_returns_both_halves_and_says_which_is_which(tmp_path: Path):
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    tid = ThreadStore(project.record.path).create(title="desk")["id"]
    _rows(_thread_log(orch, tid), [{"type": "user", "text": "which desks lost money?",
                                    "at": "2026-01-01T09:00:00Z"}])
    project.workspace.append_history({"type": "user", "text": "add a date filter"}, tid)

    merged = orch.conversation_history(tid)

    assert [row["half"] for row in merged] == ["chat", "build"]
    # Every build row already names its app, so a reader can say which one each turn built.
    assert merged[1]["app"] == project.workspace.app_id


def test_the_merge_is_ordered_by_when_it_happened(tmp_path: Path):
    """Not by half. A Conversation that went back to Chat after a build has to read in that order,
    or the transcript tells a story that never happened."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    tid = ThreadStore(project.record.path).create(title="desk")["id"]
    _rows(_thread_log(orch, tid), [
        {"type": "user", "text": "asked first", "at": "2026-01-01T09:00:00Z"},
        {"type": "user", "text": "asked again", "at": "2026-01-01T11:00:00Z"},
    ])
    _rows(project.workspace.history_path, [
        {"type": "user", "text": "built between", "conversation": tid,
         "app": project.workspace.app_id, "at": "2026-01-01T10:00:00Z"},
    ])

    assert _texts(orch.conversation_history(tid)) == [
        "chat:asked first", "build:built between", "chat:asked again",
    ]


def test_turns_written_before_timestamps_existed_fall_back_to_chat_then_build(tmp_path: Path):
    """#51 stamps every turn from now on. The logs already on disk have none, and two halves with
    no clock between them cannot be interleaved honestly — so they go in the order a Conversation
    with both actually had, because Build only ever started after a handoff out of Chat."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    tid = ThreadStore(project.record.path).create(title="desk")["id"]
    _rows(_thread_log(orch, tid), [
        {"type": "user", "text": "old ask"},
        {"type": "user", "text": "old ask two"},
    ])
    _rows(project.workspace.history_path, [
        {"type": "user", "text": "old build", "conversation": tid, "app": project.workspace.app_id},
        # A stamped turn is newer than every unstamped one, whichever half it is in.
        {"type": "user", "text": "new build", "conversation": tid,
         "app": project.workspace.app_id, "at": "2026-01-01T10:00:00Z"},
    ])

    assert _texts(orch.conversation_history(tid)) == [
        "chat:old ask", "chat:old ask two", "build:old build", "build:new build",
    ]


def test_the_merge_reads_every_app_the_conversation_drove(tmp_path: Path):
    """The failure this closes: `history()` reads the SELECTED app's log, so a merge built on it
    shows whichever app is on screen and hides the rest. One Thread hands off more than once (#72),
    and a Project holds many apps (ADR-0008)."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    first = project.workspace
    second = orch._wm.create_app("Sage")
    tid = ThreadStore(project.record.path).create(title="desk")["id"]
    first.append_history({"type": "user", "text": "built the dashboard"}, tid)
    second.append_history({"type": "user", "text": "built the report"}, tid)

    merged = orch.conversation_history(tid)

    assert {row["app"] for row in merged} == {first.app_id, second.app_id}
    # The read the merge must NOT be built on. It answers for the SELECTED app only, so which half
    # of this Conversation it can see is decided by which row the rail happens to be on.
    assert [r["text"] for r in orch.history(tid)] == ["built the report"]


def test_another_conversations_turns_stay_out_of_it(tmp_path: Path):
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    store = ThreadStore(project.record.path)
    mine, theirs = store.create(title="mine")["id"], store.create(title="theirs")["id"]
    project.workspace.append_history({"type": "user", "text": "mine"}, mine)
    project.workspace.append_history({"type": "user", "text": "theirs"}, theirs)

    assert _texts(orch.conversation_history(mine)) == ["build:mine"]


def test_build_history_written_before_tagging_is_in_the_merge_too(tmp_path: Path):
    """`history()` adopts untagged build entries to the Project's oldest conversation before it
    reads (`_adopt_legacy_build_history`), and `read_history` filters on that tag. A merge that
    skipped the adoption would show an upgraded Project LESS than the split view it replaces —
    Build's whole transcript missing, and only for the people who turned the new view on."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    store = ThreadStore(project.record.path)
    old = store.create(title="first")["id"]
    new = store.create(title="second")["id"]
    _rows(project.workspace.history_path, [{"type": "user", "text": "built before tagging"}])

    assert _texts(orch.conversation_history(old)) == ["build:built before tagging"]
    # And the conversation made after the upgrade still opens empty, which is the point of adopting
    # to the OLDEST one rather than to whoever asks first.
    assert orch.conversation_history(new) == []


def test_a_conversation_with_nothing_in_it_merges_to_nothing(tmp_path: Path):
    """Which is what still leaves Chat on its landing screen."""
    orch = _orch(tmp_path)
    tid = ThreadStore(orch.project(start_preview=False).record.path).create(title="new")["id"]

    assert orch.conversation_history(tid) == []


def test_the_merged_route_answers_beside_the_chat_only_one(tmp_path: Path, monkeypatch):
    """Beside, not instead: `/history` is what the split view still asks, and it has to keep
    getting the same answer it does today."""
    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    tid = ThreadStore(project.record.path).create(title="desk")["id"]
    _rows(_thread_log(orch, tid), [{"type": "user", "text": "asked", "at": "2026-01-01T09:00:00Z"}])
    project.workspace.append_history({"type": "user", "text": "built"}, tid)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    merged = client.get(f"/api/threads/{tid}/conversation").json()["history"]
    chat_only = client.get(f"/api/threads/{tid}/history").json()

    assert _texts(merged) == ["chat:asked", "build:built"]
    assert [r["text"] for r in chat_only] == ["asked"]
