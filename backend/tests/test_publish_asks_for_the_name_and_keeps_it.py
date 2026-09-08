"""Publish asks what the app is called, and keeps the answer (#218).

Publishing is the moment a name goes public, so it is the one moment somebody reliably thinks about
it. The confirm shows a name field on every publish — pre-filled with the app's own name where
somebody wrote one, and with the Domino project's name where nobody has — and accepting WRITES it,
so the switcher stops saying `Draft app 2` the moment the app is published.

This is also where `_app_display_name`'s `fallback` goes to die. It was a rung nobody could see,
decided by whichever caller happened to be asking, and publish was its only real caller: the Domino
project's name stops being a rung in the ladder and becomes the default value of a field a person
can read and change.

The seam is `_app_written_name` rather than a string match. "Is this app wearing a placeholder" is
the ladder's question, and a caller that answered it by looking for `Draft app` in a rendered name
would be the bottom rung leaking upwards — it would have to change every time the placeholders'
wording did.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.provision.domino import FakeControlPlane
from sage.resources.publish_guard import PublishRefused
from sage.router.models import ModelCatalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")
    return t


def _orch(tmp: Path, cp: FakeControlPlane) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),  # never called: nothing builds here
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        control_plane=cp,
        domino_project_id="proj-1",
        domino_project_name="Sales dashboard",
    )
    orch.project(start_preview=False)
    return orch


def _row(orch: Orchestrator, app_id: str) -> dict:
    return next(r for r in orch.list_apps() if r["id"] == app_id)


# ---- what the field opens holding --------------------------------------------------------------


def test_the_field_offers_the_domino_projects_name_for_an_app_nobody_named(tmp_path: Path):
    """The half the browser could not work out for itself: the Domino project's name has never
    crossed to it at all. A name somebody chose, and the better of the two things Sage can say on
    the deployment side — a rail position means nothing to somebody reading a list of Apps."""
    orch = _orch(tmp_path, FakeControlPlane())
    app = orch.project(start_preview=False).workspace

    row = _row(orch, app.app_id)
    assert row["name"] == "Draft app 1"
    assert row["publishName"] == "Sales dashboard"


def test_the_field_offers_the_apps_own_name_once_somebody_wrote_one(tmp_path: Path):
    """Both written rungs, because both are names somebody wrote: the rename box, and the planner
    through the `# ` heading the plan shape asks for."""
    orch = _orch(tmp_path, FakeControlPlane())
    app = orch.project(start_preview=False).workspace

    app.write_plan("# Desk exposure\n\nSteps...")
    assert _row(orch, app.app_id)["publishName"] == "Desk exposure"

    orch.rename_app(app.app_id, "Risk monitor")
    assert _row(orch, app.app_id)["publishName"] == "Risk monitor"


def test_the_field_is_offered_on_every_row_including_one_already_published(tmp_path: Path):
    """Always shown rather than only where the app has no name (#218). A field that appeared for the
    first time on some later publish would arrive on the publish nobody was ready for it on."""
    orch = _orch(tmp_path, FakeControlPlane())
    app = orch.project(start_preview=False).workspace
    orch.publish(name="Desk exposure")

    row = _row(orch, app.app_id)
    assert row["published"] is True
    assert row["publishName"] == "Desk exposure"


# ---- accepting it writes it --------------------------------------------------------------------


def test_accepting_the_name_writes_it_to_the_app(tmp_path: Path):
    """The ask buys the write. The switcher stops saying `Draft app 1` the moment the app is
    published, which is the whole reason publish is allowed to ask at all."""
    orch = _orch(tmp_path, FakeControlPlane())
    app = orch.project(start_preview=False).workspace

    orch.publish(name="Desk exposure")

    assert app.display_name() == "Desk exposure"
    assert _row(orch, app.app_id)["name"] == "Desk exposure"


def test_the_deployed_app_carries_the_accepted_name(tmp_path: Path):
    """The same string, both sides. A name written here and an App deployed under another would be
    two answers to the question the field just asked."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)

    out = orch.publish(name="Desk exposure")

    assert cp.app_names[out["app_id"]] == "Desk exposure"


def test_a_republish_keeps_the_name_even_though_the_deployment_is_not_renamed(tmp_path: Path):
    """A re-publish posts a VERSION to an App that already exists, so nothing here renames it in
    Domino — and the field is still worth accepting, because the name it writes is the one the
    switcher, the receipts and the next first publish all read."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    first = orch.publish(name="Desk exposure")

    again = orch.publish(name="Risk monitor")

    assert again["republished"] is True
    assert app.display_name() == "Risk monitor"
    assert cp.app_names[first["app_id"]] == "Desk exposure"


def test_a_publish_that_is_refused_writes_no_name(tmp_path: Path):
    """A refused publish leaves nothing behind, which is why the write sits below the refusals. The
    creator answers the refusal and publishes again — under the name they typed the first time,
    because the field still holds what the row still says."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    orch.publish(name="Desk exposure")
    cp.published.clear()  # the App was deleted on its own settings page in Domino (#80)

    with pytest.raises(PublishRefused):
        orch.publish(name="Risk monitor")

    assert app.display_name() == "Desk exposure"


# ---- a field somebody cleared ------------------------------------------------------------------


def test_a_cleared_field_renames_nothing(tmp_path: Path):
    """Accepting writes; clearing is not accepting. Nothing here reaches back and takes a name off
    an app, and an app that had none still has none."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    orch.rename_app(app.app_id, "Desk exposure")

    orch.publish(name="   ")

    assert app.display_name() == "Desk exposure"


def test_a_cleared_field_still_deploys_under_what_it_was_offering(tmp_path: Path):
    """The one thing a cleared field must not do is send a placeholder to Domino. `Draft app 1` is
    not a name, and a deployment is the last place for a string that says the app has none."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)

    out = orch.publish(name="")

    assert cp.app_names[out["app_id"]] == "Sales dashboard"


def test_a_publish_that_names_nothing_is_the_publish_it_always_was(tmp_path: Path):
    """Every caller older than the field — and the route, for a body that carries no name at all."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace

    out = orch.publish()

    assert cp.app_names[out["app_id"]] == "Sales dashboard"
    assert app.display_name() == ""


# ---- over the route ----------------------------------------------------------------------------


def test_the_route_carries_the_name_through(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    monkeypatch.setattr(appmod, "orchestrator", orch)

    r = TestClient(appmod.control_app).post("/api/publish", json={"name": "Desk exposure"})

    assert r.status_code == 200
    assert app.display_name() == "Desk exposure"
    assert cp.app_names[r.json()["app_id"]] == "Desk exposure"


def test_the_route_takes_a_name_that_is_not_a_string_as_no_name(tmp_path: Path, monkeypatch):
    """The same reading `new_app` gets one line above it: a malformed body means the ordinary
    publish, not a 500 — and never a `displayName` that is not a name."""
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    monkeypatch.setattr(appmod, "orchestrator", orch)

    r = TestClient(appmod.control_app).post("/api/publish", json={"name": {"nope": 1}})

    assert r.status_code == 200
    assert app.display_name() == ""
    assert cp.app_names[r.json()["app_id"]] == "Sales dashboard"


def test_the_name_reaches_the_rail_tags_and_not_only_the_app(tmp_path: Path):
    """A rename is not one write. The chip on every conversation that changed this app names it from
    OUTSIDE the app's directory, and a publish that wrote `displayName` on its own would leave those
    chips saying `Draft app 1` while the header, the rail and the preview had all moved."""
    from sage.workspace.threads import ThreadStore

    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    threads = ThreadStore(orch.project(start_preview=False).record.path)
    thread = threads.create("Desks")
    threads.record_touch(thread["id"], app_id=app.app_id, app_name="Draft app 1", kind="built")

    orch.publish(name="Desk exposure")

    assert threads.get(thread["id"])["touched"] == [
        {"appId": app.app_id, "appName": "Desk exposure", "kind": "built"},
    ]


def test_a_deploy_that_failed_writes_no_name(tmp_path: Path):
    """The same rule as the refusals above, read one step later: a publish that did not happen
    leaves nothing behind. It is also what stops a name Domino itself refused from becoming the
    app's name — and then being offered straight back as the next publish's default."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace

    def _refuse(*_a, **_kw):
        raise RuntimeError("Domino would not take that name")

    orch._control_plane.publish_app = _refuse  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        orch.publish(name="Desk exposure")

    assert app.display_name() == ""
    assert _row(orch, app.app_id)["publishName"] == "Sales dashboard"
