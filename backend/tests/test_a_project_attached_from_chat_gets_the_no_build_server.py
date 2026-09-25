"""The preview server is re-picked once the app is born (#554).

A person opens the Workbench on a fresh Project. Chat attaches FIRST, with `seed_app=False`, and
`project()` picks the preview supervisor right there — from an app directory that does not exist
yet. No record, no files, so the pick falls back to the build stack. Then Build runs,
`_ensure_seeded` gives the app birth and records it as `fastapi-antd`, and **nothing re-picks the
server**: the only re-pick is behind `_prepare_app_files()`, whose preview half returns False for
`fastapi-antd` because that stack has no preview config to refresh. The Vite supervisor cached at
attach therefore ran `npm run dev` in a Python app directory — `ENOENT ... package.json`, exit 254,
"max restarts reached".

**The suite could not see it.** `conftest.py:51` pins `SAGE_DEFAULT_STACK=react-vite`, which is
exactly the stack the blind pick falls back to, so the wrong pick was right by luck in every test
ever written. Deleting that pin is what makes this file a test rather than a restatement.
"""
from pathlib import Path

import pytest

import sage.orchestrator.service as svc
from sage.orchestrator.service import Orchestrator
from .test_switch_app import FakeQueries, FakeVite, _orch


class FakeUvicorn(FakeVite):
    """The no-build stack's server, so `type(...) is` can tell the two picks apart."""


@pytest.fixture(autouse=True)
def _fake_preview(monkeypatch):
    FakeVite.made = []
    monkeypatch.setattr(svc, "ViteSupervisor", FakeVite)
    monkeypatch.setattr(svc, "UvicornSupervisor", FakeUvicorn)
    monkeypatch.setattr(svc, "PreviewQueries", FakeQueries)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    yield
    FakeVite.made = []


def test_an_app_born_after_chat_attached_is_served_by_its_own_stacks_server(tmp_path, monkeypatch):
    monkeypatch.delenv("SAGE_DEFAULT_STACK", raising=False)   # the pin that hid this
    orch, _oc, _root = _orch(tmp_path)

    orch.project(start_preview=False, seed_app=False)          # Chat: no app exists yet
    assert type(orch._project.supervisor) is FakeVite, "the blind pick is the build stack"

    project = orch._ensure_seeded()                            # Build: the app is born here

    assert type(project.supervisor) is FakeUvicorn, (
        "the server picked before the app existed was never re-picked")
    app = project.workspace.path
    assert (app / "app.py").is_file(), "a no-build app was not seeded"
    assert not (app / "package.json").exists(), "this is the directory npm was run in"


def test_a_react_vite_project_is_not_restarted_for_nothing(tmp_path, monkeypatch):
    """The other half: where the blind pick was already right, the supervisor must not churn."""
    monkeypatch.setenv("SAGE_DEFAULT_STACK", "react-vite")
    orch, _oc, _root = _orch(tmp_path)

    orch.project(start_preview=False, seed_app=False)
    picked = orch._project.supervisor
    project = orch._ensure_seeded()

    assert type(project.supervisor) is FakeVite
    assert project.supervisor is picked, "a correct server was replaced anyway"
