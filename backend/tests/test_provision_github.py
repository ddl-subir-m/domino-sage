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



def test_repo_full_name_reads_only_urls_on_the_providers_own_host():
    """The parse the whole diagnosis hangs on, asserted directly.

    FakeRepoProvider answers False for any name it does not hold, so a door test cannot tell a
    correct `owner/name` from a wrong one — only this can.
    """
    from sage.provision.github import repo_full_name

    assert repo_full_name("https://github.com/o/n.git", host="github.com") == "o/n"
    assert repo_full_name("https://github.com/o/n", host="github.com") == "o/n"
    assert repo_full_name("https://GitHub.com/o/n.git", host="github.com") == "o/n"
    # Another host's repo: a 404 from OUR provider would mean "not here", never "deleted".
    assert repo_full_name("https://gitlab.com/o/n.git", host="github.com") is None
    assert repo_full_name("git@github.com:o/n.git", host="github.com") is None  # scp-style, no host
    # Not an addressable repo path.
    assert repo_full_name("https://github.com/o/n/extra.git", host="github.com") is None
    assert repo_full_name("https://github.com/o", host="github.com") is None
    assert repo_full_name("", host="github.com") is None
    assert repo_full_name(None, host="github.com") is None


@pytest.mark.parametrize(("status", "expected"), [
    (200, True),
    (404, False),   # deleted, OR private and invisible to this token — unreachable either way
    (301, False),   # renamed/transferred: not at the URI Domino stored and just failed to clone
    (302, False),
    (401, None),    # a token problem says nothing about the repo
    (403, None),    # rate limit / SSO wall
    (500, None),
])
def test_repo_exists_maps_each_status_to_one_of_three_answers(status, expected):
    """The mapping the door's whole diagnosis rests on, asserted against the real adapter.

    FakeRepoProvider reimplements the answer, so no door test can reach this. `None` is the one
    that must never collapse into `False`: it is the difference between "your repo is gone" and
    "we could not check", and only one of those is safe to act on.
    """
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(status)

    assert _provider(handler).repo_exists("me/sage-foo") is expected
    assert seen["method"] == "GET"
    assert seen["url"] == "https://api.github.com/repos/me/sage-foo"


def test_repo_exists_never_raises_when_the_provider_cannot_be_reached():
    """It runs on an already-failed door open. Raising here would replace the real error."""
    def handler(request):
        raise httpx.ConnectError("no route to host")

    assert _provider(handler).repo_exists("me/sage-foo") is None
