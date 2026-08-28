"""A deleted Domino App no longer wedges publish (#80, ADR-0008).

Since #70 each Built App records the Domino App its first publish created and re-publishes to that
one. Nothing cleared the record, so an App deleted on its own settings page in Domino — the page
Publish itself links to as "Manage settings in Domino" — left every later publish of that Built App
failing, with no way out from inside Sage.

Before #70 this self-healed, and the self-healing is exactly what made publishing a *second* Built
App deploy over the first. So the recovery here is deliberate: Sage says the App is gone, and the
creator asks for a new one. What is asserted hardest is the line between the two failures — an App
that 404s is gone, an App that timed out is not, and treating the second as the first would create
the duplicate deployment #70 exists to prevent.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from sage.orchestrator.service import Orchestrator
from sage.provision.domino import (
    DominoControlPlane,
    FakeControlPlane,
    NotFound,
    PublishedApp,
)
from sage.resources.provider import FakeResourceProvider
from sage.resources.publish_guard import (
    INDIVIDUAL_CREDENTIAL,
    MISSING_APP,
    PublishRefused,
)
from sage.router.models import ModelCatalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")  # entry script, no serve.py
    return t


def _orch(tmp: Path, cp: FakeControlPlane) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),  # never called: nothing builds here
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider(),
        control_plane=cp,
        domino_project_id="proj-1",
        domino_project_name="Sales dashboard",
    )
    orch.project(start_preview=False)  # attach + seed the first app without starting Vite
    return orch


def _published(tmp: Path, cp: FakeControlPlane) -> tuple[Orchestrator, str]:
    """A builder whose selected Built App has published once, and the Domino App id it recorded."""
    orch = _orch(tmp, cp)
    return orch, orch.publish()["app_id"]


# ---- the App is gone ----------------------------------------------------------------------------


def test_a_publish_whose_domino_app_was_deleted_says_so_and_deploys_nothing(tmp_path: Path):
    cp = FakeControlPlane()
    orch, app_id = _published(tmp_path, cp)
    orch.rename_app(orch.project(start_preview=False).workspace.app_id, "Desk exposure")
    cp.delete_app_deployment(app_id)  # deleted in Domino, behind Sage's back

    with pytest.raises(PublishRefused) as ei:
        orch.publish()

    problem = ei.value.problems[0]
    assert [p.reason for p in ei.value.problems] == [MISSING_APP]
    # Named for the Built App. The app id was minted by Domino and shown to nobody, so a refusal
    # quoting it would name a thing the person reading it cannot go and look at.
    assert "Desk exposure" in problem.message and app_id not in problem.message
    assert not problem.kind and not problem.id  # about the app itself, not about a Binding
    assert cp.published == {}  # refused before anything was deployed


def test_the_refusal_is_the_same_for_an_app_that_reads_a_store(tmp_path: Path):
    """The wedge had two shapes and this is the one that reported the wrong reason.

    With a Binding, the stale id made the visibility read fail and the guard answered
    `unchecked-visibility` — "try again in a moment", forever, for a thing no amount of waiting
    fixes. The App's absence is established before the guard runs, so it is now the answer.
    """
    cp = FakeControlPlane()
    orch, app_id = _published(tmp_path, cp)
    orch.bind_data_source("ds-dwh", "ANALYTICS", "MARTS")
    cp.delete_app_deployment(app_id)

    with pytest.raises(PublishRefused) as ei:
        orch.publish()

    assert [p.reason for p in ei.value.problems] == [MISSING_APP]


def test_a_first_publish_asks_nothing_about_an_app_it_has_not_created_yet(tmp_path: Path):
    # An app with no record has no target to check, and a check that ran anyway would refuse every
    # first publish on a deployment that 404s an empty id.
    class Refuses(FakeControlPlane):
        def app_exists(self, app_id: str) -> bool:
            raise AssertionError("publish asked whether an App it has never created still exists")

    assert _orch(tmp_path, Refuses()).publish()["published"] is True


# ---- only a 404 is evidence ---------------------------------------------------------------------


def test_a_domino_that_cannot_be_reached_does_not_take_away_an_ordinary_republish(tmp_path: Path):
    """The preflight must not cost a publish that worked before it existed.

    A re-publish posts a version to an id it already had; it cannot create a second deployment
    whatever the check says, so an unreachable check has nothing to protect against and refusing on
    it only breaks publishing. Permanently, in one case: `settings.json` is committed, so a teammate
    re-publishing an App they hold no grant on reads 403 here rather than 404, and a refusal on 403
    would wedge them forever — the shape of bug this whole ticket is about.
    """
    cp = FakeControlPlane()
    orch, app_id = _published(tmp_path, cp)

    def raises(_app_id: str) -> bool:
        raise RuntimeError("GET /api/apps/beta/apps/app-1 -> 403: forbidden")

    cp.app_exists = raises  # type: ignore[method-assign]

    out = orch.publish()

    assert out["republished"] is True and out["app_id"] == app_id


def test_a_new_app_is_refused_when_domino_cannot_confirm_the_old_one_is_gone(tmp_path: Path):
    # The same unreachable answer, read the opposite way, because THIS one creates an App. An
    # unconfirmed "gone" is exactly the guess that strands the App still serving.
    cp = FakeControlPlane()
    orch, app_id = _published(tmp_path, cp)

    def raises(_app_id: str) -> bool:
        raise RuntimeError("GET /api/apps/beta/apps/app-1 -> 502: bad gateway")

    cp.app_exists = raises  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="couldn't reach Domino"):
        orch.publish(new_app=True)

    assert list(cp.published) == [app_id]  # no second App
    assert orch.project(start_preview=False).workspace.domino_app_id() == app_id


def test_only_a_404_answers_that_the_app_is_gone():
    """The split lives at the HTTP boundary, so it is asserted there too: `app_exists` reads a 404
    as an answer and everything else as a failure to get one."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/gone"):
            return httpx.Response(404, json={"message": "no such app"})
        if request.url.path.endswith("/sulking"):
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, json={"id": "here", "visibility": "GRANT_BASED"})

    cp = DominoControlPlane(
        "https://domino.example.com", lambda: "tok",
        environment_id="env-1", hardware_tier_id="tier-1",
        transport=httpx.MockTransport(handler),
    )

    assert cp.app_exists("here") is True
    assert cp.app_exists("gone") is False
    with pytest.raises(RuntimeError):
        cp.app_exists("sulking")


def test_a_404_is_a_runtime_error_too_so_old_callers_are_unchanged():
    # `NotFound` is a subclass, not a sibling: every `except RuntimeError` written before #80 —
    # the publish route's included — still catches a 404 exactly as it did.
    assert issubclass(NotFound, RuntimeError)


def test_an_app_deleted_while_the_publish_was_saving_gets_the_same_answer(tmp_path: Path):
    """The window between the check and the deploy is a git push — seconds, and enough. Answering
    that with the raw 502 naming an app id would be the failure this ticket is about, arriving in
    the one case the check cannot cover."""
    cp = FakeControlPlane()
    orch, app_id = _published(tmp_path, cp)
    checked: list[str] = []

    def gone(app: str, **_kw: object) -> PublishedApp:
        cp.delete_app_deployment(app)  # deleted after the preflight passed, before the POST landed
        raise NotFound("POST /api/apps/beta/apps/app-1/versions -> 404: no such app")

    cp.republish_app = gone  # type: ignore[method-assign]
    cp.app_exists = lambda a: checked.append(a) or a in cp.published  # type: ignore[method-assign]

    with pytest.raises(PublishRefused) as ei:
        orch.publish()

    assert checked == [app_id, app_id]  # asked again before accusing anybody of deleting it

    assert [p.reason for p in ei.value.problems] == [MISSING_APP]
    # Still not cleared on Sage's own initiative — the creator answers this the same way.
    assert orch.project(start_preview=False).workspace.domino_app_id() == app_id


def test_a_404_from_the_version_route_is_not_read_as_a_deleted_app(tmp_path: Path):
    """`versions` is a sub-resource, and a deployment that does not route it would 404 every single
    re-publish. Reporting that as "your App was deleted" would invite every creator to publish a
    fresh App beside a live one — one broken route becoming a deployment full of duplicates."""
    cp = FakeControlPlane()
    orch, app_id = _published(tmp_path, cp)

    def unrouted(_app_id: str, **_kw: object) -> PublishedApp:
        raise NotFound("POST /api/apps/beta/apps/app-1/versions -> 404: not found")

    cp.republish_app = unrouted  # type: ignore[method-assign]

    # The App is still there, so the 404 was about something else and is reported as itself.
    with pytest.raises(NotFound):
        orch.publish()
    assert list(cp.published) == [app_id]


# ---- the way out --------------------------------------------------------------------------------


def test_publishing_as_a_new_app_clears_the_record_and_creates_one(tmp_path: Path):
    cp = FakeControlPlane()
    orch, app_id = _published(tmp_path, cp)
    cp.delete_app_deployment(app_id)

    out = orch.publish(new_app=True)

    assert out["republished"] is False
    assert out["app_id"] != app_id  # a new App, and so a new URL
    # Recorded in turn, so the app is back to normal: the publish after this one is a version.
    workspace = orch.project(start_preview=False).workspace
    assert workspace.domino_app_id() == out["app_id"]
    assert orch.publish() == {**out, "republished": True}


def test_clearing_the_record_is_never_something_a_failed_publish_does_by_itself(tmp_path: Path):
    # The whole reason the recovery is a flag. A refused publish leaves the record where it was, so
    # nothing gets published twice by an app that stopped being able to reach its own deployment.
    cp = FakeControlPlane()
    orch, app_id = _published(tmp_path, cp)
    cp.delete_app_deployment(app_id)

    with pytest.raises(PublishRefused):
        orch.publish()

    assert orch.project(start_preview=False).workspace.domino_app_id() == app_id
    assert cp.published == {}  # and nothing was deployed on the way past


def test_a_new_app_beside_a_live_one_is_refused_rather_than_stranding_it(tmp_path: Path):
    """`new_app` answers one question and this is not it.

    `record_domino_app` overwrites the only id Sage holds, so publishing a second App while the
    first is alive would put that first App beyond both Publish and Delete while it went on serving
    old code at a URL people already have. A double-submitted retry, or somebody acting on a
    refusal they read ten minutes ago, is all it would take.
    """
    cp = FakeControlPlane()
    orch, app_id = _published(tmp_path, cp)

    with pytest.raises(RuntimeError, match="still there"):
        orch.publish(new_app=True)

    assert list(cp.published) == [app_id]
    assert orch.project(start_preview=False).workspace.domino_app_id() == app_id


# ---- the other half of the record's life ---------------------------------------------------------


def test_deleting_a_built_app_takes_its_record_with_it(tmp_path: Path):
    # Criterion six, already answered by #76: the record lives in the app's own settings and the
    # settings live in the app's directory, so Delete removes it in the same move.
    cp = FakeControlPlane()
    orch, app_id = _published(tmp_path, cp)
    workspace = orch.project(start_preview=False).workspace

    orch.delete_app(workspace.app_id, delete_domino_app=True)

    assert not workspace.path.exists()
    assert cp.deleted_apps == [app_id]


def test_a_new_app_that_is_then_refused_leaves_the_record_where_it_was(tmp_path: Path):
    """`new_app` on an App that IS still there, refused by the Data Source guard on the way past.

    The record must survive that. Clearing it up front would leave a Built App that has forgotten a
    Domino App still serving on a URL — one Sage can no longer re-publish to or delete, which is
    #76's stranding, self-inflicted. So the record is REPLACED by a publish that succeeds, and
    never cleared by one that refuses.
    """
    cp = FakeControlPlane()
    orch, app_id = _published(tmp_path, cp)
    cp.delete_app_deployment(app_id)
    orch.bind_data_source("ds-test", "ANALYTICS", "PUBLIC")  # the fake's individual-credential row

    with pytest.raises(PublishRefused) as ei:
        orch.publish(new_app=True)

    assert [p.reason for p in ei.value.problems] == [INDIVIDUAL_CREDENTIAL]
    assert orch.project(start_preview=False).workspace.domino_app_id() == app_id
    assert cp.published == {}  # and no second App was created


# ---- over the route -----------------------------------------------------------------------------


def test_the_route_reports_the_refusal_and_takes_the_answer_back(tmp_path: Path, monkeypatch):
    """The recovery has to be reachable from inside Sage, and `POST /api/publish` is the whole of
    Publish's surface today — the vanilla builder page that carried the button is gone and the
    Workbench has not rebuilt it. So the round trip is asserted here: the refusal names the reason
    a caller keys off, and `new_app` is what sends it back."""
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    cp = FakeControlPlane()
    orch, app_id = _published(tmp_path, cp)
    cp.delete_app_deployment(app_id)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    refused = client.post("/api/publish")
    assert refused.status_code == 409
    assert [p["reason"] for p in refused.json()["refused"]] == [MISSING_APP]

    # A body-less publish is still the ordinary one, so a caller that never learned about #80 keeps
    # working — and keeps being refused, which is the point.
    assert client.post("/api/publish").status_code == 409

    out = client.post("/api/publish", json={"new_app": True})
    assert out.status_code == 200
    assert out.json()["app_id"] != app_id


def test_the_route_answers_a_body_it_cannot_read_rather_than_falling_over(tmp_path: Path, monkeypatch):
    # Every other failure on this route is mapped deliberately (409 / 400 / 502). A body that is not
    # JSON, or that is JSON but not an object, must not be the one that reaches the client as a 500.
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    cp = FakeControlPlane()
    orch, _ = _published(tmp_path, cp)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app, raise_server_exceptions=False)

    assert client.post("/api/publish", content=b"new_app=true",
                       headers={"Content-Type": "application/json"}).status_code == 400
    # A JSON body that is not an object carries no `new_app`, so it is the ordinary publish.
    assert client.post("/api/publish", json=["new_app"]).status_code == 200
