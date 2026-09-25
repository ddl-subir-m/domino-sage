"""Stack recovery never uses a missing file as permission to replace an app (#503)."""
import hashlib
import json

import pytest

from sage.workspace.manager import WorkspaceManager
from sage.workspace.stack import FASTAPI_ANTD, REACT_VITE, preview_stack_of, stack_of


def _files(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def _manager(tmp_path):
    return WorkspaceManager(workspace_dir=tmp_path / "project", template=REACT_VITE.template_dir)


@pytest.mark.parametrize("kind", [FASTAPI_ANTD, REACT_VITE])
def test_missing_stack_record_recovers_only_metadata(tmp_path, kind):
    manager = _manager(tmp_path)
    app = manager.ensure("project", stack=kind.name)
    settings_path = app.path / ".sage/settings.json"
    settings = json.loads(settings_path.read_text())
    settings.pop("stack")
    settings["displayName"] = "Keep this name"
    settings_path.write_text(json.dumps(settings))
    before = _files(app.path)

    assert stack_of(app.path) is kind
    assert preview_stack_of(app.path) is kind
    manager.ensure("project")

    after = _files(app.path)
    assert {k: v for k, v in after.items() if k != ".sage/settings.json"} == {
        k: v for k, v in before.items() if k != ".sage/settings.json"}
    assert json.loads(settings_path.read_text()) == {**settings, "stack": kind.name}
    assert manager.stack.name == app.stack.name == kind.name


@pytest.mark.parametrize("fault", ["mixed", "incomplete", "unknown", "malformed"])
def test_unresolved_app_is_not_seeded_or_rewritten(tmp_path, fault):
    manager = _manager(tmp_path)
    app = manager.ensure("project", stack=FASTAPI_ANTD.name)
    settings = app.path / ".sage/settings.json"
    if fault == "unknown":
        settings.write_text('{"stack":"unknown","displayName":"Keep me"}')
    elif fault == "malformed":
        settings.write_text('{"stack":"fastapi-antd", broken')
    else:
        settings.unlink()
        if fault == "mixed":
            (app.path / "package.json").write_text("{}")
        else:
            (app.path / "app.py").unlink()
    before = _files(app.path)

    manager.ensure("project")

    assert _files(app.path) == before
    assert preview_stack_of(app.path) is None
    with pytest.raises(ValueError, match="stack|settings|recover"):
        stack_of(app.path)


def test_recorded_app_with_missing_entry_is_preserved(tmp_path):
    manager = _manager(tmp_path)
    app = manager.ensure("project", stack=FASTAPI_ANTD.name)
    (app.path / "app.py").unlink()
    before = _files(app.path)
    manager.ensure("project")
    assert _files(app.path) == before
    assert app.stack is FASTAPI_ANTD
    assert not (app.path / "app.py").exists()


def test_incomplete_recorded_app_does_not_spawn_a_server(tmp_path, monkeypatch):
    from sage.preview.supervisor import UvicornSupervisor

    manager = _manager(tmp_path)
    app = manager.ensure("project", stack=FASTAPI_ANTD.name)
    (app.path / "app.py").unlink()
    spawned = []
    monkeypatch.setattr(UvicornSupervisor, "_spawn", lambda self: spawned.append(True))
    with pytest.raises(RuntimeError, match="incomplete.*app.py"):
        UvicornSupervisor(app.path).start()
    assert spawned == []


def test_recorded_app_remains_authoritative_with_other_stack_files(tmp_path):
    manager = _manager(tmp_path)
    app = manager.ensure("project", stack=FASTAPI_ANTD.name)
    (app.path / "package.json").write_text('{"scripts":{"dev":"do not run"}}')
    before = _files(app.path)
    manager.ensure("project")
    assert _files(app.path) == before
    assert stack_of(app.path) is preview_stack_of(app.path) is FASTAPI_ANTD


def test_interrupted_recorded_seed_resumes_missing_files_only(tmp_path, monkeypatch):
    import sage.workspace.manager as module

    manager = _manager(tmp_path)
    original = module._seed_file
    count = 0

    def interrupted(src, dest):
        nonlocal count
        count += 1
        if count == 3:
            raise OSError("interrupted copy")
        return original(src, dest)

    monkeypatch.setattr(module, "_seed_file", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        manager.ensure("project", stack=FASTAPI_ANTD.name)
    settings_path = manager.app_path / ".sage/settings.json"
    assert json.loads(settings_path.read_text())["seedState"] == "pending"
    custom = manager.app_path / "static/app.js"
    custom.parent.mkdir(exist_ok=True)
    custom.write_text("// Keep this edit during recovery")
    monkeypatch.setattr(module, "_seed_file", original)

    app = manager.ensure("project", stack=REACT_VITE.name)

    assert custom.read_text() == "// Keep this edit during recovery"
    assert (app.path / "static/index.html").is_file()
    assert not (app.path / "package.json").exists()
    assert json.loads(settings_path.read_text())["seedState"] == "complete"


def test_reset_uses_resolved_stack_before_removing_files(tmp_path):
    manager = _manager(tmp_path)
    app = manager.ensure("project", stack=FASTAPI_ANTD.name)
    (app.path / ".sage/settings.json").unlink()
    (app.path / "static/app.js").write_text("// an old app")
    manager.reset()
    assert (app.path / "app.py").is_file()
    assert not (app.path / "package.json").exists()
    assert (app.path / "static/app.js").read_text() != "// an old app"
    assert app.stack is FASTAPI_ANTD


def test_unresolved_app_still_opens_chat_and_the_app_rail(tmp_path):
    from .test_switch_app import _orch

    orch, _oc, _root = _orch(tmp_path)
    app = orch._wm.ensure("project", stack=FASTAPI_ANTD.name)
    (app.path / ".sage/settings.json").write_text('{"stack": broken')
    before = _files(app.path)

    project = orch.project(start_preview=False, seed_app=False)
    orch._ensure_seeded()

    assert project.workspace.path == app.path
    assert orch.list_apps()[0]["stack"] is None
    assert _files(app.path) == before
