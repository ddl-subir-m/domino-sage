from __future__ import annotations

from pathlib import Path

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import FakeResourceProvider
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Sage app\n")
    (t / ".gitignore").write_text("node_modules\n")
    return t


def _orch(tmp_path: Path) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider([], []),
    )


def test_a_stray_thread_directory_name_does_not_stop_list(tmp_path: Path):
    store = ThreadStore(tmp_path)
    keep = store.create("A conversation")
    (tmp_path / ".sage" / "threads" / "thr_x copy").mkdir(parents=True)

    rows = store.list()

    assert [r["id"] for r in rows] == [keep["id"]]


def test_an_idless_meta_record_does_not_reach_the_rail_payload(tmp_path: Path):
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    store = ThreadStore(project.record.path)
    keep = store.create("A conversation")
    idless = project.record.path / ".sage" / "threads" / "thr_idless"
    idless.mkdir(parents=True)
    (idless / "meta.json").write_text("{}")

    rows = orch.list_threads()

    assert [(r["id"], r["title"]) for r in rows] == [
        (keep["id"], "A conversation"),
        ("thr_idless", "New conversation"),
    ]


def test_an_idless_meta_record_can_still_be_deleted(tmp_path: Path):
    store = ThreadStore(tmp_path)
    thread_id = "thr_idless"
    thread = tmp_path / ".sage" / "threads" / thread_id
    thread.mkdir(parents=True)
    (thread / "meta.json").write_text("{}")
    examples = store.examples_dir(thread_id)
    examples.mkdir(parents=True)
    (examples / "chart.png").write_bytes(b"png")

    assert store.orphaned_artifact_ids() == []
    assert store.delete(thread_id) is True
    assert store.get(thread_id) is None
    assert not examples.exists()


def test_app_tag_sweeps_skip_stray_thread_directory_names(tmp_path: Path):
    store = ThreadStore(tmp_path)
    thread = store.create("A conversation")
    store.record_touch(thread["id"], app_id="app_a", app_name="Old name", kind="built")
    (tmp_path / ".sage" / "threads" / "thr_x copy").mkdir(parents=True)

    store.rename_app("app_a", "New name")
    assert store.get(thread["id"])["touched"][0]["appName"] == "New name"


def test_app_tag_delete_sweep_skips_stray_thread_directory_names(tmp_path: Path):
    store = ThreadStore(tmp_path)
    thread = store.create("A conversation")
    store.record_touch(thread["id"], app_id="app_a", app_name="Old name", kind="built")
    (tmp_path / ".sage" / "threads" / "thr_x copy").mkdir(parents=True)

    store.forget_app("app_a")
    assert store.get(thread["id"])["touched"] == []


def test_legacy_index_adoption_skips_stray_thread_directory_names(tmp_path: Path):
    (tmp_path / ".sage" / "threads" / "thr_keep").mkdir(parents=True)
    (tmp_path / ".sage" / "threads" / "thr_x copy").mkdir(parents=True)
    (tmp_path / ".sage" / "threads.json").write_text(
        '[{"id": "thr_keep", "title": "A conversation", "updatedAt": "2026-09-13T10:00:00Z"}]'
    )

    rows = ThreadStore(tmp_path).list()

    assert [r["id"] for r in rows] == ["thr_keep"]
    assert not (tmp_path / ".sage" / "threads" / "thr_x copy" / "meta.json").exists()
