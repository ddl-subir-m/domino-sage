"""A Built App records what kind of app it is at birth, and nothing works it out from the files (#490).

Sage seeds from a template, and there is one: `fastapi-antd`, since the one-app pivot retired
`react-vite`. Every constant the backend keyed on a template's shape — which file says "an app is
here", which files publish refreshes, what the helpers are called — is a `Stack` the app in hand
answers with. Three things have to hold for that to be safe over a fleet of apps that never re-seed
(#40):

  - the record is written at the moment the app is born, beside `createdAt`, and never moves;
  - an app with NO record is `react-vite` — every app born before the record existed, or before
    this pivot, is one — and its `Stack` object answers as `fastapi-antd` rather than `None`
    (`workspace/stack.py`'s module docstring says why that's safe for a best-effort reader and not
    for a Build turn about to act, which is refused separately — see
    `test_a_build_turn_refuses_a_stack_it_no_longer_carries.py`'s stack-refusal tests, not this
    file; that pointer used to name `test_orchestrator.py`, where no such tests actually existed —
    found stale 2026-09-23);
  - the record is what answers, not the disk: an agent can delete the seed sentinel (`ensure` says
    so), and a stack guessed from what is left would re-seed the wrong template over a real app.

Each test here plants one of the conditions the seam has to hold against, so a green run says the
guard is armed and not merely that the constant moved.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.workspace import stack as stackmod
from sage.workspace.manager import _DEPLOY_FILES, _OWNED_SOURCES, WorkspaceManager
from sage.workspace.stack import FASTAPI_ANTD, LEGACY_STACK, Stack, read_stack_name


def _fake_template(tmp: Path, name: str = "template") -> Path:
    t = tmp / name
    (t / "static").mkdir(parents=True)
    (t / "static" / "app.js").write_text("placeholder")
    (t / "app.py").write_text("# app\n")
    return t


def _settings(ws) -> dict:
    return json.loads((ws.path / ".sage" / "settings.json").read_text())


def test_birth_writes_the_stack_beside_created_at(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))

    ws = mgr.ensure("proj1")

    settings = _settings(ws)
    assert settings["stack"] == "fastapi-antd"
    assert settings["createdAt"], "the stack is recorded at the same moment the app is born"
    assert ws.stack_name == "fastapi-antd"
    assert mgr.stack.name == "fastapi-antd"


def test_an_app_with_no_record_reads_as_react_vite_but_answers_as_fastapi_antd(tmp_path: Path):
    """Every app on a volume from before this pivot was born before the record existed, or was a
    `react-vite` app the record names explicitly — both read the same way. It has to open, seed
    nothing, and hand a best-effort reader a workable `Stack` rather than crash or guess."""
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    ws = mgr.ensure("proj1")
    settings = _settings(ws)
    del settings["stack"]
    (ws.path / ".sage" / "settings.json").write_text(json.dumps(settings))
    (ws.path / "static" / "app.js").write_text("the person's app")

    again = mgr.ensure("proj1")

    assert again.stack_name == LEGACY_STACK
    # The Stack OBJECT is never react-vite's (deleted) — a best-effort reader gets fastapi-antd's
    # shape instead, which is safe because nothing here ACTS on the app (see the module docstring).
    assert mgr.stack.name == FASTAPI_ANTD.name
    assert (again.path / "static" / "app.js").read_text() == "the person's app", "not re-seeded"
    assert "stack" not in _settings(again), "an old app is not rewritten just for being opened"


def test_the_record_answers_and_the_files_do_not(tmp_path: Path):
    """The sentinel is a file the agent can delete. Deleting it re-seeds the SAME stack — the one
    the record names — and the record itself does not move."""
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    ws = mgr.ensure("proj1")
    (ws.path / "app.py").unlink()
    (ws.path / "static" / "app.js").write_text("the person's app")

    again = mgr.ensure("proj1")

    assert (again.path / "app.py").exists(), "the sentinel is restored from the template"
    assert (again.path / "static" / "app.js").read_text() == "the person's app", "entry by entry"
    assert _settings(again)["stack"] == "fastapi-antd"


def test_an_unreadable_record_reads_as_react_vite(tmp_path: Path):
    """A stray comma in the settings file must not decide that an app on the disk cannot open."""
    app = tmp_path / "app"
    (app / ".sage").mkdir(parents=True)
    (app / ".sage" / "settings.json").write_text("{not json")
    assert read_stack_name(app) == LEGACY_STACK
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": "   "}))
    assert read_stack_name(app) == LEGACY_STACK
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": 7}))
    assert read_stack_name(app) == LEGACY_STACK


def test_a_name_sage_cannot_seed_is_refused_before_anything_is_minted(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    mgr.ensure("proj1")
    before = mgr.selected_app_id()

    with pytest.raises(ValueError, match="not a kind of app"):
        mgr.create_app("proj1", stack="cobol-cgi")

    assert mgr.selected_app_id() == before, "no selection points at a directory never made"
    assert sorted(p.name for p in mgr.apps_dir.iterdir()) == [before]


def _other_stack(tmp_path: Path, monkeypatch) -> Stack:
    """A second kind of app, registered for this test only, and deliberately shaped nothing like
    fastapi-antd's (a distinct sentinel, no `static/`) so a test can tell which template an app
    seeded from by which files exist. ONE registry — the module's — because `Workspace` (a value
    object built in many places) and the manager both answer off it, and a stack known to one and
    not the other is exactly the fault the first run of this file found."""
    other_tmpl = tmp_path / "other"
    other_tmpl.mkdir()
    (other_tmpl / "manifest.json").write_text("{}")
    (other_tmpl / "widget.js").write_text("// widget")
    other = Stack(
        name="other", template_dir=other_tmpl, sentinel="manifest.json",
        deploy_files=("manifest.json",), owned_sources=(), helpers=FASTAPI_ANTD.helpers,
        preview_config=None, server_script=None, entry_file="widget.js",
        source_globs=("*.js",), query_globs=("*.js",),
    )
    monkeypatch.setitem(stackmod.STACKS, other.name, other)
    return other


def test_a_second_stack_seeds_from_its_own_template_and_records_its_name(tmp_path: Path, monkeypatch):
    """The seam with two stacks in it: a new app of the other kind comes from the other template,
    records the other name, and the fastapi-antd app beside it is untouched."""
    other = _other_stack(tmp_path, monkeypatch)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    first = mgr.ensure("proj1")

    second = mgr.create_app("proj1", stack="other")

    assert second.stack_name == "other"
    assert (second.path / "manifest.json").exists() and not (second.path / "app.py").exists()
    assert second.app_entry.name == "widget.js", "the value object answers off the same registry"
    assert mgr.stack == other
    assert mgr.stack_for(first.app_id).name == "fastapi-antd"
    assert (first.path / "app.py").exists() and not (first.path / "manifest.json").exists()
    # The template the orchestrator asks about is the selected app's, not a process-wide one.
    assert mgr.template == other.template_dir


def test_the_deployment_default_names_a_new_app_and_a_typo_falls_back(tmp_path: Path, monkeypatch, caplog):
    _other_stack(tmp_path, monkeypatch)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))

    monkeypatch.setenv("SAGE_DEFAULT_STACK", "other")
    assert mgr.ensure("proj1").stack_name == "other"

    monkeypatch.setenv("SAGE_DEFAULT_STACK", "othre")
    born = mgr.create_app("proj1")
    assert born.stack_name == FASTAPI_ANTD.name
    assert "SAGE_DEFAULT_STACK='othre' names no stack" in caplog.text


def test_the_fastapi_antd_stack_is_the_shape_the_backend_always_had():
    """The constants the tests read the ORDER off are the fastapi-antd stack's fields, unchanged."""
    assert _DEPLOY_FILES is FASTAPI_ANTD.deploy_files
    assert _OWNED_SOURCES is FASTAPI_ANTD.owned_sources
    assert FASTAPI_ANTD.sentinel == "app.py"
    assert FASTAPI_ANTD.helpers.query_path == "static/sage/appQuery.js"
    assert FASTAPI_ANTD.helpers.llm_config_path == "static/sage/appLlm.config.js"
    assert FASTAPI_ANTD.preview_config is None
    assert FASTAPI_ANTD.server_script is None
    assert FASTAPI_ANTD.entry_file == "static/app.js"
    assert stackmod.default_stack_name() in stackmod.STACKS


def test_the_template_override_reaches_only_the_configured_manager(tmp_path: Path):
    """`SAGE_TEMPLATE` overrides the directory THIS manager seeds fastapi-antd apps from; the
    module constant is not rewritten under a caller that names a different one."""
    tmpl = _fake_template(tmp_path)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl)
    assert mgr.stack_for("nobody").template_dir == tmpl
    assert FASTAPI_ANTD.template_dir != tmpl, "the module constant is not rewritten under a caller"
