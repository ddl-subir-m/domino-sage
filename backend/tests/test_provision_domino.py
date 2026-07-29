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
    ref = _cp(_creds_handler(resp, seen)).create_project("My App", git_url="https://github.com/me/sage-my-app.git")
    assert ref.id == "proj-42"
    assert ref.name == "My App"
    assert seen["path"] == "/api/projects/beta/projects"
    b = seen["body"]
    # NewProjectV1: no ownerId (defaults to caller), no tags/collaborators/mainGitRepoRef.
    assert "ownerId" not in b
    assert "tags" not in b
    assert b["visibility"] == "Private"
    # Matched the github.com https credential, not the gitlab one.
    assert b["mainRepository"] == {
        "uri": "https://github.com/me/sage-my-app.git",
        "serviceProvider": "Github",
        "defaultRef": {"refType": "Branch", "value": "main"},
        "gitCredentialId": "cred-gh",
    }


def test_create_project_errors_without_matching_credential():
    def handler(request):
        if request.url.path == "/api/users/v1/self":
            return httpx.Response(200, json={"user": {"id": "user-1"}})
        if request.url.path == "/api/users/beta/credentials/user-1":
            return httpx.Response(200, json={"credentials": []})  # none for github.com
        raise AssertionError("should not POST without a credential")

    try:
        _cp(handler).create_project("X", git_url="https://github.com/me/sage-x.git")
    except RuntimeError as e:
        assert "github.com" in str(e)
    else:
        raise AssertionError("expected RuntimeError when no git credential matches")


def test_create_project_surfaces_error_body():
    resp = httpx.Response(400, text='{"message":"bad visibility"}')
    cp = _cp(_creds_handler(resp, {}))
    try:
        cp.create_project("X", git_url="https://github.com/me/sage-x.git")
    except RuntimeError as e:
        assert "400" in str(e)
        assert "bad visibility" in str(e)
    else:
        raise AssertionError("expected RuntimeError on 400")


def test_tag_dataset_sensitive_fetches_snapshot_when_none_given():
    # For an existing dataset we don't have a snapshot id, so we GET it, then tag that snapshot.
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/api/datasetrw/v1/datasets/ds-1":
            return httpx.Response(200, json={"dataset": {"id": "ds-1", "latestSnapshotId": "snap-9"}})
        if request.url.path == "/api/datasetrw/v1/datasets/ds-1/tags":
            assert json.loads(request.content) == {"tagName": "sensitive", "snapshotId": "snap-9"}
            return httpx.Response(200, json={"dataset": {"id": "ds-1"}})
        raise AssertionError(f"unexpected call: {request.method} {request.url.path}")

    assert _cp(handler).tag_dataset_sensitive("ds-1") is True
    assert ("GET", "/api/datasetrw/v1/datasets/ds-1") in calls        # fetched the snapshot first
    assert calls[-1] == ("POST", "/api/datasetrw/v1/datasets/ds-1/tags")


def test_tag_dataset_sensitive_uses_supplied_snapshot_without_fetch():
    # When the caller already has a snapshot id (from the v2 tag map), skip the GET.
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/datasetrw/v1/datasets/ds-1/tags":
            assert json.loads(request.content) == {"tagName": "sensitive", "snapshotId": "snap-x"}
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected call: {request.method} {request.url.path}")

    assert _cp(handler).tag_dataset_sensitive("ds-1", snapshot_id="snap-x") is True
    assert calls == [("POST", "/api/datasetrw/v1/datasets/ds-1/tags")]   # no GET


def test_tag_dataset_sensitive_returns_false_on_failure():
    # Best-effort: a datasetrw error must never bubble up and block an upload.
    assert _cp(lambda req: httpx.Response(500, text="boom")).tag_dataset_sensitive("ds-1", snapshot_id="s") is False


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
    assert b["environmentRevisionSpec"] == {"revisionId": "rev-1"}
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


def test_find_project_app_matches_on_nested_project_id():
    seen = {}
    # Live schema: {"items": [...]}, global list, project nested under item["project"]["id"].
    apps = {"items": [
        {"id": "app-other", "project": {"id": "proj-99"}, "url": "https://d/other"},
        {"id": "app-mine", "project": {"id": "proj-42"}, "url": "https://d/mine"},
    ], "metadata": {}}

    def handler(request):
        seen["path"] = request.url.path
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json=apps)

    app = _cp(handler).find_project_app("proj-42")
    assert seen["path"] == "/api/apps/beta/apps"
    assert "projectId=proj-42" in seen["query"]
    assert app is not None and app.id == "app-mine"  # matched project.id, skipped the other project's app
    assert app.url == "https://d/mine"


def test_list_project_apps_filters_by_nested_project_id():
    # Global list; only the two items nested under our project.id (and carrying an id) are returned.
    apps = {"items": [
        {"id": "app-a", "project": {"id": "proj-42"}, "url": "https://d/a"},
        {"id": "app-b", "project": {"id": "proj-99"}, "url": "https://d/b"},
        {"id": "app-c", "project": {"id": "proj-42"}, "url": "https://d/c"},
        {"project": {"id": "proj-42"}},  # no id -> skipped
    ], "metadata": {}}
    out = _cp(lambda req: httpx.Response(200, json=apps)).list_project_apps("proj-42")
    assert [a.id for a in out] == ["app-a", "app-c"]
    assert out[0].url == "https://d/a"


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


def test_find_project_app_returns_none_when_no_app_matches():
    # Global list with only another project's app -> no match for ours.
    apps = {"items": [{"id": "app-other", "project": {"id": "proj-99"}}], "metadata": {}}
    assert _cp(lambda req: httpx.Response(200, json=apps)).find_project_app("proj-42") is None


def test_find_project_app_returns_none_when_no_apps():
    cp = _cp(lambda req: httpx.Response(200, json={"items": [], "metadata": {}}))
    assert cp.find_project_app("proj-42") is None


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
