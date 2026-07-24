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
    assert b["environmentRevisionId"] == "rev-1"
    assert b["hardwareTierId"] == {"value": "tier-1"}
    assert b["tools"] == ["sageBuilder"]
    assert b["externalVolumeMounts"] == []


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
