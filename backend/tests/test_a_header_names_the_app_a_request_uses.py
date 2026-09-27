"""A request names its Built App. The selected app stays the default for a request that does not.

WHAT THIS GUARDS. Two Build tabs share one process. The app a tab means is the one in its URL,
sent as `X-Sage-App`. A click in the tab on app A must read and write `apps/A` while the process's
selected app is still B, and it must not stop B's preview or move the selection. A request with no
header is the CLI, a test, or a caller that has not learned the header, and it still means B.
A header that names no app is a 404, not a silent fall-through onto B.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import sage.orchestrator.app as appmod
from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_LLM_ALIAS
from sage.resources.provider import FakeResourceProvider, LlmAlias
from sage.router.models import ModelCatalog

_ALIAS = LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {})


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder\n")
    (t / "package.json").write_text("{}\n")
    return t


def _binding_ids(root: Path, app_id: str) -> list[str]:
    path = root / "apps" / app_id / ".sage" / "bindings.json"
    if not path.is_file():
        return []
    return [row["id"] for row in json.loads(path.read_text())]


def _two_apps(tmp_path: Path, monkeypatch):
    """App A exists. App B is the selected one, because minting selects it."""
    root = tmp_path / "mnt" / "code"
    orch = Orchestrator(
        workspace_dir=root,
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider([_ALIAS]),
    )
    app_a = orch.project(start_preview=False).workspace.app_id
    app_b = orch.create_app()["id"]
    monkeypatch.setattr(appmod, "orchestrator", orch)
    return orch, root, app_a, app_b, TestClient(appmod.control_app)


def test_a_bindings_write_with_the_header_lands_on_that_app_and_leaves_the_selection(
        tmp_path: Path, monkeypatch):
    """Selected app is B. The header names A. The write lands in A's manifest, B's file is
    untouched, and the selection is still B. The preview B was holding is the same object."""
    orch, root, app_a, app_b, client = _two_apps(tmp_path, monkeypatch)
    held = orch.project(start_preview=False).supervisor

    wrote = client.post(
        "/api/bindings",
        headers={"X-Sage-App": app_a},
        json={"kind": KIND_LLM_ALIAS, "id": "id-sonnet"},
    )
    assert wrote.status_code == 200, wrote.text
    assert _binding_ids(root, app_a) == ["id-sonnet"]
    assert _binding_ids(root, app_b) == []
    assert orch._wm.selected_app_id() == app_b
    assert orch.project(start_preview=False).supervisor is held

    read = client.get("/api/bindings", headers={"X-Sage-App": app_a})
    assert read.status_code == 200, read.text
    assert [row["id"] for row in read.json()["bindings"]] == ["id-sonnet"]


def test_a_bindings_call_with_no_header_uses_the_selected_app(tmp_path: Path, monkeypatch):
    """The same calls, without the header, still mean the app the process has selected."""
    orch, root, app_a, app_b, client = _two_apps(tmp_path, monkeypatch)

    wrote = client.post(
        "/api/bindings",
        json={"kind": KIND_LLM_ALIAS, "id": "id-sonnet"},
    )
    assert wrote.status_code == 200, wrote.text
    assert _binding_ids(root, app_b) == ["id-sonnet"]
    assert _binding_ids(root, app_a) == []
    assert [row["id"] for row in client.get("/api/bindings").json()["bindings"]] == ["id-sonnet"]
    assert orch._wm.selected_app_id() == app_b


def test_a_header_for_an_unknown_app_is_not_found(tmp_path: Path, monkeypatch):
    """A header that names nothing does not fall through onto the selected app."""
    _orch, root, _app_a, app_b, client = _two_apps(tmp_path, monkeypatch)
    missing = "app_" + "0" * 21

    wrote = client.post(
        "/api/bindings",
        headers={"X-Sage-App": missing},
        json={"kind": KIND_LLM_ALIAS, "id": "id-sonnet"},
    )
    assert wrote.status_code == 404, wrote.text
    assert _binding_ids(root, app_b) == []

    read = client.get("/api/bindings", headers={"X-Sage-App": missing})
    assert read.status_code == 404, read.text
