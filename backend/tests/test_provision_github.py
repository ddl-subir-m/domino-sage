import json

import httpx
import pytest

from sage.provision.github import GitHubProvider, RepoNameConflict, RepoProviderError


def _provider(handler):
    return GitHubProvider(lambda: "tok", transport=httpx.MockTransport(handler))


def test_create_repo_success_sends_right_shape():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["version"] = request.headers["x-github-api-version"]
        body = json.loads(request.content)
        seen["body"] = body
        return httpx.Response(
            201,
            json={"full_name": f"me/{body['name']}", "clone_url": f"https://github.com/me/{body['name']}.git", "private": True},
        )

    info = _provider(handler).create_repo("sage-foo", description="Sage app: Foo")
    assert info.full_name == "me/sage-foo"
    assert info.clone_url == "https://github.com/me/sage-foo.git"
    assert info.private is True
    assert seen["url"] == "https://api.github.com/user/repos"
    assert seen["auth"] == "Bearer tok"
    assert seen["version"] == "2022-11-28"
    assert seen["body"] == {"name": "sage-foo", "private": True, "auto_init": False, "description": "Sage app: Foo"}


def test_create_repo_422_is_conflict():
    handler = lambda req: httpx.Response(422, json={"message": "name already exists"})
    with pytest.raises(RepoNameConflict):
        _provider(handler).create_repo("sage-foo")


def test_create_repo_other_error():
    handler = lambda req: httpx.Response(500, text="boom")
    with pytest.raises(RepoProviderError) as e:
        _provider(handler).create_repo("sage-foo")
    assert e.value.status == 500
