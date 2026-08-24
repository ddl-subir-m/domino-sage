"""Starting over is its own action (#36).

Before this, "rebuild this app from scratch, remove everything you have built" was only a sentence
handed to the build agent — and a build agent builds. Live on 2026-08-24 it wrote the user a landing
page saying "Ready to rebuild from scratch", which is the most literal thing those words describe.

Two halves: a reset that keeps what the user set up, and a phrase rule that OFFERS it rather than
running it. The second half is deliberate — a reset throws the app away, and putting a destructive
action behind a heuristic is the shape of #29.
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
    # And the next build is planned like a first build, because the app really is new again.
    assert project.workspace.has_built() is False


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
    assert "Reset app" in events[0]["message"]             # names the control it is offering
    assert (project.workspace.path / "src" / "App.tsx").read_text() == "export default () => <b>built</b>;"


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
