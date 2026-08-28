"""Deleting a Built App (#76, ADR-0008).

A Project holds many Built Apps, so it accumulates the ones an idea did not survive. Delete is how
that rail gets cleared, and it is deliberately not Reset: Reset empties an app and keeps it, Delete
takes the app away. Nobody should lose an app while trying to start it over, so the two are asserted
against each other here rather than only apart.

The other half is the Domino App. A Built App's Domino App id lives in the app's own settings, so
once the directory goes Sage cannot update or delete that App either — a live URL nobody can find or
fix is the same stranding this whole ticket exists to prevent. Delete offers it, and the answer the
person gave is the one that happens.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.provision.domino import FakeControlPlane
from sage.router.models import ModelCatalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")
    return t


def _orch(tmp: Path, cp: FakeControlPlane | None = None) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),  # never called: nothing builds here
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        control_plane=cp,
        domino_project_id="proj-1" if cp else "",
        domino_project_name="Sales dashboard" if cp else "",
    )
    orch.project(start_preview=False)  # attach and seed the first app without starting Vite
    return orch


def _second_app(orch: Orchestrator) -> str:
    """Mint a second Built App and put Build in front of it, as New app in the rail does."""
    return orch.create_app()["id"]


def test_deleting_a_built_app_takes_it_out_of_the_rail(tmp_path: Path):
    orch = _orch(tmp_path)
    first = orch.project(start_preview=False).workspace.app_id
    second = _second_app(orch)

    orch.delete_app(second)

    assert [row["id"] for row in orch.list_apps()] == [first]
    assert not (orch._wm.apps_dir / second).exists()


def test_deleting_an_app_that_is_not_there_is_refused(tmp_path: Path):
    """A 404 rather than a quiet success: an id that names nothing was somebody's stale rail."""
    orch = _orch(tmp_path)
    with pytest.raises(KeyError):
        orch.delete_app("app-nobody")


def test_deleting_one_app_leaves_the_other_one_exactly_as_it_was(tmp_path: Path):
    """Code, plan document, Bindings and transcript — the four things a Built App owns.

    The same four Reset was narrowed to leave alone (#75). Delete reaches further than Reset does
    inside one app, so it has more to prove about the app it is not touching.
    """
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    kept_app = project.workspace
    kept_plan = project.record.create_plan_doc("A daily P&L report.\n", title="A daily P&L report.",
                                               app_id=kept_app.app_id)["id"]
    kept_app.update_bindings(lambda rows: rows + [{"kind": "llm", "id": "gpt", "name": "gpt"}])
    kept_app.append_history({"type": "user", "text": "build the P&L report"})
    (kept_app.path / "src" / "Report.tsx").write_text("export const R = 1;")
    kept_app.mark_built()

    doomed = _second_app(orch)
    doomed_plan = project.record.create_plan_doc("A desk exposure dashboard.\n",
                                                 title="A desk exposure dashboard.",
                                                 app_id=doomed)["id"]

    orch.delete_app(doomed)

    assert (kept_app.path / "src" / "Report.tsx").read_text() == "export const R = 1;"
    assert [b["id"] for b in kept_app.read_bindings()] == ["gpt"]
    assert [e["text"] for e in kept_app.read_history() if e["type"] == "user"] \
        == ["build the P&L report"]
    assert kept_app.has_built() is True
    # The deleted app's plan document goes with it and the other app's stays. A document describes
    # code, and this app's code is gone for good rather than back to the template.
    assert [d["id"] for d in orch.list_plan_docs()] == [kept_plan]
    assert doomed_plan not in [d["id"] for d in orch.list_plan_docs()]


def test_a_plan_drafted_in_chat_is_not_the_deleted_apps_to_take(tmp_path: Path):
    """A document naming no app has not been handed off to one, so it is nobody's to delete."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    draft = project.record.create_plan_doc("A dashboard.\n", title="A dashboard.")["id"]

    orch.delete_app(_second_app(orch))

    assert [d["id"] for d in orch.list_plan_docs()] == [draft]


def test_deleting_the_app_in_front_of_you_moves_build_to_the_one_that_is_left(tmp_path: Path):
    """Build has to be looking at something. The app it lands on is a real one on disk, not the
    directory that was just removed."""
    orch = _orch(tmp_path)
    first = orch.project(start_preview=False).workspace.app_id
    second = _second_app(orch)
    assert orch.project(start_preview=False).workspace.app_id == second

    out = orch.delete_app(second)

    assert out["selected"] == first
    assert orch.project(start_preview=False).workspace.app_id == first
    assert [row["id"] for row in orch.list_apps() if row["selected"]] == [first]


def test_deleting_the_last_app_leaves_the_project_with_a_new_one(tmp_path: Path):
    """A Project with no app is the state a Project starts in, and it seeds one. Deleting the last
    app reaches the same place rather than a Build mode pointed at nothing."""
    orch = _orch(tmp_path)
    only = orch.project(start_preview=False).workspace.app_id

    orch.delete_app(only)

    rows = orch.list_apps()
    assert [row["id"] for row in rows] != [only]
    assert len(rows) == 1 and rows[0]["built"] is False
    assert (orch.project(start_preview=False).workspace.path / "package.json").exists()


def test_reset_keeps_the_app_and_delete_takes_it_away(tmp_path: Path):
    """The two are one keystroke apart in intent and a whole app apart in outcome, so the difference
    is asserted rather than left to the copy: after a Reset the app is still in the rail with its
    code replaced; after a Delete it is not in the rail at all."""
    orch = _orch(tmp_path)
    app_id = _second_app(orch)
    workspace = orch.project(start_preview=False).workspace
    (workspace.path / "src" / "Desk.tsx").write_text("export const D = 1;")

    orch.reset_app()

    assert app_id in [row["id"] for row in orch.list_apps()]
    assert not (workspace.path / "src" / "Desk.tsx").exists()
    assert (workspace.path / "package.json").exists()

    orch.delete_app(app_id)

    assert app_id not in [row["id"] for row in orch.list_apps()]
    assert not workspace.path.exists()


# ---------------------------------------------------------------------------
# The Domino App
# ---------------------------------------------------------------------------


def _publish(orch: Orchestrator) -> str:
    """Publish whichever app Build is in front of, and answer with its Domino App id."""
    return orch.publish()["app_id"]


def test_deleting_a_published_app_deletes_its_domino_app_when_the_offer_is_accepted(tmp_path: Path):
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app_id = orch.project(start_preview=False).workspace.app_id
    deployed = _publish(orch)
    assert deployed in cp.published

    out = orch.delete_app(app_id, delete_domino_app=True)

    assert cp.deleted_apps == [deployed]
    assert deployed not in cp.published
    assert out["dominoApp"] == "deleted"


def test_declining_the_offer_leaves_the_domino_app_running_and_says_so(tmp_path: Path):
    """The Built App goes and the Domino App keeps serving. Saying so is the point: Sage can no
    longer update or delete that App, because the id that reaches it went with the app."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app_id = orch.project(start_preview=False).workspace.app_id
    deployed = _publish(orch)

    out = orch.delete_app(app_id, delete_domino_app=False)

    assert cp.deleted_apps == []
    assert deployed in cp.published
    assert out["dominoApp"] == "running"
    assert app_id not in [row["id"] for row in orch.list_apps()]


def test_deleting_an_unpublished_app_makes_no_control_plane_call(tmp_path: Path):
    """Even when the person accepted an offer that was never made to them: there is no Domino App,
    so "deleted its Domino App too" is a sentence about nothing."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)

    out = orch.delete_app(_second_app(orch), delete_domino_app=True)

    assert cp.deleted_apps == []
    assert out["dominoApp"] == "none"


def test_a_control_plane_that_refuses_leaves_the_built_app_where_it_was(tmp_path: Path):
    """The order that leaves a way out. Deleting the directory first and then failing would lose the
    app AND strand the Domino App, with nothing left on either side to try again with."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app_id = orch.project(start_preview=False).workspace.app_id
    deployed = _publish(orch)

    def refuse(_app_id: str) -> dict:
        raise RuntimeError("DELETE /api/apps/beta/apps/app-1 -> 403: not yours")

    cp.delete_app_deployment = refuse  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as caught:
        orch.delete_app(app_id, delete_domino_app=True)

    assert "still here" in str(caught.value)
    assert app_id in [row["id"] for row in orch.list_apps()]
    assert deployed in cp.published


def test_the_rail_says_which_apps_are_published(tmp_path: Path):
    """What makes Delete offer the Domino App on one row and not another."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    published_app = orch.project(start_preview=False).workspace.app_id
    _publish(orch)
    plain = _second_app(orch)

    rows = {row["id"]: row["published"] for row in orch.list_apps()}

    assert rows == {published_app: True, plain: False}


def test_delete_is_refused_while_a_build_is_running(tmp_path: Path):
    """A turn holds one working tree and this removes one. Refused as `busy`, the same refusal a
    switch and a New app get, so the route can tell it from a real failure."""
    orch = _orch(tmp_path)
    app_id = _second_app(orch)

    assert orch._turn_lock.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError, match="busy"):
            orch.delete_app(app_id)
    finally:
        orch._turn_lock.release()

    assert app_id in [row["id"] for row in orch.list_apps()]
