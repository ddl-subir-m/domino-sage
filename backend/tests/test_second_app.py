"""A Project holds a SECOND Built App (ADR-0008, #69).

#67 gave the app a directory of its own. This is the ticket where a Project has two of them: both
sit in `apps/`, the rail lists them, picking one points Sage at it, and building one leaves the
other's code, plan document, Bindings and log exactly as they were.

The list is a directory scan. There is no index file, for the reason `threads.json` no longer
exists: an index is one file with many writers, and two Sage Builders in one Project are two
processes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word for every routed request — the Chat/Build classifier is its only caller."""

    def __init__(self, verdict: str = "CHAT") -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _plan(title: str, step: str) -> str:
    return (f"{title}\n\n"
            "## Plan\n"
            f"1. **{step}** — Show it.\n\n"
            "## Open questions\n"
            "None — ready to build.\n")


_DESK = _plan("A desk exposure dashboard.", "Desk table")
_PNL = _plan("A daily P&L report.", "P&L table")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building this app\n\nSage's rules go here.\n")
    return t


def _orch(tmp: Path, turns: list[Turn] | None = None, *, verdict: str = "CHAT"):
    root = tmp / "mnt" / "code"
    oc = FakeOpenCode(root, turns or [])
    orch = Orchestrator(workspace_dir=root, template=_template(tmp), gateway=ScriptedGateway(verdict),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    return orch, oc, root


def _app_from_chat(orch, ask: str) -> str:
    """Talk, draft, confirm — the whole way a Built App is born. Returns its id."""
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, ask))
    orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, {"resources": False, "artifacts": False, "transcript": False})
    return orch.project(start_preview=False).workspace.app_id


def _two_apps(tmp_path: Path, extra: list[Turn] | None = None):
    """Two conversations, two confirmed handoffs, two Built Apps. The second one is selected,
    because confirming is what put it in front of you."""
    turns = [Turn(text="A dashboard, then."), Turn(text=_DESK),
             Turn(text="A report, then."), Turn(text=_PNL)]
    orch, oc, root = _orch(tmp_path, turns + list(extra or []))
    first = _app_from_chat(orch, "build me a desk dashboard")
    second = _app_from_chat(orch, "now build me a daily P&L report")
    return orch, oc, root, first, second


def test_a_second_confirmed_handoff_leaves_two_built_apps_side_by_side(tmp_path: Path):
    """The failure this ticket closes: a second conversation that wanted a dashboard had nowhere to
    put it, and Reset was the only way to start over."""
    _, _oc, root, first, second = _two_apps(tmp_path)

    assert first != second
    assert sorted(p.name for p in (root / "apps").iterdir()) == sorted([first, second])
    # Each is a whole app, seeded from the template, holding its own plan.
    for app_id, title in ((first, "A desk exposure dashboard."), (second, "A daily P&L report.")):
        app = root / "apps" / app_id
        assert (app / "package.json").read_text() == '{"name": "template"}'
        assert (app / ".sage" / "plan.md").read_text().startswith(title)


def test_confirming_the_same_handoff_twice_reopens_its_app_rather_than_minting_a_twin(
        tmp_path: Path):
    """Confirming is where an app is born, so it makes a new one — but the same sheet confirmed
    twice is one handoff, not two. The plan document already names the app it bound to, which is
    what says so."""
    orch, _oc, root = _orch(tmp_path, [Turn(text="A dashboard, then."), Turn(text=_DESK)])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "build me a desk dashboard"))
    orch.draft_handoff_plan(tid)
    include = {"resources": False, "artifacts": False, "transcript": False}
    orch.confirm_handoff(tid, include)
    born = orch.project(start_preview=False).workspace.app_id

    orch.confirm_handoff(tid, include)

    assert [p.name for p in (root / "apps").iterdir()] == [born]
    assert orch.project(start_preview=False).workspace.app_id == born


def test_the_app_list_is_a_directory_scan_with_no_index_file(tmp_path: Path):
    """No `apps.json`. An index is one file with many writers, which is what a Project with two
    Sage Builders in it keeps losing work to (ADR-0008)."""
    orch, _oc, root, first, second = _two_apps(tmp_path)

    assert [row["id"] for row in orch.list_apps()] == [first, second]   # oldest first

    written = {p.name for p in (root / ".sage").iterdir()} | {p.name for p in (root / "apps").iterdir()}
    assert "apps.json" not in written
    assert not [p for p in (root / "apps").iterdir() if p.is_file()]

    # And the scan is the only source: an app directory put there by hand is in the list, and one
    # taken away leaves it, with nothing to keep in step.
    (root / "apps" / "app_deadbeef").mkdir()
    assert "app_deadbeef" in [row["id"] for row in orch.list_apps()]
    (root / "apps" / "app_deadbeef").rmdir()
    assert [row["id"] for row in orch.list_apps()] == [first, second]


def test_selecting_an_app_switches_the_project_to_it(tmp_path: Path):
    orch, _oc, _root, first, second = _two_apps(tmp_path)

    assert orch.project(start_preview=False).workspace.app_id == second
    assert [row["id"] for row in orch.list_apps() if row["selected"]] == [second]

    row = orch.select_app(first)

    assert row["id"] == first
    assert orch.project(start_preview=False).workspace.app_id == first
    assert orch.project(start_preview=False).workspace.path.name == first
    assert [r["id"] for r in orch.list_apps() if r["selected"]] == [first]


def test_selecting_an_app_that_is_not_there_is_refused(tmp_path: Path):
    orch, _oc, _root, _first, second = _two_apps(tmp_path)
    with pytest.raises(KeyError):
        orch.select_app("app_nosuchthing")
    assert orch.project(start_preview=False).workspace.app_id == second


def test_selecting_on_a_project_with_no_apps_is_refused_rather_than_crashing(tmp_path: Path):
    """The id a Project with no app answers with is one this process minted for a directory that
    does not exist yet. Naming it is naming nothing, and has to say so."""
    orch, _oc, root = _orch(tmp_path)
    minted = orch.project(start_preview=False, seed_app=False).workspace.app_id

    assert not (root / "apps").exists()
    with pytest.raises(KeyError):
        orch.select_app(minted)


def test_an_unconfirmed_plan_never_becomes_another_apps_plan(tmp_path: Path):
    """A plan document names the app it bound to, and one that names none is a FALLBACK rather
    than a peer. Drafted in Chat after an app was built it is the NEWEST document, so mixed into
    one list it would become what that app's pin names and what a bare approval builds."""
    turns = [Turn(text="A dashboard, then."), Turn(text=_DESK),
             Turn(text="A report, then."), Turn(text=_PNL)]
    orch, _oc, _root = _orch(tmp_path, turns)
    first = _app_from_chat(orch, "build me a desk dashboard")

    # A second conversation drafts a plan and stops there — no confirm, so no app and no reference.
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "now build me a daily P&L report"))
    orch.draft_handoff_plan(tid)

    assert orch.read_plan_doc("002")["appId"] == ""          # newer, and bound to nothing
    assert orch.project(start_preview=False).workspace.app_id == first
    assert orch.read_plan_pin()["planId"] == "001"           # still the app's own plan


def test_a_build_in_one_app_leaves_the_other_untouched(tmp_path: Path):
    """Code, plan document, Bindings and log. The whole reason a Built App got a directory."""
    orch, oc, root, first, second = _two_apps(tmp_path, [Turn(text="Built it.",
                                                              writes={"src/App.tsx": "// P&L\n"})])
    idle = root / "apps" / first
    orch.project(start_preview=False).workspace.update_bindings(
        lambda rows: rows + [{"kind": "llm", "id": "gpt", "name": "gpt"}])
    before = {
        "code": (idle / "src" / "App.tsx").read_text(),
        "plan": (idle / ".sage" / "plan.md").read_text(),
        "bindings": (idle / ".sage" / "bindings.json").exists(),
        "log": (idle / ".sage" / "history.jsonl").read_text(),
        "docs": orch.read_plan_doc("001"),
    }

    list(orch.approve_stream())

    busy = root / "apps" / second
    assert (busy / "src" / "App.tsx").read_text() == "// P&L\n"
    assert oc.sessions[-1]["directory"] == str(busy)     # and that is where the agent stood
    assert (idle / "src" / "App.tsx").read_text() == before["code"]
    assert (idle / ".sage" / "plan.md").read_text() == before["plan"]
    assert (idle / ".sage" / "bindings.json").exists() == before["bindings"]
    assert (idle / ".sage" / "history.jsonl").read_text() == before["log"]
    assert orch.read_plan_doc("001") == before["docs"]
    # The Bindings the build's app recorded are its own, and the idle app never sees them.
    assert [b["id"] for b in orch.list_bindings()] == ["gpt"]


def test_an_app_display_name_is_editable_and_its_id_is_not(tmp_path: Path):
    """The id is the directory's name and never changes: Domino fixes a published App's
    `entryPoint` when the App is created. The name is the mutable half, and it starts as the title
    of the plan the app was built from."""
    orch, _oc, root, first, second = _two_apps(tmp_path)

    by_id = {row["id"]: row for row in orch.list_apps()}
    assert by_id[first]["name"] == "A desk exposure dashboard."
    assert by_id[second]["name"] == "A daily P&L report."

    renamed = orch.rename_app(first, "Desk exposure")

    assert renamed["id"] == first
    assert renamed["name"] == "Desk exposure"
    assert {row["id"] for row in orch.list_apps()} == {first, second}
    assert (root / "apps" / first).is_dir()          # the directory did not move
    # And it survives a restart, because it is kept inside the app.
    restarted, _oc2, _root = _orch(tmp_path)
    assert {row["id"]: row["name"] for row in restarted.list_apps()}[first] == "Desk exposure"


def test_renaming_refuses_an_empty_name_and_an_app_that_is_not_there(tmp_path: Path):
    orch, _oc, _root, first, _second = _two_apps(tmp_path)
    with pytest.raises(ValueError):
        orch.rename_app(first, "   ")
    with pytest.raises(KeyError):
        orch.rename_app("app_nosuchthing", "Desk exposure")


def test_a_build_session_belongs_to_a_conversation_and_an_app(tmp_path: Path):
    """One conversation can build into several apps, and an OpenCode session is opened on ONE
    directory — so a session recovered for the app you just left would stand the agent in the wrong
    tree."""
    orch, _oc, _root, first, second = _two_apps(tmp_path)
    record = orch.project(start_preview=False).record

    record.write_session_id("ses_desk", "thr_a", first)
    record.write_session_id("ses_pnl", "thr_a", second)

    assert record.read_session_id("thr_a", first) == "ses_desk"
    assert record.read_session_id("thr_a", second) == "ses_pnl"


def test_the_build_rail_reads_apps_and_the_chat_rail_still_reads_threads(tmp_path: Path, monkeypatch):
    """Two lists, one per mode (ADR-0008). Both are scans, and neither is the other."""
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as appmod

    orch, _oc, _root, first, second = _two_apps(tmp_path)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    with TestClient(appmod.control_app) as client:
        apps = client.get("/api/apps").json()
        assert [row["id"] for row in apps["items"]] == [first, second]
        assert apps["selected"] == second
        assert len(client.get("/api/threads").json()) == 2

        assert client.post(f"/api/apps/{first}/select").json()["app"]["id"] == first
        assert client.get("/api/apps").json()["selected"] == first

        renamed = client.patch(f"/api/apps/{first}", json={"name": "Desk exposure"})
        assert renamed.json()["name"] == "Desk exposure"
        assert client.patch(f"/api/apps/{first}", json={"name": ""}).status_code == 400
        assert client.post("/api/apps/app_nosuchthing/select").status_code == 404
