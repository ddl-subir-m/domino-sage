import pytest

from sage.provision.domino import FakeControlPlane
from sage.provision.github import FakeRepoProvider
from sage.provision.service import HubService


@pytest.fixture
def no_network_seed():
    """A recording no-op seeder so create_app runs without touching git or the network."""
    calls = []
    yield calls


def _hub(tmp_path, cp=None, repo=None, seed_calls=None):
    seed = (lambda url, tmpl, **kw: seed_calls.append((url, kw))) if seed_calls is not None else (lambda *a, **k: None)
    return HubService(cp or FakeControlPlane(), repo or FakeRepoProvider(), tmp_path, seed=seed)


def test_create_app_provisions_repo_project_workspace(tmp_path, no_network_seed):
    cp, repo = FakeControlPlane(), FakeRepoProvider()
    hub = _hub(tmp_path, cp, repo, seed_calls=no_network_seed)

    created = hub.create_app("My App")

    assert created.repo.full_name == "test-owner/sage-my-app"
    assert created.repo.private is True
    assert created.project.name == "My App"
    assert created.project.git_url == created.repo.clone_url
    assert created.workspace["id"] == f"ws-{created.project.id}"
    # seed was invoked with the new repo's clone URL
    assert no_network_seed and no_network_seed[0][0] == created.repo.clone_url


def test_create_app_resolves_repo_name_collision(tmp_path, no_network_seed):
    repo = FakeRepoProvider()
    repo.create_repo("sage-my-app")  # occupy the base name
    hub = _hub(tmp_path, FakeControlPlane(), repo, seed_calls=no_network_seed)

    created = hub.create_app("My App")
    assert created.repo.full_name == "test-owner/sage-my-app-2"


def test_create_app_requires_name(tmp_path, no_network_seed):
    with pytest.raises(ValueError):
        _hub(tmp_path).create_app("   ")


def test_open_app_reuses_running_workspace(tmp_path):
    cp = FakeControlPlane()
    cp.workspaces["proj-1"] = [{"id": "ws-existing", "state": "running"}]
    hub = _hub(tmp_path, cp)

    result = hub.open_app("proj-1")
    assert result["launched"] is False
    assert result["workspace"]["id"] == "ws-existing"


def test_open_app_launches_when_none(tmp_path):
    hub = _hub(tmp_path, FakeControlPlane())
    result = hub.open_app("proj-9")
    assert result["launched"] is True
    assert result["workspace"]["id"] == "ws-proj-9"
