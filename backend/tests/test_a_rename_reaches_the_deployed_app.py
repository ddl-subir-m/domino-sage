"""A rename reaches the deployed App, or Sage says it did not (#219).

`publish_app` names a Domino App at creation and that was the only time the name was ever set: a
re-publish posts a *version*, which carries no name, and `rename_app` wrote `displayName` and the
rail's tags and stopped. So an app published as `Sales dashboard` and renamed to `Risk monitor`
said `Risk monitor` on every Sage surface and `Sales dashboard` on every Domino one, for the life
of the App, with no way back that did not change the URL.

The live probe (docs/live-runs/2026-09-08-app-rename-probe.md) settled the half that could not be
guessed: `PATCH /api/apps/beta/apps/{id}` with `{"name": …}` renames a deployed App in place. It
merges, so nothing else has to be sent; the URL does not move; no republish is needed; and all
three Domino surfaces read the record it writes.

What the probe could not de-risk is the half that breaks quietly. The local rename is written
first and it is not rolled back, so when the Domino call fails the two sides diverge again — and a
rename that silently half-fails is worse than the divergence this exists to fix. Every test below
that names a failure is testing the sentence, not the write.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.provision.domino import FakeControlPlane, NotFound
from sage.router.models import ModelCatalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")
    return t


def _orch(tmp: Path, cp: FakeControlPlane | None) -> Orchestrator:
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


# ---- the half that breaks quietly (criterion 4) ------------------------------------------------


def test_a_rename_domino_refuses_keeps_the_local_one_and_says_so(tmp_path: Path):
    """The local rename is not rolled back, and the answer says only half of it landed.

    Rolling back would be the worse trade: the person is looking at a name they typed into a box
    that closed, and taking it away again to match a deployment they cannot see would read as the
    rename simply not working. So the app keeps the new name, and the ONE thing that is not true —
    the App in Domino is still called the old name — is what the answer carries.
    """
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    published = orch.publish(name="Sales dashboard")
    cp.rename_failures.add(published["app_id"])

    out = orch.rename_app(app.app_id, "Risk monitor")

    # The local rename survived, everywhere a rename lands.
    assert out["name"] == "Risk monitor"
    assert app.display_name() == "Risk monitor"
    assert next(r for r in orch.list_apps() if r["id"] == app.app_id)["name"] == "Risk monitor"
    # And the deployment did not, which is what the caller is told.
    assert out["dominoApp"] == "failed"
    assert cp.app_names[published["app_id"]] == "Sales dashboard"
    # Domino's own words ride in, so the sentence on screen can say why rather than that.
    assert "no" in out["dominoAppError"].lower()


def test_a_rename_with_no_way_to_reach_domino_is_a_failure_and_not_a_silence(tmp_path: Path):
    """A builder with no control plane is the same half-rename wearing a different cause: the App
    is out there under its old name and nothing here moved it. Answering `none` would say there was
    no App to rename, which is a different sentence and a false one."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    orch.publish(name="Sales dashboard")
    orch._control_plane = None

    out = orch.rename_app(app.app_id, "Risk monitor")

    assert app.display_name() == "Risk monitor"
    assert out["dominoApp"] == "failed"
    assert out["dominoAppError"]


def test_a_rename_of_an_app_whose_domino_app_was_deleted_says_it_is_gone(tmp_path: Path):
    """The App can be deleted on its own settings page in Domino, which leaves Sage holding an id
    that names nothing (#80). The Built App is renamed either way — but the ordinary sentence would
    be false in both halves here, because nothing is serving under the old name and there is no App
    on that page to go and rename. So the 404 is told apart from every other failure."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    orch.publish(name="Sales dashboard")
    cp.published.clear()

    out = orch.rename_app(app.app_id, "Risk monitor")

    assert app.display_name() == "Risk monitor"
    assert out["dominoApp"] == "failed"
    assert "no longer there" in out["dominoAppError"]
    assert "still serving" not in out["dominoAppError"]


def test_a_404_from_a_route_that_is_not_there_is_not_read_as_a_deleted_app(tmp_path: Path):
    """The trap `republish_app`'s own 404 handler sets out: PATCH is the only verb this route
    answers, so a deployment that does not route it 404s EVERY rename. Reading that as "your App
    was deleted" would tell a whole deployment of creators to re-publish Apps that are alive and
    serving — one missing route turning into a wave of duplicates. So the App is asked about before
    the word `deleted` is used, and an App that answers gets the ordinary sentence."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    published = orch.publish(name="Sales dashboard")

    def unrouted(app_id: str, name: str) -> dict:
        raise NotFound(f"PATCH /api/apps/beta/apps/{app_id} -> 404: Public api endpoint not found")

    cp.rename_app_deployment = unrouted  # type: ignore[method-assign]

    out = orch.rename_app(app.app_id, "Risk monitor")

    assert app.display_name() == "Risk monitor"
    assert out["dominoApp"] == "failed"
    # The App is still there, so it is still serving — and nobody is told to publish a second one.
    assert cp.app_names[published["app_id"]] == "Sales dashboard"
    assert "still serving" in out["dominoAppError"]
    assert "deleted" not in out["dominoAppError"]


# ---- both writers carry the name to the deployment (criterion 2) --------------------------------


def test_renaming_a_published_app_renames_the_deployment(tmp_path: Path):
    """The `…` menu's Rename, which is the door the divergence came in through."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    published = orch.publish(name="Sales dashboard")

    out = orch.rename_app(app.app_id, "Risk monitor")

    assert out["dominoApp"] == "renamed"
    assert cp.app_names[published["app_id"]] == "Risk monitor"
    assert cp.renamed_apps == [(published["app_id"], "Risk monitor")]


def test_a_republish_under_a_new_name_renames_the_deployment(tmp_path: Path):
    """Publish's name field, the other writer. A re-publish posts a version and a version carries
    no name, so without this the confirm's `Published new version "Risk monitor"` was a promise
    about a deployment that still said `Sales dashboard`."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    first = orch.publish(name="Sales dashboard")

    again = orch.publish(name="Risk monitor")

    assert again["republished"] is True
    assert app.display_name() == "Risk monitor"
    assert cp.app_names[first["app_id"]] == "Risk monitor"


def test_a_republish_that_cannot_rename_the_deployment_says_so(tmp_path: Path):
    """The publish confirm is the sentence that promised the deployment moved, so it is the one
    that has to take the promise back. The version still shipped — the code behind the URL is new
    even where the name on it is old — so this is a warning on a publish that worked, not a
    failure."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    first = orch.publish(name="Sales dashboard")
    cp.rename_failures.add(first["app_id"])

    out = orch.publish(name="Risk monitor")

    assert out["published"] is True
    assert out["republished"] is True
    assert out["dominoApp"] == "failed"
    assert cp.app_names[first["app_id"]] == "Sales dashboard"


def test_a_republish_carries_the_name_even_when_the_local_one_already_matches(tmp_path: Path):
    """The retry after a half-rename, and the reason publish does not skip on `name == name`.

    Rename to `Risk monitor`, Domino refuses: the app is called that here and the App is still
    called `Sales dashboard` over there. The obvious next move is to publish — the confirm opens
    pre-filled with `Risk monitor`, and a publish that compared the two LOCAL names would find them
    equal, skip, and ship code to an App whose name is still wrong while saying `Published a new
    version of "Risk monitor"`. The divergence would survive the one act that promises otherwise.
    """
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    first = orch.publish(name="Sales dashboard")
    cp.rename_failures.add(first["app_id"])
    orch.rename_app(app.app_id, "Risk monitor")
    cp.rename_failures.clear()

    out = orch.publish(name="Risk monitor")

    assert app.display_name() == "Risk monitor"
    assert out["dominoApp"] == "renamed"
    assert cp.app_names[first["app_id"]] == "Risk monitor"


def test_a_first_publish_names_the_app_at_creation_and_does_not_rename_it_after(tmp_path: Path):
    """`publish_app` already carries the accepted name (#218), so the App is created holding it and
    there is nothing left to rename. Asserted rather than assumed, because a redundant PATCH here
    would not just be a wasted call: a failure on it would report a half-rename of an App whose
    name was right all along."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)

    out = orch.publish(name="Desk exposure")

    assert cp.app_names[out["app_id"]] == "Desk exposure"
    assert cp.renamed_apps == []
    # `named`, not `none`: an App was just deployed, and `none` is the word for one that never was.
    assert out["dominoApp"] == "named"


def test_a_republish_with_the_name_field_cleared_claims_nothing_about_the_deployment(tmp_path: Path):
    """Clearing the field is not accepting a name, so nothing is renamed — and the answer says
    nothing rather than `none`, which is reserved for an app that has no published App at all. This
    app plainly has one, and a consumer reading `none` as "unpublished" would read it wrong."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    first = orch.publish(name="Desk exposure")

    out = orch.publish(name="")

    assert out["republished"] is True
    assert "dominoApp" not in out
    assert cp.app_names[first["app_id"]] == "Desk exposure"


# ---- an app that was never published ------------------------------------------------------------


def test_renaming_an_unpublished_app_makes_no_control_plane_call(tmp_path: Path):
    """There is no App to rename, and `none` says that rather than pretending at either answer."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace

    out = orch.rename_app(app.app_id, "Risk monitor")

    assert out["dominoApp"] == "none"
    assert cp.renamed_apps == []


def test_a_rename_before_the_first_publish_reaches_domino_when_it_publishes(tmp_path: Path):
    """The claim #219 makes about the unpublished case, checked rather than assumed: a name written
    before there is an App needs no new code, because the App is created holding it."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace

    orch.rename_app(app.app_id, "Risk monitor")
    out = orch.publish()

    assert cp.app_names[out["app_id"]] == "Risk monitor"
    assert cp.renamed_apps == []


# ---- over the route -----------------------------------------------------------------------------


def test_the_route_carries_the_half_rename_back_to_the_browser(tmp_path: Path, monkeypatch):
    """A 200, because the rename it was asked for happened. The sentence rides in the body: the
    modal has already closed on a name the person can see, so the one thing left to say is about
    the App they cannot."""
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace
    cp.rename_failures.add(orch.publish(name="Sales dashboard")["app_id"])
    monkeypatch.setattr(appmod, "orchestrator", orch)

    r = TestClient(appmod.control_app).patch(
        f"/api/apps/{app.app_id}", json={"name": "Risk monitor"})

    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Risk monitor"
    assert body["dominoApp"] == "failed"
    assert body["dominoAppError"]


# ---- the control plane's own half ---------------------------------------------------------------


def test_the_control_plane_patches_the_beta_apps_route(tmp_path: Path):
    """PATCH is the one verb routed for this (the probe found PUT, POST and OPTIONS 404 at the
    router), and `{"name": …}` alone is the whole body — the API merges, so sending the App back
    would only be a chance to lose a field."""
    import httpx

    from sage.provision.domino import DominoControlPlane

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"id": "app-1", "name": "Risk monitor"})

    cp = DominoControlPlane(
        "https://nucleus", lambda: "tok", environment_id="env-1", hardware_tier_id="tier-1",
        transport=httpx.MockTransport(handler),
    )

    cp.rename_app_deployment("app-1", "Risk monitor")

    assert seen["method"] == "PATCH"
    assert seen["url"] == "https://nucleus/api/apps/beta/apps/app-1"
    assert seen["body"] == '{"name":"Risk monitor"}'


def test_the_control_plane_raises_notfound_for_an_app_that_is_gone(tmp_path: Path):
    """So the caller can tell "Domino refused" from "there is no App there any more" — the same
    distinction `app_exists` exists to draw (#80)."""
    import httpx

    from sage.provision.domino import DominoControlPlane

    cp = DominoControlPlane(
        "https://nucleus", lambda: "tok", environment_id="env-1", hardware_tier_id="tier-1",
        transport=httpx.MockTransport(lambda r: httpx.Response(404, text="No app found with id")),
    )

    with pytest.raises(NotFound):
        cp.rename_app_deployment("app-1", "Risk monitor")
