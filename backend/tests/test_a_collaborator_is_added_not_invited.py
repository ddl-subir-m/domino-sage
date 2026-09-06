"""Adding a colleague to the Project, and taking them off it again.

Three layers, one claim each. The provider says what Domino said — including that it said nothing
(ADR-0028). The service joins the two reads Domino disagrees on and tells the three states apart.
The routes carry no project id, because the server already knows which Project it is.

Nothing here reaches the network: the Domino adapter runs against a stub HTTP server on a throwaway
port, and the orchestrator runs on the fake provider.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from sage.resources.provider import (
    Collaborator,
    DominoResourceProvider,
    FakeResourceProvider,
    Person,
    ResourceUnavailable,
)

PROJECT = "66a821b2ecadae7f043a5171"


# ---- a Domino that answers the collaborator calls ------------------------------------------------


@contextmanager
def stub_collaborator_api(
    *,
    people: object = None,
    projects: object = None,
    directory: object = None,
    self_user: object = None,
    add_status: int = 200,
    remove_status: int = 200,
):
    """A Domino answering the four reads and two writes this feature makes, recording the writes.

    Answers by path, not by call order, for the reason `stub_domino_api` gives: order made the stub
    a script of the provider's internals. `None` for any of them is a 404 — a Domino that will not
    say, which is the case ADR-0028 exists for.
    """
    writes: list[tuple[str, str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: object) -> None:
            if payload is None:
                self.send_response(status if status >= 400 else 404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path
            if path.startswith(f"/v4/projects/{PROJECT}/collaborators"):
                return self._send(200, people)
            if path == f"/v4/projects/{PROJECT}":
                return self._send(200, projects)
            if path.startswith("/v4/projects"):
                return self._send(200, None)
            if path.startswith("/v4/users"):
                return self._send(200, directory)
            if path.startswith("/api/users/v1/self"):
                return self._send(200, self_user)
            return self._send(404, None)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            writes.append(("POST", self.path, body))
            if add_status >= 400:
                # The real 400 for a duplicate: `errors`, not `message`, and the condition is
                # legible only in the English. Nothing here reads it, which is the point.
                return self._send(add_status, {"requestId": "r", "errors": [
                    f"User {body.get('id')} is already part of project {PROJECT}"]})
            return self._send(200, {"collaborator": body, "metadata": {"notices": []}})

        def do_DELETE(self):
            writes.append(("DELETE", self.path, None))
            if remove_status >= 400:
                return self._send(remove_status, {"requestId": "r", "errors": ["no"]})
            return self._send(200, {"success": True, "metadata": {}})

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", writes
    finally:
        server.shutdown()
        server.server_close()


def provider(api_host: str) -> DominoResourceProvider:
    return DominoResourceProvider(
        "http://gw/v1", lambda: "t", api_host=api_host, api_token_provider=lambda: "t",
    )


ADA = {"id": "u-ada", "fullName": "Ada Lovelace", "userName": "ada", "avatarUrl": "/a.png"}
GRACE = {"id": "u-grace", "fullName": "Grace Hopper", "userName": "grace", "avatarUrl": ""}
# The live record, field for field (probed 2026-09-03). The join key is `collaboratorId` — NOT
# `id`, which is what the name-bearing read calls the same person.
RECORD = {"id": PROJECT, "name": "quick-start", "ownerId": "u-ada",
          "collaborators": [{"collaboratorId": "u-grace", "projectRole": "Contributor"}]}


# ---- the provider: two reads, one answer ---------------------------------------------------------


def test_the_owner_is_a_row_with_no_role():
    """Domino's two reads disagree about the owner: the name-bearing read includes them and the
    role-bearing read does not. Dropping the owner would take the Project's owner off the list of
    people on the Project, so they are kept as a row that carries no role."""
    with stub_collaborator_api(people=[ADA, GRACE], projects=RECORD) as (host, _):
        out = provider(host).list_collaborators(PROJECT)

    assert [(c.id, c.name, c.role, c.owner) for c in out] == [
        ("u-ada", "Ada Lovelace", "", True),
        ("u-grace", "Grace Hopper", "Contributor", False),
    ]


def test_the_role_is_reported_in_the_spelling_domino_reads_it_back_in():
    """The write takes `contributor` and the read answers `Contributor`. Sage shows what it read —
    the raw platform value — rather than a word of its own, and never normalises it away."""
    record = dict(RECORD, collaborators=[{"collaboratorId": "u-grace", "projectRole": "ProjectImporter"}])
    with stub_collaborator_api(people=[ADA, GRACE], projects=record) as (host, _):
        out = provider(host).list_collaborators(PROJECT)
    assert out[1].role == "ProjectImporter"


def test_the_role_joins_on_the_key_the_role_bearing_record_actually_uses():
    """The two reads spell the same person's id differently: the names read calls it `id` and the
    roles read calls it `collaboratorId`. Joining on the wrong one matched nobody and answered every
    role as empty — no error, no missing row, just a column that was quietly always blank."""
    record = dict(RECORD, collaborators=[{"collaboratorId": "u-grace", "projectRole": "Contributor"}])
    with stub_collaborator_api(people=[ADA, GRACE], projects=record) as (host, _):
        assert provider(host).list_collaborators(PROJECT)[1].role == "Contributor"


def test_a_collaborator_with_no_full_name_is_still_nameable():
    """A service account has no `fullName`, and it is exactly the kind of account a Project gets
    added to. It is named by the name it does have rather than by its id."""
    bare = {"id": "u-sa", "userName": "repro-practitioner-sa"}
    record = dict(RECORD, collaborators=[{"collaboratorId": "u-sa", "projectRole": "Contributor"}])
    with stub_collaborator_api(people=[ADA, bare], projects=record) as (host, _):
        out = provider(host).list_collaborators(PROJECT)
    assert out[1].name == "repro-practitioner-sa"


def test_a_project_record_the_caller_cannot_read_is_reported_not_swallowed():
    """ADR-0028. Under the old contract this answered `[]`, and the People modal could not tell it
    from a Project with nobody else on it. The read failing is a different fact from the read
    finding nobody, and only the caller knows what each is worth."""
    with stub_collaborator_api(people=None, projects=RECORD) as (host, _):
        with pytest.raises(ResourceUnavailable):
            provider(host).list_collaborators(PROJECT)


def test_roles_the_caller_cannot_read_are_reported_too():
    """The names alone would render a list, but not one it is safe to act on: with no ownerId there
    is no way to know whose row must not offer Remove, and the design refuses to learn that from a
    403 after the click."""
    with stub_collaborator_api(people=[ADA, GRACE], projects=None) as (host, _):
        with pytest.raises(ResourceUnavailable):
            provider(host).list_collaborators(PROJECT)


def test_the_record_is_read_for_this_project_rather_than_picked_out_of_a_listing():
    """A first pass searched `GET /v4/projects` for the project. That answers 200 without it once a
    builder belongs to enough projects, and a healthy project would then report as a failed read.
    The single-project route answers about the project asked for, or refuses."""
    with stub_collaborator_api(people=[ADA, GRACE], projects=RECORD) as (host, _):
        assert len(provider(host).list_collaborators(PROJECT)) == 2


def test_a_record_that_names_no_owner_is_refused_rather_than_rendered():
    """The quieter half of the ownerId hazard: the record answered, so nothing failed, but every row
    would come back un-owned and the modal would offer Remove on the Project owner. The creator
    would learn better from a refusal after the click, which is what this read exists to prevent."""
    with stub_collaborator_api(people=[ADA, GRACE],
                               projects=dict(RECORD, ownerId="")) as (host, _):
        with pytest.raises(ResourceUnavailable):
            provider(host).list_collaborators(PROJECT)


def test_off_domino_there_is_nobody_on_the_project_and_that_is_not_a_failure():
    """No host and no project id is nothing to read, which is not the same as a read that failed.
    The service tells a caller which of the two it is; the provider only has to not confuse them."""
    assert FakeResourceProvider().list_collaborators(PROJECT) == []
    assert DominoResourceProvider("http://gw/v1", lambda: "t").list_collaborators(PROJECT) == []


# ---- the provider: the directory the picker offers -----------------------------------------------


def test_the_directory_is_everyone_on_the_deployment():
    """The picker offers people who are not on the Project yet, so it cannot be built from the
    Project's own list. The whole directory is fetched once and filtered in the browser."""
    rows = [ADA, GRACE, {"id": "u-sa", "fullName": "", "userName": "repro-practitioner-sa"}]
    with stub_collaborator_api(directory=rows) as (host, _):
        out = provider(host).list_directory()

    assert [p.id for p in out] == ["u-ada", "u-grace", "u-sa"]
    # An account with no full name is still pickable, under the name it does have.
    assert out[2].name == "repro-practitioner-sa"


def test_a_directory_that_cannot_be_read_is_reported():
    with stub_collaborator_api(directory=None) as (host, _), pytest.raises(ResourceUnavailable):
        provider(host).list_directory()


# ---- the provider: the writes --------------------------------------------------------------------


def test_adding_names_the_person_and_the_one_role_sage_assigns():
    """Lowercase `contributor`, which is the spelling the write takes. No role picker: the roles
    differ in ways a creator cannot judge from the Workbench."""
    with stub_collaborator_api(people=[ADA], projects=RECORD) as (host, writes):
        provider(host).add_collaborator(PROJECT, "u-grace")

    assert writes == [
        ("POST", f"/api/projects/v1/projects/{PROJECT}/collaborators",
         {"id": "u-grace", "role": "contributor"}),
    ]


def test_adding_somebody_already_there_is_reported_as_added():
    """Adding twice is a 400 whose only marker is an English sentence, and matching that sentence
    would break the day Domino rewords it. So any 400 is answered by re-reading the list once: if
    the person is on the Project, the creator's intent holds however it came about."""
    with stub_collaborator_api(people=[ADA, GRACE], projects=RECORD, add_status=400) as (host, _):
        provider(host).add_collaborator(PROJECT, "u-grace")  # no raise


def test_a_refused_add_that_the_re_read_does_not_confirm_still_fails():
    """The re-read is a check, not a shrug. A 400 the creator is not entitled to make must reach
    them as a refusal rather than a quiet success."""
    with stub_collaborator_api(people=[ADA], projects=RECORD, add_status=400) as (host, _):
        with pytest.raises(ResourceUnavailable):
            provider(host).add_collaborator(PROJECT, "u-grace")


def test_a_refusal_that_is_not_a_400_is_never_re_read():
    """A 403 is Domino saying no, and asking again cannot turn it into a yes."""
    with stub_collaborator_api(people=[ADA], projects=RECORD, add_status=403) as (host, writes):
        with pytest.raises(ResourceUnavailable):
            provider(host).add_collaborator(PROJECT, "u-grace")
    assert [w[0] for w in writes] == ["POST"]


def test_removing_names_the_person_in_the_path():
    with stub_collaborator_api(people=[ADA]) as (host, writes):
        provider(host).remove_collaborator(PROJECT, "u-grace")
    assert writes == [
        ("DELETE", f"/api/projects/v1/projects/{PROJECT}/collaborators/u-grace", None),
    ]


def test_a_refused_remove_is_reported():
    with stub_collaborator_api(people=[ADA], remove_status=403) as (host, _):
        with pytest.raises(ResourceUnavailable):
            provider(host).remove_collaborator(PROJECT, "u-grace")


# ---- the provider: who is asking -----------------------------------------------------------------


def test_the_caller_is_named_in_the_id_space_a_collaborator_is_named_in():
    """The modal has to find the caller's own row to keep Remove off it, and `/api/me` answers in
    the identity provider's id space rather than Domino's. This read is the one that matches."""
    with stub_collaborator_api(self_user={"user": {"id": "u-ada"}}) as (host, _):
        assert provider(host).caller_id() == "u-ada"


def test_a_caller_domino_will_not_name_is_nobody_rather_than_a_failure():
    """Not knowing who is asking costs two Remove buttons that Domino would refuse anyway. It is
    not worth failing the list over, and it is the one read here whose failure is not fatal."""
    with stub_collaborator_api(self_user=None) as (host, _):
        assert provider(host).caller_id() == ""


# ---- the fake: the whole flow runs off Domino ----------------------------------------------------


def test_the_fake_records_an_add_and_a_remove():
    fake = FakeResourceProvider(
        collaborators=[Collaborator(id="u-ada", name="Ada Lovelace", owner=True)],
        directory=[Person(id="u-grace", name="Grace Hopper", title="grace")],
    )
    fake.add_collaborator(PROJECT, "u-grace")
    assert [(c.id, c.role) for c in fake.list_collaborators(PROJECT)] == [
        ("u-ada", ""), ("u-grace", "Contributor"),
    ]
    fake.remove_collaborator(PROJECT, "u-grace")
    assert [c.id for c in fake.list_collaborators(PROJECT)] == ["u-ada"]


def test_the_fake_refuses_to_add_somebody_it_has_never_heard_of():
    """A picker offers the directory, so an id that is not in it did not come from the picker."""
    with pytest.raises(ResourceUnavailable):
        FakeResourceProvider().add_collaborator(PROJECT, "u-nobody")


# ---- the service: three states, never conflated --------------------------------------------------


def routed(tmp_path: Path, monkeypatch, *, project_id: str = PROJECT, resources=None):
    """The real orchestrator on a temp workspace, bound to the app the Workbench calls.

    `project_id` is the whole of "is this running against Domino": off it there is no project id,
    which is a different fact from a Project with nobody else on it.
    """
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod
    from sage.gateway.client import FakeGatewayClient
    from sage.orchestrator.service import Orchestrator
    from sage.router.models import ModelCatalog

    template = tmp_path / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("placeholder")
    (template / "package.json").write_text("{}")
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=template,
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        domino_project_id=project_id,
        resources=resources if resources is not None else FakeResourceProvider(),
    )
    orch.project(start_preview=False)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    return TestClient(appmod.control_app), orch


def peopled() -> FakeResourceProvider:
    return FakeResourceProvider(
        collaborators=[Collaborator(id="u-ada", name="Ada Lovelace", title="ada", owner=True)],
        directory=[
            Person(id="u-ada", name="Ada Lovelace", title="ada"),
            Person(id="u-grace", name="Grace Hopper", title="grace"),
        ],
        caller="u-ada",
    )


class NoDirectory(FakeResourceProvider):
    """Domino answers who is on the Project but will not list the deployment's people. Two reads,
    and only one of them is the plan page's."""

    def list_directory(self):
        raise ResourceUnavailable("The Domino API answered 503 at /v4/users.")


class Unreadable(FakeResourceProvider):
    """A platform that answers nothing. The fake's own empty lists are a working read of a Project
    with nobody on it, and the whole point here is that those two are not the same."""

    def list_collaborators(self, project_id):
        raise ResourceUnavailable("The Domino API answered 503 at /v4/projects.")

    def list_directory(self):
        raise ResourceUnavailable("The Domino API answered 503 at /v4/users.")


def test_not_being_on_domino_is_not_the_same_as_having_nobody_to_add(tmp_path: Path, monkeypatch):
    """Off Domino the fake's directory is empty by design. Reporting that as "nobody to add" would
    tell a creator their colleagues do not exist, when what is true is that Sage cannot see them."""
    client, _ = routed(tmp_path, monkeypatch, project_id="")
    body = client.get("/api/members").json()
    assert body["connected"] is False
    assert body["error"] == ""
    assert body["members"] == [] and body["directory"] == []


def test_a_project_worked_on_alone_says_so_rather_than_reporting_a_failure(tmp_path, monkeypatch):
    client, _ = routed(tmp_path, monkeypatch, resources=peopled())
    body = client.get("/api/members").json()
    assert body["connected"] is True
    assert body["error"] == ""
    assert [m["id"] for m in body["members"]] == ["u-ada"]
    assert [p["id"] for p in body["directory"]] == ["u-ada", "u-grace"]


def test_a_read_that_failed_is_reported_as_a_failure_and_not_as_an_empty_project(tmp_path, monkeypatch):
    """The third state, and the one ADR-0028 exists to make sayable. It carries a reason, so the
    modal can offer a Retry instead of an empty picker."""
    client, _ = routed(tmp_path, monkeypatch, resources=Unreadable())
    body = client.get("/api/members").json()
    assert body["connected"] is True
    assert "503" in body["error"]
    assert body["members"] == []


def test_the_plan_page_still_loads_when_nobody_can_be_named(tmp_path: Path, monkeypatch):
    """ADR-0028's other half. The provider now raises, and the forgiveness lives here: the plan page
    shows ids where it would show names, which is worse than names and better than a page that will
    not load. The status is what says so — a 502 here would be that page."""
    client, _ = routed(tmp_path, monkeypatch, resources=Unreadable())
    assert client.get("/api/members").status_code == 200


def test_a_directory_outage_does_not_take_the_reviewer_names_with_it(tmp_path, monkeypatch):
    """The two reads fail independently and cost different callers different things. The plan page
    never asks for the directory, so a directory outage must not blank the names it does ask for —
    sharing one `try` made a picker's failure into the plan page's."""
    resources = NoDirectory(
        collaborators=[Collaborator(id="u-ada", name="Ada Lovelace", owner=True)],
        caller="u-ada",
    )
    client, _ = routed(tmp_path, monkeypatch, resources=resources)
    body = client.get("/api/members").json()

    assert [m["id"] for m in body["members"]] == ["u-ada"]
    assert body["ownerId"] == "u-ada"
    # The modal still learns it cannot offer a picker, so it retries rather than showing an empty one.
    assert "503" in body["error"]
    assert body["directory"] == []


def test_the_owner_and_the_caller_are_named_so_their_rows_can_refuse_removal(tmp_path, monkeypatch):
    """Neither may be removed, and the design refuses to learn that from a refusal after the click.
    Both ids are in Domino's id space rather than the identity provider's, so they join."""
    client, _ = routed(tmp_path, monkeypatch, resources=peopled())
    body = client.get("/api/members").json()
    assert body["ownerId"] == "u-ada"
    assert body["self"] == "u-ada"


def test_a_members_row_carries_the_raw_platform_role(tmp_path: Path, monkeypatch):
    resources = peopled()
    resources.add_collaborator(PROJECT, "u-grace")
    client, _ = routed(tmp_path, monkeypatch, resources=resources)
    rows = {m["id"]: m["role"] for m in client.get("/api/members").json()["members"]}
    assert rows == {"u-ada": "", "u-grace": "Contributor"}


# ---- the routes: the server knows which Project it is --------------------------------------------


def test_adding_names_people_and_never_a_project(tmp_path: Path, monkeypatch):
    """A client-supplied project id is an authorization surface: it would let whoever can reach this
    route add people to a Project this Builder is not bound to. The server uses its own."""
    client, _ = routed(tmp_path, monkeypatch, resources=peopled())
    r = client.post("/api/collaborators", json={"userIds": ["u-grace"]})

    assert r.status_code == 200
    assert r.json() == {"added": ["u-grace"], "failed": []}
    assert [m["id"] for m in client.get("/api/members").json()["members"]] == ["u-ada", "u-grace"]


def test_a_project_id_in_the_body_is_ignored_rather_than_honoured(tmp_path: Path, monkeypatch):
    client, orch = routed(tmp_path, monkeypatch, resources=peopled())
    client.post("/api/collaborators",
                json={"userIds": ["u-grace"], "projectId": "some-other-project"})
    # The fake ignores the project argument, so the claim is read off what was passed to it.
    assert [c.id for c in orch._resources.list_collaborators(PROJECT)] == ["u-ada", "u-grace"]


def test_one_person_failing_does_not_take_the_others_with_them(tmp_path: Path, monkeypatch):
    """Nothing is rolled back and nothing is hidden. The people who were added are added, and the
    one who was not is named so the modal can keep them selected for a retry."""
    client, _ = routed(tmp_path, monkeypatch, resources=peopled())
    r = client.post("/api/collaborators", json={"userIds": ["u-grace", "u-nobody"]}).json()

    assert r["added"] == ["u-grace"]
    assert [f["id"] for f in r["failed"]] == ["u-nobody"]
    assert r["failed"][0]["reason"]


def test_adding_nobody_is_a_bad_request_rather_than_a_quiet_success(tmp_path: Path, monkeypatch):
    client, _ = routed(tmp_path, monkeypatch, resources=peopled())
    assert client.post("/api/collaborators", json={"userIds": []}).status_code == 400


def test_a_body_that_is_not_a_request_is_refused_rather_than_crashing(tmp_path, monkeypatch):
    """A malformed body is a bad request, not a server fault, and the route that reports it as one
    is the route that stays diagnosable."""
    client, _ = routed(tmp_path, monkeypatch, resources=peopled())
    r = client.post("/api/collaborators", content=b"not json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_one_name_sent_where_a_list_belongs_is_refused_not_read_letter_by_letter(tmp_path, monkeypatch):
    """A bare string is iterable, so the singular-for-plural slip would be read one character at a
    time and answered 200 with a refusal per letter."""
    client, _ = routed(tmp_path, monkeypatch, resources=peopled())
    r = client.post("/api/collaborators", json={"userIds": "u-grace"})
    assert r.status_code == 400


def test_removing_takes_the_person_off_the_project(tmp_path: Path, monkeypatch):
    resources = peopled()
    resources.add_collaborator(PROJECT, "u-grace")
    client, _ = routed(tmp_path, monkeypatch, resources=resources)

    assert client.delete("/api/collaborators/u-grace").status_code == 200
    assert [m["id"] for m in client.get("/api/members").json()["members"]] == ["u-ada"]


def test_a_refused_remove_reaches_the_caller_as_a_refusal(tmp_path: Path, monkeypatch):
    class Refusing(FakeResourceProvider):
        def remove_collaborator(self, project_id, user_id):
            raise ResourceUnavailable("The Domino API answered 403 at /api/projects/v1.")

    client, _ = routed(tmp_path, monkeypatch, resources=Refusing())
    r = client.delete("/api/collaborators/u-grace")
    assert r.status_code == 502
    assert "403" in r.json()["error"]


def test_writing_off_domino_says_so_rather_than_pretending(tmp_path: Path, monkeypatch):
    client, _ = routed(tmp_path, monkeypatch, project_id="")
    assert client.post("/api/collaborators", json={"userIds": ["u-grace"]}).status_code == 502
    assert client.delete("/api/collaborators/u-grace").status_code == 502


# ---- the modal: what a creator actually reads ----------------------------------------------------

_HARNESS = Path(__file__).resolve().parent / "js" / "people_modal_harness.mjs"

needs_node = pytest.mark.skipif(
    __import__("shutil").which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def modal(**spec) -> dict:
    import subprocess

    out = subprocess.run(
        ["node", str(_HARNESS)], input=json.dumps(spec),
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


ADA_ROW = {"id": "u-ada", "name": "Ada Lovelace", "title": "ada", "role": ""}
GRACE_ROW = {"id": "u-grace", "name": "Grace Hopper", "title": "grace", "role": "Contributor"}
PRIYA = {"id": "u-priya", "name": "Priya Raman", "title": "priya"}


@needs_node
def test_not_connected_says_sage_cannot_see_rather_than_that_there_is_nobody():
    """The state the fake provider produces off Domino. "No people found" here would be a claim
    about the creator's colleagues, made from a failure to look at them."""
    said = " ".join(modal(connected=False)["said"])
    assert "cannot see who else works here" in said
    # No picker, because there is nothing to pick from and offering one would imply there is.
    assert modal(connected=False)["offers"] == []


@needs_node
def test_a_failed_read_offers_the_platforms_own_words_and_something_to_do():
    """Not the empty state, and not a dead end. The reason is Domino's sentence passed through —
    Sage does not restate a platform error in its own words."""
    out = modal(connected=True, error="The Domino API answered 503 at /v4/projects.")
    said = " ".join(out["said"])
    assert "The Domino API answered 503 at /v4/projects." in said
    assert "Try again" in said


@needs_node
def test_our_own_server_not_answering_reads_as_a_failure_and_not_as_being_off_the_platform():
    """`connected` false with a reason set is "we could not look", not "there is nothing to look
    at". The Workbench cannot tell whether Sage is on the platform when its own server said nothing,
    so it offers the Retry rather than making a claim it has no answer to support."""
    out = modal(connected=False, error="502 Bad Gateway")
    said = " ".join(out["said"])
    assert "Try again" in said
    assert "cannot see who else works here" not in said


@needs_node
def test_a_project_worked_on_alone_still_offers_the_picker():
    """The third state. Nobody else on it is a fact about the Project, not a failure, so the modal
    reads as a working one with an empty list."""
    out = modal(connected=True, members=[ADA_ROW], directory=[PRIYA], ownerId="u-ada")
    assert out["offers"] == ["u-priya"]
    assert "Ada Lovelace" in out["said"]


@needs_node
def test_the_picker_offers_the_people_who_are_not_on_the_project_yet():
    """The old modal filtered the directory against the members and the server sent the members AS
    the directory, so the picker was always empty. These are two different reads now."""
    out = modal(connected=True, members=[ADA_ROW, GRACE_ROW],
                directory=[{"id": "u-ada", "name": "Ada Lovelace"},
                           {"id": "u-grace", "name": "Grace Hopper"}, PRIYA],
                ownerId="u-ada")
    assert out["offers"] == ["u-priya"]


@needs_node
def test_the_owner_and_you_say_why_instead_of_offering_a_remove():
    """A 403 after the click would say the same thing one action too late, and only to somebody who
    clicked. Two rows, two reasons, no Remove on either."""
    out = modal(connected=True, members=[ADA_ROW, GRACE_ROW], ownerId="u-ada", selfId="u-grace")
    said = out["said"]
    assert "Project owner" in said and "You" in said
    assert out["removable"] == 0


@needs_node
def test_a_colleague_who_is_neither_can_be_removed():
    """The other half of the claim above — the row that should offer Remove does offer it, so the
    test is about who is exempt rather than about nobody having the button."""
    out = modal(connected=True, members=[ADA_ROW, GRACE_ROW], ownerId="u-ada", selfId="u-ada")
    assert out["removable"] == 1


@needs_node
def test_the_role_that_cannot_open_a_published_app_says_so_on_its_row():
    """A role name does not carry this, and it is the one thing that breaks the reason most people
    are added: to look at what got built. Case-folded, because Domino writes it in one case and
    reads it back in another."""
    row = dict(GRACE_ROW, role="ProjectImporter")
    said = " ".join(modal(connected=True, members=[ADA_ROW, row], ownerId="u-ada")["said"])
    assert "Cannot open published Built Apps." in said
    # The raw platform value is shown beside it rather than replaced by Sage's explanation.
    assert "ProjectImporter" in said


@needs_node
def test_removal_names_both_things_it_takes_away():
    """Under GRANT_BASED visibility one act does both, and a creator removing somebody from the
    Project may not have the App in mind. Read off the named confirm rather than by clicking, which
    is why the copy is named at all."""
    title, content = modal(connected=True, members=[GRACE_ROW])["confirm"]
    assert title == "Remove Grace Hopper from this Project?"
    assert content == (
        "They will lose access to this Project and to any Built App published from it."
    )


@needs_node
def test_a_selection_walked_away_from_does_not_come_back_with_the_modal():
    """The modal is mounted for the life of the Shell and only returns null, so React keeps its
    state across a close. A creator who picks two people and presses Escape would reopen on
    "Add 2 people", one click from adding people they had decided against."""
    out = modal(connected=True, directory=[PRIYA], reopen=["u-priya"])
    assert out["reopenedWith"] == []


@needs_node
def test_the_stack_shows_who_is_on_the_project_rather_than_who_is_present():
    """It used to filter on a `presence` field the server has never sent, so it rendered nobody.
    Nothing implements presence, and a faked dot beside an absent person is worse than no dot."""
    out = modal(connected=True, members=[ADA_ROW, GRACE_ROW], ownerId="u-ada")
    said = " ".join(out["stackSaid"])
    assert "Ada Lovelace" in said and "Grace Hopper" in said
    assert "is-active" not in said and "is-idle" not in said


def test_the_word_invite_is_gone_from_the_workbench():
    """CONTEXT.md rules it out: Sage cannot invite, because there is no acceptance step to wait on.
    A person is on the Project the moment the creator picks them."""
    js = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
    for path in [*js.glob("*.js"), *js.glob("components/*.js"), *js.glob("modes/*.js")]:
        assert "invite" not in path.read_text().lower(), path.name
