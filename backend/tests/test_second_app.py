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
from unittest import mock

import pytest

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator, TurnBusy
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore

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


_NOTHING_EXTRA = {"resources": False, "artifacts": False, "transcript": False}


def _app_from_chat(orch, ask: str, target: dict | None = None) -> str:
    """Talk, draft, confirm — the whole way a Built App is born. Returns the app it landed in.

    `target` is the sheet's answer to "which app": None is New app, which is what it is for every
    caller that does not say otherwise, the same way the sheet defaults (#73)."""
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, ask))
    orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, _NOTHING_EXTRA, target)
    return orch.project(start_preview=False).workspace.app_id


def _two_apps(tmp_path: Path, extra: list[Turn] | None = None):
    """Two conversations, two confirmed handoffs, two Built Apps. The second one is selected,
    because confirming is what put it in front of you."""
    # No reply scripted for the desk conversation: "build me a desk dashboard" is an explicit
    # build request, so it goes straight to the sheet without reaching sage-chat. The P&L one
    # is asked in ordinary words and still spends a turn, so it keeps its reply.
    turns = [Turn(text=_DESK),
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


def test_one_conversation_that_hands_off_twice_leaves_two_built_apps(tmp_path: Path):
    """The second Built App does not need a second conversation (#72). A Thread's handoff record is
    a list, so the second handoff writes its own plan into its own app and the first one's plan
    stays where the first app can still read it."""
    orch, _oc, root = _orch(tmp_path, [Turn(text="A dashboard, then."), Turn(text=_DESK),
                                       Turn(text="A report, then."), Turn(text=_PNL)],
                            verdict="APP")
    include = {"resources": False, "artifacts": False, "transcript": False}
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "put the desk exposure on a dashboard colleagues can open"))
    orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, include)
    first = orch.project(start_preview=False).workspace.app_id

    list(orch.chat_stream(tid, "we also need a daily P&L report for the desk heads"))
    orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, include)
    second = orch.project(start_preview=False).workspace.app_id

    assert first != second
    assert sorted(p.name for p in (root / "apps").iterdir()) == sorted([first, second])
    assert (root / "apps" / first / ".sage" / "plan.md").read_text().startswith(
        "A desk exposure dashboard.")
    assert (root / "apps" / second / ".sage" / "plan.md").read_text().startswith(
        "A daily P&L report.")
    # And each entry says which app it built, so neither handoff is anonymous afterwards.
    store = ThreadStore(orch.project(start_preview=False).record.path)
    assert [e["appId"] for e in store.read_handoffs(tid)] == [first, second]


def test_confirming_the_same_handoff_twice_reopens_its_app_rather_than_minting_a_twin(
        tmp_path: Path):
    """Confirming is where an app is born, so it makes a new one — but the same sheet confirmed
    twice is one handoff, not two. The plan document already names the app it bound to, which is
    what says so."""
    orch, _oc, root = _orch(tmp_path, [Turn(text=_DESK)])
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
    turns = [Turn(text=_DESK),
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


# ---- Which app a handoff builds into (#73) ----
#
# The sheet asks, and New app is the answer it starts on. What makes that safe is that the DEFAULT
# lives in the contract rather than the markup: a confirm that names no app gets a new one, so a
# change to the sheet cannot quietly point a build at somebody else's dashboard.


def _draft(orch, ask: str) -> tuple[str, dict]:
    """A Thread with a plan drafted and the sheet's payload, stopping short of confirming."""
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, ask))
    return tid, orch.draft_handoff_plan(tid)


def test_the_sheet_lists_the_projects_apps_and_preselects_none_of_them(tmp_path: Path):
    """Criterion 11: the payload carries the apps to choose between and no choice among them."""
    orch, _oc, _root, first, second = _two_apps(tmp_path, [Turn(text="A third, then."),
                                                           Turn(text=_DESK)])

    _tid, sheet = _draft(orch, "and one more dashboard")

    assert [row["id"] for row in sheet["apps"]] == [first, second]
    assert [row["name"] for row in sheet["apps"]] == ["A desk exposure dashboard.",
                                                      "A daily P&L report."]
    # Not one field naming a target. `selected` is the Build rail's, and in the sheet it would be
    # exactly the preselect this row exists to prevent.
    for row in sheet["apps"]:
        assert "selected" not in row
        # `published`, `building`, `behind` and the published App's `url` ride along because a row
        # is one shape everywhere (#76, #77, #78, #89). Each says what is true of the app rather
        # than what the sheet should do with it, which is why none of them is stripped the way
        # `selected` is.
        assert set(row) == {"id", "name", "built", "builtAt", "planId", "published", "publishedAt",
                            "url", "building", "behind"}


def test_an_app_row_carries_the_date_of_its_last_build(tmp_path: Path):
    """Two dashboards read the same in a list; which one is still alive is the date."""
    orch, _oc, _root, _first, second = _two_apps(tmp_path, [Turn(text="Built it.",
                                                                 writes={"src/App.tsx": "// 1\n"}),
                                                            Turn(text="Built it again.",
                                                                 writes={"src/App.tsx": "// 2\n"})])
    by_id = {row["id"]: row for row in orch.list_apps()}
    assert by_id[second]["built"] is False
    assert by_id[second]["builtAt"] == ""  # never built, so no date rather than a wrong one

    list(orch.approve_stream())
    first_build = {row["id"]: row for row in orch.list_apps()}[second]
    assert first_build["built"] is True
    assert first_build["builtAt"]

    # LAST built, not first: the stamp moves with every build, or the list ages while the app does not.
    workspace = orch.project(start_preview=False).workspace
    with mock.patch("sage.workspace.manager._now", return_value="2099-01-01T00:00:00Z"):
        workspace.mark_built()
    assert {row["id"]: row for row in orch.list_apps()}[second]["builtAt"] == "2099-01-01T00:00:00Z"

    # And Reset takes it away with the code it described.
    workspace.clear_built()
    reset = {row["id"]: row for row in orch.list_apps()}[second]
    assert (reset["built"], reset["builtAt"]) == (False, "")


def test_confirming_with_no_target_builds_a_new_app(tmp_path: Path):
    """Criterion 9, and the reason the default is the server's: every way of saying nothing —
    no target, an empty one, an empty id — means New app, so a sheet that forgets to send one
    cannot land on an existing app."""
    orch, _oc, root, first, second = _two_apps(tmp_path, [Turn(text="A third, then."), Turn(text=_DESK),
                                                          Turn(text="A fourth, then."), Turn(text=_PNL)])

    third = _app_from_chat(orch, "a third dashboard", {})
    fourth = _app_from_chat(orch, "a fourth dashboard", {"appId": ""})

    assert len({first, second, third, fourth}) == 4
    assert sorted(p.name for p in (root / "apps").iterdir()) == sorted([first, second, third, fourth])
    # The apps that were already there are untouched.
    assert (root / "apps" / first / ".sage" / "plan.md").read_text().startswith("A desk exposure dashboard.")
    assert (root / "apps" / second / ".sage" / "plan.md").read_text().startswith("A daily P&L report.")


def test_confirming_with_an_existing_app_builds_into_that_app(tmp_path: Path):
    """The other half of the row. Picking one is deliberate, so it replaces that app's plan and
    mints nothing."""
    orch, _oc, root, first, second = _two_apps(tmp_path, [Turn(text="A rewrite, then."), Turn(text=_PNL)])
    # Work already in the app it is going to land in. Confirming replaces the plan, not the app:
    # the code stays until the person approves that plan and builds (docs/workbench/handoff.md §4).
    (root / "apps" / first / "src" / "App.tsx").write_text("// the desk one\n")

    landed = _app_from_chat(orch, "redo the desk dashboard as a P&L one", {"appId": first})

    assert landed == first
    assert (root / "apps" / first / "src" / "App.tsx").read_text() == "// the desk one\n"
    assert sorted(p.name for p in (root / "apps").iterdir()) == sorted([first, second])
    assert (root / "apps" / first / ".sage" / "plan.md").read_text().startswith("A daily P&L report.")
    # The app it did not name keeps its plan, and the one it did keeps the name a person gave it.
    assert (root / "apps" / second / ".sage" / "plan.md").read_text().startswith("A daily P&L report.")
    assert {row["id"]: row["name"] for row in orch.list_apps()}[first] == "A desk exposure dashboard."
    assert orch.project(start_preview=False).workspace.app_id == first  # Build lands on it


def test_a_bound_sheet_answered_again_honours_the_app_it_names(tmp_path: Path):
    """The sheet is served again on a Thread that already bound (`_draft_handoff_plan`), so the
    app that entry bound cannot be allowed to win: it would swallow the answer to the question the
    target row asks, which is criterion 11's failure coming the other way. The old app is the
    FALLBACK, which is what a double-confirm — the same sheet, saying nothing — still lands on."""
    orch, _oc, root, first, second = _two_apps(tmp_path, [Turn(text=_PNL)])

    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "build me a third dashboard"))
    orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, _NOTHING_EXTRA)
    third = orch.project(start_preview=False).workspace.app_id
    assert third not in (first, second)

    # Open in Build again on the bound Thread, and this time pick an app.
    orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, _NOTHING_EXTRA, {"appId": first})
    assert orch.project(start_preview=False).workspace.app_id == first
    assert sorted(p.name for p in (root / "apps").iterdir()) == sorted([first, second, third])

    # Saying nothing still reaches the entry's own app rather than minting a twin.
    orch.confirm_handoff(tid, _NOTHING_EXTRA)
    assert orch.project(start_preview=False).workspace.app_id == first
    assert sorted(p.name for p in (root / "apps").iterdir()) == sorted([first, second, third])


def test_confirming_into_an_app_that_is_not_there_is_refused_and_builds_nothing(tmp_path: Path):
    """A named app that has gone is refused rather than turned into a new one: the person picked a
    target, and building somewhere else is the surprise this whole row prevents."""
    orch, _oc, root, first, second = _two_apps(tmp_path, [Turn(text="A third, then."), Turn(text=_DESK)])

    tid, _sheet = _draft(orch, "and one more dashboard")
    with pytest.raises(ValueError):
        orch.confirm_handoff(tid, _NOTHING_EXTRA, {"appId": "app_nosuchthing"})

    assert sorted(p.name for p in (root / "apps").iterdir()) == sorted([first, second])
    store = ThreadStore(orch.project(start_preview=False).record.path)
    assert store.read_handoff(tid)["status"] == "planned"  # not bound, so the sheet can be answered again


def test_confirming_leaves_the_projects_own_name_alone(tmp_path: Path):
    """The Default rename is gone (#73). It renamed the Project to the plan title, which was a
    Project-per-app rule: a Project holds many apps now, and two of them cannot share one name.
    The plan title names the APP."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text=_DESK)])
    record = orch.project(start_preview=False).record
    record.mark_untitled(True)

    born = _app_from_chat(orch, "build me a desk dashboard")

    assert record.is_untitled() is True
    assert record.display_name() == "Default"
    assert {row["id"]: row["name"] for row in orch.list_apps()}[born] == "A desk exposure dashboard."


def test_the_confirm_route_carries_the_target_the_sheet_picked(tmp_path: Path, monkeypatch):
    """The wire between the sheet and the rule. A target that did not reach the orchestrator would
    fall back to New app and look like it worked, so the route is asserted rather than assumed."""
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as appmod

    orch, _oc, root, first, second = _two_apps(tmp_path, [Turn(text="A rewrite, then."), Turn(text=_PNL)])
    monkeypatch.setattr(appmod, "orchestrator", orch)

    tid, sheet = _draft(orch, "redo the desk dashboard")
    assert [row["id"] for row in sheet["apps"]] == [first, second]

    with TestClient(appmod.control_app) as client:
        missing = client.post(f"/api/threads/{tid}/handoff/confirm",
                              json={"include": _NOTHING_EXTRA, "target": {"appId": "app_nosuchthing"}})
        assert missing.status_code == 400

        done = client.post(f"/api/threads/{tid}/handoff/confirm",
                           json={"include": _NOTHING_EXTRA, "target": {"appId": first}})
        assert done.json()["handoff"]["appId"] == first

    assert sorted(p.name for p in (root / "apps").iterdir()) == sorted([first, second])



# ---- New app from the Build rail (#74) ----
#
# A person already in Build starts something new without going to Chat first to earn the right.
# No new gate comes with it: the plan gate fires on the first build of an app that has not been
# built, so a fresh app lands on the same review a handoff earns, reached from the other side.


_CHART = _plan("A burndown chart.", "Burndown chart")


def test_new_app_in_the_build_rail_creates_and_selects_one_with_no_thread_behind_it(tmp_path: Path):
    """Criterion 1. Nothing is asked for on the way in — no Thread, no plan, no handoff."""
    orch, _oc, root, first, second = _two_apps(tmp_path)

    row = orch.create_app()

    born = row["id"]
    assert born not in (first, second)
    assert (root / "apps" / born / "src" / "App.tsx").exists()      # seeded from the template
    assert row["selected"] is True
    assert row["built"] is False
    assert orch.project(start_preview=False).workspace.app_id == born
    # Two Threads went into the two apps that came from a handoff. This one added none.
    assert len(ThreadStore(orch.project(start_preview=False).record.path).list()) == 2
    assert row["planId"] == ""


def test_an_app_started_from_build_sits_in_the_rail_beside_the_ones_from_a_handoff(tmp_path: Path):
    """Criterion 4. The list is a directory scan, so where an app came from is not a thing the
    rail can tell — which is the point: one kind of app, several ways in."""
    orch, _oc, _root, first, second = _two_apps(tmp_path)

    born = orch.create_app()["id"]

    rows = orch.list_apps()
    assert [r["id"] for r in rows] == [first, second, born]
    assert [r["id"] for r in rows if r["selected"]] == [born]
    # Named for what it is, because there is no plan to borrow a title from yet.
    assert {r["id"]: r["name"] for r in rows}[born] == "Unnamed Built App"
    # And it survives a restart, because the app is a directory rather than a row in an index.
    restarted, _oc2, _root2 = _orch(tmp_path)
    assert [r["id"] for r in restarted.list_apps()] == [first, second, born]


def test_the_first_turn_on_an_app_started_from_build_gates_on_a_plan(tmp_path: Path):
    """Criterion 2. `_should_gate` keys on has_built, and a minted app has built nothing — so the
    existing gate does the work and this ticket adds no second one."""
    orch, _oc, root, _first, second = _two_apps(
        tmp_path, [Turn(text=_CHART), Turn(text="Built it.", writes={"src/App.tsx": "// burndown\n"})])
    born = orch.create_app()["id"]

    events = list(orch.build_stream("build me a burndown chart"))

    assert next(e for e in events if e["type"] == "done")["decision"] == "awaiting approval"
    assert "Burndown chart" in next(e for e in events if e["type"] == "plan-proposed")["plan"]
    # Read-only, as a gated turn is: the plan is written, the app is not.
    app = root / "apps" / born
    assert (app / ".sage" / "plan.md").read_text().startswith("A burndown chart.")
    assert "burndown" not in (app / "src" / "App.tsx").read_text()
    # The gate stamped the app it stood in, so the document belongs to this app and no other.
    assert orch.read_plan_doc("003")["appId"] == born
    assert orch.read_plan_doc("002")["appId"] == second


def test_approving_that_plan_builds_into_the_new_app_and_no_other(tmp_path: Path):
    """Criterion 3. The whole reason to mint an app rather than build into the one on screen."""
    orch, oc, root, first, second = _two_apps(
        tmp_path, [Turn(text=_CHART), Turn(text="Built it.", writes={"src/App.tsx": "// burndown\n"})])
    born = orch.create_app()["id"]
    idle = {app_id: (root / "apps" / app_id / "src" / "App.tsx").read_text()
            for app_id in (first, second)}

    list(orch.build_stream("build me a burndown chart"))
    list(orch.approve_stream())

    assert (root / "apps" / born / "src" / "App.tsx").read_text() == "// burndown\n"
    assert oc.sessions[-1]["directory"] == str(root / "apps" / born)   # where the agent stood
    for app_id, before in idle.items():
        assert (root / "apps" / app_id / "src" / "App.tsx").read_text() == before


def test_new_app_is_refused_while_a_build_is_running_and_mints_nothing(tmp_path: Path):
    """A turn holds one working tree, so starting an app is refused the way switching to one is.
    Refused BEFORE the mint: a half-born app would sit in the rail with nothing pointed at it."""
    orch, _oc, root, first, second = _two_apps(tmp_path)

    assert orch._turn_lock.acquire(blocking=False)      # simulate a turn in flight
    try:
        with pytest.raises(TurnBusy):
            orch.create_app()
    finally:
        orch._turn_lock.release()

    assert sorted(p.name for p in (root / "apps").iterdir()) == sorted([first, second])
    assert orch.project(start_preview=False).workspace.app_id == second


def test_the_rail_starts_an_app_over_the_route_that_lists_them(tmp_path: Path, monkeypatch):
    """The button's wire. A refusal answers 409, which is the one thing the rail has to tell from a
    failure it cannot wait out."""
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as appmod

    orch, _oc, _root, first, second = _two_apps(tmp_path)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    with TestClient(appmod.control_app) as client:
        born = client.post("/api/apps").json()["id"]

        assert [row["id"] for row in client.get("/api/apps").json()["items"]] == [first, second, born]
        assert client.get("/api/apps").json()["selected"] == born

        assert orch._turn_lock.acquire(blocking=False)
        try:
            refused = client.post("/api/apps")
        finally:
            orch._turn_lock.release()
        assert refused.status_code == 409
        assert "A build is already running" in refused.json()["error"]
