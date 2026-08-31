"""A session that says running is not yet a page you can open.

Domino flips a workspace session to running when its execution is up. The Sage process inside binds
its port some seconds later, and for that whole gap the workspace proxy answers `502 Bad Gateway`
(openresty). The door and the chip both send the browser in the moment `running` is true, so the
first page a brand-new viewer ever saw was that 502 — a refresh fixed it, which is exactly the shape
of a race. So `running` now means both halves: the session is up AND its builder answers.
"""
import httpx
import pytest

from sage.provision.domino import DominoControlPlane, FakeControlPlane, ProjectRef, UserRef
from sage.provision.github import FakeRepoProvider
from sage.provision.service import ProvisionService

ALICE = UserRef(id="u-alice", name="alice")
PROJECT = "p-sales"
OPEN_URL = "/alice/sage-sales/notebookSession/run-ws-alice/"


def _cp() -> FakeControlPlane:
    cp = FakeControlPlane(user=ALICE)
    cp.projects.append(ProjectRef(id=PROJECT, name="sage-sales", git_url="https://g/me/sage-sales.git"))
    cp.workspaces[PROJECT] = [{
        "id": "ws-alice",
        "ownerName": "alice",
        "name": "sage",
        "state": "Started",
        "createdAt": "2026-01-01T00:00:00Z",
        "mostRecentSession": {"executionId": "run-ws-alice", "sessionStatusInfo": {"isRunning": True}},
    }]
    return cp


def _service(tmp_path, cp) -> ProvisionService:
    return ProvisionService(cp, FakeRepoProvider(), tmp_path, seed=lambda *a, **k: None)


# --- the service seam: what the door and the chip poll -----------------------------------------

def test_a_booting_builder_is_not_reported_as_open_yet(tmp_path):
    cp = _cp()
    cp.unready_paths.add(OPEN_URL)  # the proxy still has no upstream: 502

    status = _service(tmp_path, cp).workspace_status(PROJECT, owner="alice")

    assert status["running"] is False
    assert status["open_url"] == OPEN_URL  # still known — the caller polls the same URL back
    assert cp.probed_paths == [OPEN_URL]


def test_the_builder_answering_is_what_opens_the_door(tmp_path):
    cp = _cp()

    status = _service(tmp_path, cp).workspace_status(PROJECT, owner="alice")

    assert status["running"] is True


def test_reusing_a_running_workspace_waits_for_it_too(tmp_path):
    """open_app is the short-circuit: both callers skip the poll when it already says running, so a
    reused workspace whose builder is still booting has to be caught here as well."""
    cp = _cp()
    cp.unready_paths.add(OPEN_URL)

    opened = _service(tmp_path, cp).open_app(PROJECT, owner="alice")

    assert opened["workspace"]["id"] == "ws-alice"  # reused, not relaunched
    assert opened["running"] is False


def test_a_probe_that_cannot_tell_leaves_the_session_state_standing(tmp_path):
    """Fail open. A probe that answers None anywhere this runs — no route to the proxy, an auth wall
    in front of it — must leave the door as it was, never wedge it shut for the four minutes the
    caller is willing to wait."""
    class CannotTell(FakeControlPlane):
        def workspace_http_ready(self, open_path):
            return None

    cp = CannotTell(user=ALICE)
    cp.projects.append(ProjectRef(id=PROJECT, name="sage-sales", git_url="https://g/me/sage-sales.git"))
    cp.workspaces[PROJECT] = _cp().workspaces[PROJECT]

    assert _service(tmp_path, cp).workspace_status(PROJECT, owner="alice")["running"] is True


def test_a_stopped_workspace_is_never_probed(tmp_path):
    """Nothing to ask: no session, no server. The probe costs a round trip per poll, so it only runs
    once Domino says there is something on the other end."""
    cp = _cp()
    cp.workspaces[PROJECT][0]["state"] = "Stopped"
    cp.workspaces[PROJECT][0]["mostRecentSession"]["sessionStatusInfo"]["isRunning"] = False

    assert _service(tmp_path, cp).workspace_status(PROJECT, owner="alice")["running"] is False
    assert cp.probed_paths == []


# --- the real client: what each answer off the wire means ---------------------------------------

def _domino(handler) -> DominoControlPlane:
    return DominoControlPlane(
        "https://domino.example.com",
        lambda: "tok",
        environment_id="env-1",
        hardware_tier_id="tier-1",
        transport=httpx.MockTransport(handler),
    )


def test_the_probe_asks_the_builder_through_its_own_proxy_path():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    assert _domino(handler).workspace_http_ready(OPEN_URL) is True
    # The same host-relative path the browser is about to open, on the internal host, with the
    # sidecar token — the way save_workspace_work already reaches a running builder.
    assert seen["url"] == f"https://domino.example.com{OPEN_URL.rstrip('/')}/healthz"
    assert seen["auth"] == "Bearer tok"


@pytest.mark.parametrize("status", [502, 503, 504])
def test_the_gateway_family_means_keep_waiting(status):
    assert _domino(lambda r: httpx.Response(status, text="Bad Gateway")).workspace_http_ready(OPEN_URL) is False


@pytest.mark.parametrize("status", [200, 401, 403, 404])
def test_anything_that_answered_means_the_browser_gets_a_page(status):
    """Not "is this Sage" — "will the gateway serve rather than error". A 401 is Domino's own wall in
    front of a proxy that is up, and the browser carries a session this probe does not."""
    assert _domino(lambda r: httpx.Response(status)).workspace_http_ready(OPEN_URL) is True


def test_an_unreachable_proxy_cannot_tell():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    assert _domino(handler).workspace_http_ready(OPEN_URL) is None
