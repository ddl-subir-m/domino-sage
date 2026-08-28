"""Starting over is its own action (#36).

Before this, "rebuild this app from scratch, remove everything you have built" was only a sentence
handed to the build agent — and a build agent builds. Live on 2026-08-24 it wrote the user a landing
page saying "Ready to rebuild from scratch", which is the most literal thing those words describe.

Two halves: a reset that keeps what the user set up, and a phrase rule that OFFERS it rather than
running it. The second half is deliberate — a reset throws the app away, and putting a destructive
action behind a heuristic is the shape of #29.

The contract has not changed since. What narrowed is its reach (#75): a Project holds many Built
Apps, so a reset replaces the app in front of the person and leaves the rest of the Project — the
other apps and the plans that name them — where they were.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.orchestrator.service import Mode, Orchestrator, ResetBusy, _asks_to_reset

from .test_attach_upload import _catalog, _template


def _orch(tmp: Path) -> Orchestrator:
    return Orchestrator(workspace_dir=tmp / "mnt" / "code", template=_template(tmp),
                        gateway=object(), catalog=_catalog(), project_id="Sage",
                        assets=FakeAssetProvider())


def test_reset_replaces_the_app_and_keeps_what_the_user_set_up(tmp_path: Path):
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ws = project.workspace.path
    orch.upload_file("q3.csv", b"region,revenue\nwest,10\neast,20\n")
    attached = project.attached[0]["path"]
    orch.write_instructions(project, "Always label axes in full.")
    (ws / "src" / "App.tsx").write_text("export default function App() { return <b>built</b>; }")
    (ws / "src" / "Dashboard.tsx").write_text("export const D = 1;")     # a file the agent added
    (ws / ".sage" / "queries.json").write_text('{"top_regions": "select 1"}')
    # A document that belongs to an app names it, which is what every write of one does: the BUILD
    # gate stamps it, and a Chat handoff stamps it on confirm.
    project.record.create_plan_doc("A dashboard.\n", title="A dashboard.",
                                   app_id=project.workspace.app_id)
    project.workspace.mark_built()

    orch.reset_app()

    # The app is the starter template again — the agent's own files are gone, not just overwritten.
    assert (ws / "src" / "App.tsx").read_text() == (orch._wm.template / "src" / "App.tsx").read_text()
    assert not (ws / "src" / "Dashboard.tsx").exists()
    # The user's setup survives: the attachment, its manifest, and their project instructions.
    assert (ws / attached).is_symlink()
    assert [e["path"] for e in json.loads((ws / ".sage" / "attachments.json").read_text())] == [attached]
    assert orch.read_instructions(project) == "Always label axes in full."
    # The app's own Sage metadata goes with the app.
    assert not (ws / ".sage" / "queries.json").exists()
    # Including the plan documents (ADR-0007). They are durable across builds, not across a Reset:
    # they describe the app that just went away, and the next build would be planned from them.
    # They live with the Project rather than in the app, so this asks the surface that owns them.
    assert orch.list_plan_docs() == []
    # And the next build is planned like a first build, because the app really is new again.
    assert project.workspace.has_built() is False


def test_resetting_one_app_leaves_the_other_one_exactly_as_it_was(tmp_path: Path):
    """Code, plan document, Bindings and log — the four things a Built App owns (ADR-0008, #75).

    Reset was the only way to start over when a Project was an app, and it took that app's code,
    its documents and its queries with it. A Project holds many now, and starting one over must not
    be a way to lose the others.
    """
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    first = project.workspace.app_id
    kept = project.record.create_plan_doc("A daily P&L report.\n", title="A daily P&L report.",
                                          app_id=first)["id"]
    project.workspace.update_bindings(lambda rows: rows + [{"kind": "llm", "id": "gpt", "name": "gpt"}])
    project.workspace.append_history({"type": "user", "text": "build the P&L report"})
    (project.workspace.path / "src" / "Report.tsx").write_text("export const R = 1;")
    project.workspace.mark_built()
    idle = project.workspace           # a frozen value: it keeps naming this app after the switch

    second = orch.create_app()["id"]   # minted, seeded and selected — the app in front of them now
    live = orch.project(start_preview=False).workspace
    project.record.create_plan_doc("A desk exposure dashboard.\n",
                                   title="A desk exposure dashboard.", app_id=second)
    (live.path / "src" / "Desk.tsx").write_text("export const D = 1;")

    orch.reset_app()

    # The app in front of them is the starter template again, and its document went with it.
    assert orch.project(start_preview=False).workspace.app_id == second
    assert not (live.path / "src" / "Desk.tsx").exists()
    assert live.has_built() is False
    # The other one is untouched, down to the plan document that names it.
    assert (idle.path / "src" / "Report.tsx").read_text() == "export const R = 1;"
    assert [d["id"] for d in orch.list_plan_docs()] == [kept]
    assert [b["id"] for b in idle.read_bindings()] == ["gpt"]
    assert [e["text"] for e in idle.read_history() if e["type"] == "user"] == ["build the P&L report"]
    assert idle.has_built() is True


def test_a_plan_drafted_in_chat_is_not_the_reset_apps_to_take(tmp_path: Path):
    """A document that names no app has not been handed off to one, so it is nobody's to reset.

    It is the FALLBACK a build reads when this app has no document of its own — and after a reset
    the app really is new, which is the case that fallback is already for.
    """
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    draft = project.record.create_plan_doc("A dashboard.\n", title="A dashboard.")["id"]

    orch.reset_app()

    assert [d["id"] for d in orch.list_plan_docs()] == [draft]


def test_reset_leaves_a_line_in_the_transcript_it_keeps(tmp_path: Path):
    # The conversation survives a reset, so the record has to say the reset happened — the agent
    # greps .sage/history.md, and without a marker it would go on building from a description of
    # code it can no longer read.
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)

    orch.reset_app()

    assert [e for e in project.workspace.read_history() if e["type"] == "app-reset"]


def test_reset_refuses_while_a_turn_is_streaming(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    assert orch._turn_lock.acquire(blocking=False)
    try:
        with pytest.raises(ResetBusy):
            orch.reset_app()
    finally:
        orch._turn_lock.release()


def test_a_build_nobody_stopped_still_fails_at_once(tmp_path: Path):
    # The wait below is for a turn that is already unwinding. Waiting on one that is simply running
    # would be a slower way to say the same sentence, and the sentence is the useful part.
    import time as _time

    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    assert orch._turn_lock.acquire(blocking=False)
    try:
        started = _time.monotonic()
        with pytest.raises(ResetBusy):
            orch.reset_app()
        assert _time.monotonic() - started < 1.0
    finally:
        orch._turn_lock.release()


def test_reset_waits_out_a_stop_that_is_still_unwinding(tmp_path: Path):
    """The live failure on 2026-08-24: Stop, then Reset, then "a build is running — stop it".

    stop_build() sets the flag, interrupts the session and returns; the turn releases the lock
    seconds later, after it reverts the files and finishes its git work. Reset used to fail instantly
    in that window, telling the user to do the thing they had just done.
    """
    import threading
    import time as _time

    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    assert orch._turn_lock.acquire(blocking=False)
    project.stop_requested = True          # what stop_build sets before it returns

    def unwind() -> None:
        # The real ordering, and the whole trap: handle_stop clears the flag FIRST, then the turn
        # reverts the files and finishes its git work, and only then is the lock released. A wait
        # that polled `stop_requested` as its loop condition would give up in that gap — which is
        # what a first cut of this did, and what made it flaky under load rather than plainly wrong.
        _time.sleep(0.2)
        project.stop_requested = False     # cleared by the turn's own handle_stop
        _time.sleep(0.4)                   # reverting, committing — still holding the lock
        orch._turn_lock.release()

    threading.Thread(target=unwind, daemon=True).start()
    assert orch.reset_app()["ok"] is True


def test_a_turn_that_never_unwinds_still_gives_up(tmp_path: Path):
    # Bounded, so a wedged turn cannot hold the button open forever with no explanation.
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    assert orch._turn_lock.acquire(blocking=False)
    project.stop_requested = True
    try:
        assert orch._acquire_for_reset(wait=0.3) is False
    finally:
        orch._turn_lock.release()


@pytest.mark.parametrize("prompt", [
    "lets rebuild this app from scratch again remove everything you have built",
    "start over",
    "ok lets start from scratch",
    "delete everything and start again",
    "reset the app",
    "wipe the whole app please",
])
def test_a_request_to_start_over_is_recognised(prompt):
    assert _asks_to_reset(prompt) is True


@pytest.mark.parametrize("prompt", [
    "remove the severity filter",
    "delete the dataset from the UI",
    "clear the search box when the user presses escape",
    "build a dashboard from scratch data",   # 'from scratch data', not a request to start over
    "start a new tab for adverse events",
])
def test_an_ordinary_change_is_not_a_reset(prompt):
    assert _asks_to_reset(prompt) is False


@pytest.mark.parametrize("prompt", [
    # The live false positive on 2026-08-24, verbatim. They had just used the button, said so, and
    # asked for the app back — and were handed the button again.
    "i reset the app build me the dashboard with @synthetic_adverse_events.csv again",
    "i just reset the app, now build a dashboard",
    "i've reset the app already, build the tickets view again",
    "we already reset the app - please rebuild it",
])
def test_reporting_a_reset_you_already_did_is_not_asking_for_one(prompt):
    assert _asks_to_reset(prompt) is False


@pytest.mark.parametrize("prompt", [
    # A first-person subject is what separates a report from a request, and these are requests:
    # nothing here puts "i" directly in front of the verb.
    "i want to reset the app",
    "i'd like to reset the app please",
    "can you reset the app",
])
def test_asking_for_a_reset_in_the_first_person_is_still_a_request(prompt):
    assert _asks_to_reset(prompt) is True


def test_a_prompt_can_report_one_reset_and_ask_for_another():
    # Only the reported clause is cut out, not the whole prompt — otherwise naming the button once
    # would buy a free pass for everything after it.
    assert _asks_to_reset("i reset the app but now delete everything") is True


def test_a_reset_request_offers_the_control_and_never_resets(tmp_path: Path):
    # The whole point of the phrase half: it stops the turn and hands back the button. Nothing is
    # deleted on a heuristic, and no inference is spent either.
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    (project.workspace.path / "src" / "App.tsx").write_text("export default () => <b>built</b>;")
    built = []
    orch._build_stream = lambda *a, **k: (built.append(1), iter([]))[1]  # type: ignore[method-assign]

    events = list(orch.build_stream("ok lets rebuild the app from scratch, remove everything"))

    assert built == []                                    # no build turn ran
    assert [e["type"] for e in events] == ["reset-offer", "done"]
    assert "starter template" in events[0]["message"]      # says what the control it offers does
    assert (project.workspace.path / "src" / "App.tsx").read_text() == "export default () => <b>built</b>;"


def test_the_offer_carries_the_turn_its_buttons_have_to_replay(tmp_path: Path):
    # "clear everything and build X from @data.csv" is ONE request. The buttons on the offer re-send
    # it after resetting, so the offer has to hand back the mentions too — an offer that returns only
    # the prompt makes the user re-attach and retype the half the reset didn't answer.
    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    orch._build_stream = lambda *a, **k: iter([])  # type: ignore[method-assign]

    prompt = "clear everything and build the dashboard from @public/data/clicks.csv"
    offer = next(e for e in orch.build_stream(prompt, ["public/data/clicks.csv"])
                 if e["type"] == "reset-offer")

    assert offer["prompt"] == prompt
    assert offer["mentions"] == ["public/data/clicks.csv"]


def test_answering_the_offer_builds_instead_of_offering_again(tmp_path: Path):
    # Without this the buttons loop: the re-sent prompt still says "clear everything", still matches,
    # and the user gets the same offer they just answered. The gate is skipped only because it already
    # ran for this exact prompt and a button — not a heuristic — decided what happens next.
    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    built = []
    orch._build_stream = lambda *a, **k: (built.append(1), iter([]))[1]  # type: ignore[method-assign]

    kinds = [e["type"] for e in orch.build_stream("clear everything and start again", skip_reset_gate=True)]

    assert built == [1]
    assert "reset-offer" not in kinds


def test_the_reset_offer_beats_the_ask_mode_refusal(tmp_path: Path):
    # "remove everything" is a change request by every rule, so in Ask mode it would otherwise be
    # refused with "switch to Auto" — which sends the user round to the build agent that caused #36.
    orch = _orch(tmp_path)
    orch.project(start_preview=False).control.set_mode(Mode.ASK)
    orch._build_stream = lambda *a, **k: iter([])  # type: ignore[method-assign]

    kinds = [e["type"] for e in orch.build_stream("delete everything and start over")]

    assert kinds[0] == "reset-offer"


def test_the_reset_route_refuses_while_a_turn_is_streaming(tmp_path: Path):
    # A 409, not a 500: the UI says "wait or stop it", which is the same rule a build already follows.
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as app_mod

    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    app_mod.orchestrator = orch
    client = TestClient(app_mod.control_app)
    assert orch._turn_lock.acquire(blocking=False)
    try:
        r = client.post("/api/project/reset")
    finally:
        orch._turn_lock.release()
    assert r.status_code == 409
    assert "stop it" in r.json()["error"]


def test_the_build_route_carries_the_answered_offer_through(tmp_path: Path):
    """The `skipResetGate` field is the whole seam between the offer's buttons and the build, and it
    is the kind that breaks silently: drop it in the route and the button just re-offers, with every
    unit test below still green."""
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as app_mod

    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    orch._build_stream = lambda *a, **k: iter([])  # type: ignore[method-assign]
    app_mod.orchestrator = orch
    client = TestClient(app_mod.control_app)

    prompt = "clear everything and start again"
    gated = client.post("/api/project/build/stream", json={"prompt": prompt})
    answered = client.post("/api/project/build/stream",
                           json={"prompt": prompt, "skipResetGate": True})

    assert "reset-offer" in gated.text          # unanswered, the gate still stops it
    assert "reset-offer" not in answered.text   # answered, it builds
