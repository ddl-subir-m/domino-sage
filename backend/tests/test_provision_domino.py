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


def test_owner_id():
    cp = _cp(lambda req: httpx.Response(200, json={"id": "oid-123"}))
    assert cp.owner_id() == "oid-123"


def test_create_project_sends_git_ref_and_tag():
    seen = {}

    def handler(request):
        if request.url.path.endswith("/users/self"):
            return httpx.Response(200, json={"id": "oid-9"})
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "proj-42"})

    ref = _cp(handler).create_project("My App", git_url="https://github.com/me/sage-my-app.git")
    assert ref.id == "proj-42"
    assert ref.name == "My App"
    assert seen["path"] == "/v4/projects"
    b = seen["body"]
    assert b["ownerId"] == "oid-9"
    assert b["visibility"] == "Private"
    assert b["tags"] == {"tagNames": ["sage"]}
    assert b["mainGitRepoRef"] == {"type": "branches", "value": "main"}
    assert b["mainRepository"]["uri"] == "https://github.com/me/sage-my-app.git"


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


def test_list_apps_filters_by_tag():
    projects = {
        "data": [
            {"id": "p1", "name": "Sage One", "tags": {"tagNames": ["sage"]}, "mainRepository": {"uri": "https://github.com/me/sage-one.git"}},
            {"id": "p2", "name": "Other", "tags": {"tagNames": ["ml"]}},
        ]
    }
    cp = _cp(lambda req: httpx.Response(200, json=projects))
    apps = cp.list_apps()
    assert [a.id for a in apps] == ["p1"]
    assert apps[0].git_url == "https://github.com/me/sage-one.git"
