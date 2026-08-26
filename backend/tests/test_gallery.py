"""Gallery lists Built Apps, not Projects (#48).

Which Apps a viewer is shown is policy, so it is driven at the service seam against the Fake
Control Plane. Reading the global apps list is transport, and that is driven against a mock
httpx transport alongside the rest of test_provision_domino's schema cover.
"""
from pathlib import Path

import httpx

from sage.provision.domino import BuiltApp, DominoControlPlane, FakeControlPlane, ProjectRef
from sage.provision.github import FakeRepoProvider
from sage.provision.service import ProvisionService


def _service(tmp_path, cp) -> ProvisionService:
    return ProvisionService(cp, FakeRepoProvider(), tmp_path, seed=lambda *a, **k: None)


def _app(app_id: str, project_id: str, *, name: str = "Sales dashboard", status: str = "Running") -> BuiltApp:
    return BuiltApp(id=app_id, name=name, url=f"https://d/apps/{app_id}/",
                    project_id=project_id, project_name=f"name-of-{project_id}", status=status)


def _cp() -> FakeControlPlane:
    cp = FakeControlPlane()
    cp.projects.append(ProjectRef(id="p-sage", name="sage-sales", git_url="https://g/me/sage-sales.git"))
    return cp


def test_gallery_returns_published_apps(tmp_path):
    cp = _cp()
    cp.built = [_app("app-1", "p-sage")]

    out = _service(tmp_path, cp).list_built_apps()

    assert [a.id for a in out] == ["app-1"]
    assert out[0].url == "https://d/apps/app-1/"  # the App's own viewer URL, not a Project


def test_a_sage_project_with_nothing_published_is_not_in_the_gallery(tmp_path):
    # The chip lists Projects; the Gallery lists what came out of them. Being sage-* is not enough.
    cp = _cp()
    cp.projects.append(ProjectRef(id="p-empty", name="sage-empty", git_url="https://g/me/sage-empty.git"))
    cp.built = [_app("app-1", "p-sage")]

    out = _service(tmp_path, cp).list_built_apps()

    assert [a.project_id for a in out] == ["p-sage"]


def test_the_workbench_app_is_not_listed(tmp_path):
    # Sage's own project is not a sage-* one, so the App that published this Workbench never
    # reaches the list of Projects the Gallery is narrowed to.
    cp = _cp()
    cp.built = [_app("app-sage", "p-sage-itself", name="Sage"), _app("app-1", "p-sage")]

    out = _service(tmp_path, cp).list_built_apps()

    assert [a.id for a in out] == ["app-1"]


def test_an_app_in_a_project_this_viewer_cannot_list_is_left_out(tmp_path):
    # The apps list is global — every App on the deployment. A card the viewer cannot open is
    # worse than a card they never saw.
    cp = _cp()
    cp.built = [_app("app-theirs", "p-someone-else"), _app("app-mine", "p-sage")]

    out = _service(tmp_path, cp).list_built_apps()

    assert [a.id for a in out] == ["app-mine"]


def test_an_empty_gallery_is_empty_and_does_not_fall_back_to_projects(tmp_path):
    cp = _cp()  # a sage-* Project exists, nothing is published from it

    assert _service(tmp_path, cp).list_built_apps() == []


# --- reading the global list (transport) -----------------------------------------------------


def _domino(handler) -> DominoControlPlane:
    return DominoControlPlane(
        "https://api.test", lambda: "tok", environment_id="env-1", hardware_tier_id="tier-1",
        transport=httpx.MockTransport(handler),
    )


def test_list_all_apps_reads_the_live_item_schema():
    payload = {"items": [{
        "id": "app-1",
        "name": "Sales dashboard",
        "url": "https://apps.d/apps-internal/app-1/",
        "project": {"id": "proj-42", "name": "sage-sales"},
        "currentVersion": {"currentInstance": {"status": "Running"}},
    }], "metadata": {"totalCount": 1}}

    out = _domino(lambda r: httpx.Response(200, json=payload)).list_all_apps()

    assert len(out) == 1
    app = out[0]
    assert (app.id, app.name, app.project_id, app.project_name, app.status) == (
        "app-1", "Sales dashboard", "proj-42", "sage-sales", "Running")
    assert app.url == "/modelproducts/app-1?scope=project"  # /apps-internal 404s in a browser


def test_list_all_apps_pages_past_the_first_hundred():
    # The list is global: one deployment answered with 284 rows, and stopping at one page would
    # look like a complete Gallery while hiding most of it.
    seen = []

    def handler(request):
        offset = int(dict(p.split("=") for p in request.url.query.decode().split("&"))["offset"])
        seen.append(offset)
        items = [{"id": f"app-{offset + i}", "project": {"id": "p"}} for i in range(min(100, 250 - offset))]
        return httpx.Response(200, json={"items": items, "metadata": {"totalCount": 250}})

    out = _domino(handler).list_all_apps()

    assert seen == [0, 100, 200]
    assert len(out) == 250
    assert out[0].name == "Untitled app"  # a nameless app is still openable, so it still lists


def test_list_all_apps_stops_when_a_page_comes_back_empty():
    # A totalCount that outruns the rows must not spin.
    def handler(request):
        return httpx.Response(200, json={"items": [], "metadata": {"totalCount": 900}})

    assert _domino(handler).list_all_apps() == []


# --- what the Workbench does with it, pinned in source ---------------------------------------

WB = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"


def test_the_gallery_shows_apps_and_never_falls_back_to_the_project_list():
    gallery = (WB / "modes" / "gallery.js").read_text()
    api = (WB / "api.js").read_text()

    assert "request('/gallery')" in api
    assert "SW.api.gallery()" in gallery
    assert "SW.api.projects()" not in gallery      # an empty Gallery stays empty
    assert "attachProject" not in gallery          # and opening an app never switches Project
    assert "window.open(app.url, '_blank'" in gallery
    assert "No Built Apps yet" in gallery          # empty says what, why, and what to do next
    assert "then publish it" in gallery


def test_first_open_still_lands_in_chat():
    router = (WB / "router.js").read_text()
    assert "window.location.hash || '#/chat'" in router
    assert "segments[0] || 'chat'" in router


def test_the_chip_no_longer_promises_projects_in_the_gallery():
    picker = (WB / "components" / "scope-picker.js").read_text()
    assert "Browse all projects in the gallery" not in picker


def test_a_container_that_cannot_provision_has_an_empty_gallery():
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    client = TestClient(appmod.control_app)
    assert client.get("/api/gallery").json() == {"items": [], "provisioning": False}
