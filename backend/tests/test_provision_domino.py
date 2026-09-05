import json

import httpx

from sage.provision.domino import DominoControlPlane


def _cp(handler):
    return DominoControlPlane(
        "https://domino.example.com",
        lambda: "tok",
        environment_id="env-1",
        environment_revision_id="rev-1",
        hardware_tier_id="tier-1",
        transport=httpx.MockTransport(handler),
    )


def _creds_handler(post_response, seen):
    """Route the users/self + credentials GETs the create flow makes, then the project POST."""
    def handler(request):
        path = request.url.path
        if path == "/api/users/v1/self":
            return httpx.Response(200, json={"user": {"id": "user-1"}, "metadata": {}})
        if path == "/api/users/beta/credentials/user-1":
            return httpx.Response(200, json={"credentials": [
                {"id": "cred-gh", "domain": "github.com", "protocol": "https",
                 "gitServiceProvider": "Github", "name": "gh", "fingerprint": "x"},
                {"id": "cred-gl", "domain": "gitlab.com", "protocol": "https",
                 "gitServiceProvider": "GitLab", "name": "gl", "fingerprint": "y"},
            ], "metadata": {}})
        seen["path"] = path
        seen["body"] = json.loads(request.content)
        return post_response
    return handler


def test_create_project_uses_public_api_shape():
    seen = {}
    # ProjectEnvelopeV1: {project: {...}, metadata: {...}}
    resp = httpx.Response(200, json={"project": {"id": "proj-42", "name": "My App"}, "metadata": {}})
    ref = _cp(_creds_handler(resp, seen)).create_project(
        "My App", git_url="https://github.com/me/sage-my-app.git", git_credential_id="cred-gh")
    assert ref.id == "proj-42"
    assert ref.name == "My App"
    assert seen["path"] == "/api/projects/beta/projects"
    b = seen["body"]
    # NewProjectV1: no ownerId (defaults to caller), no tags/collaborators/mainGitRepoRef.
    assert "ownerId" not in b
    assert "tags" not in b
    assert b["visibility"] == "Private"
    # The credential the caller chose, sent through untouched (ADR-0033).
    assert b["mainRepository"] == {
        "uri": "https://github.com/me/sage-my-app.git",
        "serviceProvider": "Github",
        "defaultRef": {"refType": "Branch", "value": "main"},
        "gitCredentialId": "cred-gh",
    }


def test_git_credentials_reports_labels_and_usability():
    """The provider reports; it picks nothing (ADR-0028/0033). Live field set verified 2026-09-04:
    domain, fingerprint, gitServiceProvider, id, name, protocol — and no username."""
    def handler(request):
        if request.url.path == "/api/users/v1/self":
            return httpx.Response(200, json={"user": {"id": "user-1"}})
        if request.url.path == "/api/users/beta/credentials/user-1":
            return httpx.Response(200, json={"credentials": [
                {"id": "c1", "domain": "github.com", "protocol": "https", "name": "old PAT",
                 "fingerprint": "3b:0f:6f:db", "gitServiceProvider": "Github"},
                {"id": "c2", "domain": "github.com", "protocol": "ssh", "name": "my key"},
                {"id": "c3", "domain": "gitlab.com", "protocol": "https", "name": "work GitLab"},
                {"domain": "github.com", "protocol": "https", "name": "no id"},  # unusable, skipped
            ]})
        raise AssertionError(f"unexpected call to {request.url.path}")

    creds = _cp(handler).git_credentials()
    assert [c.id for c in creds] == ["c1", "c2", "c3"]  # the id-less entry is dropped
    assert [c.usable for c in creds] == [True, False, False]
    assert [c.label for c in creds] == [
        "old PAT (github.com)",
        "my key (github.com) [SSH]",     # right host, wrong protocol — and it says so
        "work GitLab (gitlab.com)",
    ]
    # No secret leaves the provider, and the fingerprint is left out on purpose (ADR-0033).
    assert not any("3b:0f" in c.label for c in creds)


def test_create_project_reports_a_refusal_without_judging_it():
    """A rejection goes back as Domino wrote it. Deciding what it costs is the caller's (ADR-0028),
    and the real body is `errors[]` with a fresh requestId, not `message`."""
    body = ('{"requestId":"5974-abc","errors":["Cannot access Git repository with URI: '
            'https://github.com/me/sage-x.git. This may be due to invalid Git credentials."]}')

    def handler(request):
        return httpx.Response(500, text=body)

    try:
        _cp(handler).create_project("X", git_url="https://github.com/me/sage-x.git",
                                    git_credential_id="cred-a")
    except RuntimeError as e:
        assert "Cannot access Git repository" in str(e)
        assert "cred-a" not in str(e)  # the provider adds nothing of its own
    else:
        raise AssertionError("expected RuntimeError on 500")


def test_create_project_surfaces_error_body():
    resp = httpx.Response(400, text='{"message":"bad visibility"}')
    cp = _cp(_creds_handler(resp, {}))
    try:
        cp.create_project("X", git_url="https://github.com/me/sage-x.git", git_credential_id="cred-gh")
    except RuntimeError as e:
        assert "400" in str(e)
        assert "bad visibility" in str(e)
    else:
        raise AssertionError("expected RuntimeError on 400")


def test_create_workspace_body():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "ws-1"})

    ws = _cp(handler).create_workspace("proj-42")
    assert ws["id"] == "ws-1"
    assert seen["path"] == "/v4/workspace/project/proj-42/workspace"
    b = seen["body"]
    assert b["environmentId"] == "env-1"
    # No revision pin: a builder takes the Environment's active revision, so rebuilding the
    # Environment reaches new builders without restarting the Workbench App.
    assert "environmentRevisionSpec" not in b
    assert b["hardwareTierId"] == {"value": "tier-1"}
    assert b["tools"] == ["sageBuilder"]
    assert b["name"] == "sage"  # names the builder so the hub can tell it from other workspaces
    assert b["externalVolumeMounts"] == []
    assert "mainGitRepoRef" not in b  # invalid field; branch comes from the project's defaultRef


def test_stop_workspace_posts_to_stop_path():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"id": "ws-1", "state": "Stopped"})

    out = _cp(handler).stop_workspace("proj-42", "ws-1")
    assert seen["method"] == "POST"
    assert seen["path"] == "/v4/workspace/project/proj-42/workspace/ws-1/stop"
    assert out["state"] == "Stopped"


def test_resume_workspace_starts_a_new_session_on_the_existing_workspace():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["query"] = request.url.query.decode()
        seen["body"] = request.content
        return httpx.Response(200, json={"id": "sess-1", "executionId": "exec-1"})

    out = _cp(handler).resume_workspace("proj-42", "ws-1")
    assert seen["method"] == "POST"
    # Inverse of stop: start a session on the existing workspace (not /v4/workspaces/relaunch).
    assert seen["path"] == "/v4/workspace/project/proj-42/workspace/ws-1/sessions"
    assert seen["body"] == b""  # no request body
    # Required param must be PRESENT but empty; the server 400s ("Missing parameter") if absent.
    assert seen["query"] == "externalVolumeMounts="
    assert out["id"] == "sess-1"


def test_delete_workspace_deletes_via_v4_path():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"deleted": True})

    out = _cp(handler).delete_workspace("proj-42", "ws-1")
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/v4/workspace/project/proj-42/workspace/ws-1"
    assert out == {"deleted": True}


def test_save_workspace_work_posts_to_builder_sync():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"status": "merged", "pushed": True})

    # The builder is reached through its own notebookSession proxy path + /api/project/sync.
    out = _cp(handler).save_workspace_work("/u/My%20App/notebookSession/run-9/")
    assert seen["method"] == "POST"
    assert seen["path"] == "/u/My App/notebookSession/run-9/api/project/sync"
    assert out["pushed"] is True


def test_save_workspace_work_surfaces_error_body():
    cp = _cp(lambda req: httpx.Response(502, text='{"error":"builder unreachable"}'))
    try:
        cp.save_workspace_work("/u/App/notebookSession/run-9/")
    except RuntimeError as e:
        assert "502" in str(e) and "builder unreachable" in str(e)
    else:
        raise AssertionError("expected RuntimeError on 502")


def test_stop_workspace_tolerates_empty_body():
    out = _cp(lambda req: httpx.Response(200, text="")).stop_workspace("p", "ws")
    assert out == {}  # empty body -> no error, empty dict


def test_a_published_app_still_pins_its_environment_revision():
    # The opposite of create_workspace on purpose: a deployed Built App keeps running the image it
    # was tested on, while a builder follows the Environment so a rebuild reaches it.
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "app-9", "url": ""})

    _cp(handler).publish_app("proj-42", name="My App")

    assert seen["body"]["version"]["environmentRevisionId"] == "rev-1"


def test_publish_app_posts_create_and_launch_body():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "app-9", "url": "https://domino.example.com/u/p/app-9"})

    app = _cp(handler).publish_app("proj-42", name="My App")
    assert app.id == "app-9"
    assert app.url == "https://domino.example.com/u/p/app-9"
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/apps/beta/apps"
    b = seen["body"]
    assert b["name"] == "My App"
    assert b["projectId"] == "proj-42"
    assert b["entryPoint"] == "app.sh"
    assert b["configurationType"] == "STANDARD"
    # The version launches on the hub's own env + tier; "head" ref omits a value.
    assert b["version"] == {
        "environmentId": "env-1",
        "hardwareTierId": "tier-1",
        "gitRef": {"type": "head"},
        "environmentRevisionId": "rev-1",
    }


def test_publish_app_pins_a_git_ref_value_when_given():
    seen = {}
    cp = _cp(lambda req: (seen.update(body=json.loads(req.content)), httpx.Response(200, json={"id": "a"}))[1])
    cp.publish_app("p", name="X", git_ref_type="commitId", git_ref_value="abc123")
    assert seen["body"]["version"]["gitRef"] == {"type": "commitId", "value": "abc123"}


def test_republish_app_posts_new_version_and_keeps_app_id():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "version-77"})  # version id, NOT the app id

    app = _cp(handler).republish_app("app-9")
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/apps/beta/apps/app-9/versions"
    assert seen["body"] == {
        "environmentId": "env-1",
        "hardwareTierId": "tier-1",
        "gitRef": {"type": "head"},
        "environmentRevisionId": "rev-1",
    }
    assert app.id == "app-9"  # keeps the caller's app id, not the version id
    # A version response carries no URL, so the viewer URL is derived from the app id.
    assert app.url == "/modelproducts/app-9?scope=project"


def test_publish_app_rewrites_the_apps_internal_url_that_404s():
    # Live on cloud-dogfood 2026-08-07: create returned an /apps-internal/{id} URL that 404s in the
    # browser; Domino's own "Copy URL" for the same app is /modelproducts/{id}?scope=project.
    handler = lambda req: httpx.Response(
        200, json={"id": "6a76", "url": "https://apps.cloud-dogfood.domino.tech/apps-internal/6a76"}
    )
    app = _cp(handler).publish_app("proj-42", name="My App")
    # Host-relative: /modelproducts lives on the main host, so the UI resolves it browser-side.
    assert app.url == "/modelproducts/6a76?scope=project"


def test_a_republish_returns_the_same_viewer_url_as_the_create_did():
    """A Built App re-publishes to the App it recorded and the link already shared keeps working
    (#70). The two calls answer from different places — create is handed a URL, a version response
    carries none — so this pins that they still land on the same one."""
    created = _cp(lambda req: httpx.Response(
        200, json={"id": "6a76", "url": "https://apps.cloud-dogfood.domino.tech/apps-internal/6a76"}
    )).publish_app("proj-42", name="My App")

    again = _cp(lambda req: httpx.Response(200, json={"id": "ver-2"})).republish_app(created.id)

    assert again.id == created.id
    assert again.url == created.url == "/modelproducts/6a76?scope=project"


def test_list_project_apps_filters_by_nested_project_id():
    # Live schema: {"items": [...]}, global list, project nested under item["project"]["id"]. Only
    # the two items nested under our project.id (and carrying an id) come back.
    seen = {}
    apps = {"items": [
        {"id": "app-a", "project": {"id": "proj-42"}, "url": "https://d/a"},
        {"id": "app-b", "project": {"id": "proj-99"}, "url": "https://d/b"},
        {"id": "app-c", "project": {"id": "proj-42"}, "url": "https://d/c"},
        {"project": {"id": "proj-42"}},  # no id -> skipped
    ], "metadata": {}}

    def handler(request):
        seen["path"] = request.url.path
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json=apps)

    out = _cp(handler).list_project_apps("proj-42")
    assert seen["path"] == "/api/apps/beta/apps"
    assert "projectId=proj-42" in seen["query"]
    assert [a.id for a in out] == ["app-a", "app-c"]
    assert out[0].url == "https://d/a"


def test_list_project_apps_is_empty_when_no_app_matches():
    # Global list with only another project's app -> nothing for ours.
    apps = {"items": [{"id": "app-other", "project": {"id": "proj-99"}}], "metadata": {}}
    assert _cp(lambda req: httpx.Response(200, json=apps)).list_project_apps("proj-42") == []


def test_delete_app_deployment_deletes_via_beta_apps_api():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"deleted": True})

    out = _cp(handler).delete_app_deployment("app-9")
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/api/apps/beta/apps/app-9"
    assert out == {"deleted": True}


def test_delete_app_deployment_accepts_an_empty_204_body():
    # The live API answers a successful DELETE with 204/no body; r.json() on that used to raise
    # "Expecting value: line 1 column 1 (char 0)", which surfaced in the hub as a FAILED delete
    # even though the App was gone, and aborted the archive that followed it.
    handler = lambda req: httpx.Response(204)
    assert _cp(handler).delete_app_deployment("app-9") == {"deleted": True}


def test_app_manage_url_builds_settings_deep_link():
    def handler(request):
        if request.url.path == "/api/apps/beta/apps/app-9":
            # The route is appId/appVersionId, so the version id comes from the app detail.
            return httpx.Response(200, json={"currentVersion": {"id": "ver-3"}})
        assert request.url.path == "/api/users/v1/self"
        return httpx.Response(200, json={"user": {"id": "u1", "userName": "subir_mansukhani"}})

    url = _cp(handler).app_manage_url("app-9", "My App")
    # Host-relative (DOMINO_API_HOST is internal-only); the UI resolves it to the external host.
    # appId/appVersionId — the project id is NOT in the path.
    assert url == "/u/subir_mansukhani/My%20App/apps/app-9/ver-3/details/overview"


def test_app_manage_url_omitted_when_version_unresolvable():
    def handler(request):
        if request.url.path == "/api/apps/beta/apps/app-9":
            return httpx.Response(200, json={})  # no currentVersion → no safe link
        return httpx.Response(200, json={"user": {"id": "u1", "userName": "subir_mansukhani"}})

    assert _cp(handler).app_manage_url("app-9", "My App") is None


def test_app_status_reads_nested_instance_status():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={"currentVersion": {"currentInstance": {"status": "Failed"}}})

    status = _cp(handler).app_status("app-9")
    assert seen["path"] == "/api/apps/beta/apps/app-9"
    assert status == "Failed"


def test_app_status_tolerates_missing_instance():
    cp = _cp(lambda req: httpx.Response(200, json={"currentVersion": {}}))
    assert cp.app_status("app-9") == ""


def test_list_project_apps_is_empty_when_the_deployment_has_no_apps():
    cp = _cp(lambda req: httpx.Response(200, json={"items": [], "metadata": {}}))
    assert cp.list_project_apps("proj-42") == []


def test_publish_app_surfaces_error_body():
    cp = _cp(lambda req: httpx.Response(400, text='{"message":"bad hardware tier"}'))
    try:
        cp.publish_app("p", name="X")
    except RuntimeError as e:
        assert "400" in str(e) and "bad hardware tier" in str(e)
    else:
        raise AssertionError("expected RuntimeError on 400")


def test_fake_publish_then_republish_returns_same_app():
    from sage.provision.domino import FakeControlPlane

    fake = FakeControlPlane()
    app = fake.publish_app("proj-1", name="X")
    assert app.id in fake.published
    assert fake.republish_app(app.id).url == app.url  # stable URL across versions
    assert fake.republish_app("unknown").id == "unknown"  # tolerates an unknown id


def test_list_apps_filters_by_repo_prefix():
    projects = {
        "projects": [
            {"project": {"id": "p1", "name": "Sage One", "mainRepository": {"uri": "https://github.com/me/sage-one.git"}}},
            {"project": {"id": "p2", "name": "Other", "mainRepository": {"uri": "https://github.com/me/analytics.git"}}},
            {"project": {"id": "p3", "name": "No Repo"}},
        ],
        "metadata": {},
    }
    cp = _cp(lambda req: httpx.Response(200, json=projects))
    apps = cp.list_apps()
    assert [a.id for a in apps] == ["p1"]
    assert apps[0].git_url == "https://github.com/me/sage-one.git"


def test_archive_project_deletes_via_public_projects_api():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"archived": True})

    out = _cp(handler).archive_project("proj-42")
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/api/projects/beta/projects/proj-42"
    assert out == {"archived": True}


def test_archive_project_surfaces_error_body():
    cp = _cp(lambda req: httpx.Response(403, text='{"message":"not the owner"}'))
    try:
        cp.archive_project("proj-42")
    except RuntimeError as e:
        assert "403" in str(e) and "not the owner" in str(e)
    else:
        raise AssertionError("expected RuntimeError on 403")


def test_the_new_projects_description_names_the_packs_assistant(tmp_path, monkeypatch):
    """The description Sage writes on a Project it creates is prose a person reads in the
    platform's own UI, so an OEM pack renames it (#109). A description the caller passed in is
    text Sage did not write and is left exactly as it came."""
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "none.json")
    pack = tmp_path / "brand.json"
    pack.write_text(json.dumps({"productName": "Acme", "assistantName": "Ada"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(pack))
    resp = httpx.Response(200, json={"project": {"id": "p1", "name": "My App"}, "metadata": {}})

    seen = {}
    _cp(_creds_handler(resp, seen)).create_project(
        "My App", git_url="https://github.com/me/sage-x.git", git_credential_id="cred-gh")
    assert seen["body"]["description"] == "Created by Ada"

    seen = {}
    _cp(_creds_handler(resp, seen)).create_project(
        "My App", git_url="https://github.com/me/sage-x.git", git_credential_id="cred-gh",
        description="Domino demo",
    )
    assert seen["body"]["description"] == "Domino demo"
