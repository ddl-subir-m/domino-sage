"""A Built App records what kind of app it is at birth, and nothing works it out from the files (#490).

Sage seeds from a template, and for a long time there was one. Every constant the backend keyed on
that template's shape — which file says "an app is here", which files publish refreshes, what the
helpers are called — is now a `Stack` the app in hand answers with. Three things have to hold for
that to be safe over a fleet of apps that never re-seed (#40):

  - the record is written at the moment the app is born, beside `createdAt`, and never moves;
  - an app with NO record is react-vite, because every app born before the record existed is one;
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
from sage.workspace.stack import LEGACY_STACK, REACT_VITE, Stack, read_stack_name


def _fake_template(tmp: Path, name: str = "template") -> Path:
    t = tmp / name
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _settings(ws) -> dict:
    return json.loads((ws.path / ".sage" / "settings.json").read_text())


def test_birth_writes_the_stack_beside_created_at(tmp_path: Path):
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))

    ws = mgr.ensure("proj1")

    settings = _settings(ws)
    assert settings["stack"] == "react-vite"
    assert settings["createdAt"], "the stack is recorded at the same moment the app is born"
    assert ws.stack_name == "react-vite"
    assert mgr.stack.name == "react-vite"


def test_an_app_with_no_record_is_react_vite(tmp_path: Path):
    """Every app on a volume today was born before the record existed. It has to open, seed
    nothing, and answer with the one stack there was."""
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    ws = mgr.ensure("proj1")
    settings = _settings(ws)
    del settings["stack"]
    (ws.path / ".sage" / "settings.json").write_text(json.dumps(settings))
    (ws.path / "src" / "App.tsx").write_text("the person's app")

    again = mgr.ensure("proj1")

    assert again.stack_name == LEGACY_STACK
    assert mgr.stack.name == LEGACY_STACK
    assert (again.path / "src" / "App.tsx").read_text() == "the person's app", "not re-seeded"
    assert "stack" not in _settings(again), "an old app is not rewritten just for being opened"


def test_the_record_answers_and_the_files_do_not(tmp_path: Path):
    """The sentinel is a file the agent can delete. Deleting it re-seeds the SAME stack — the one
    the record names — and the record itself does not move."""
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    ws = mgr.ensure("proj1")
    (ws.path / "package.json").unlink()
    (ws.path / "src" / "App.tsx").write_text("the person's app")

    again = mgr.ensure("proj1")

    assert (again.path / "package.json").exists(), "the sentinel is restored from the template"
    assert (again.path / "src" / "App.tsx").read_text() == "the person's app", "entry by entry"
    assert _settings(again)["stack"] == "react-vite"


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
    """A second kind of app, registered for this test only. ONE registry — the module's — because
    `Workspace` (a value object built in many places) and the manager both answer off it, and a
    stack known to one and not the other is exactly the fault the first run of this file found."""
    other_tmpl = tmp_path / "other"
    other_tmpl.mkdir()
    (other_tmpl / "app.py").write_text("# app")
    (other_tmpl / "static").mkdir()
    (other_tmpl / "static" / "index.html").write_text("<div id=root>")
    other = Stack(
        name="other", template_dir=other_tmpl, sentinel="app.py", deploy_files=("app.py", "app.sh"),
        owned_sources=(), helpers=REACT_VITE.helpers, preview_config=None, server_script=None,
        entry_file="static/index.html", preview="uvicorn", checker="python",
        source_globs=("*.py", "static/**/*"), query_globs=("static/**/*.js",),
    )
    monkeypatch.setitem(stackmod.STACKS, other.name, other)
    return other


def test_a_second_stack_seeds_from_its_own_template_and_records_its_name(tmp_path: Path, monkeypatch):
    """The seam with two stacks in it: a new app of the other kind comes from the other template,
    records the other name, and the react-vite app beside it is untouched."""
    other = _other_stack(tmp_path, monkeypatch)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))
    first = mgr.ensure("proj1")

    second = mgr.create_app("proj1", stack="other")

    assert second.stack_name == "other"
    assert (second.path / "app.py").exists() and not (second.path / "package.json").exists()
    assert second.app_entry.name == "index.html", "the value object answers off the same registry"
    assert mgr.stack is other
    assert mgr.stack_for(first.app_id).name == "react-vite"
    assert (first.path / "package.json").exists() and not (first.path / "app.py").exists()
    # The template the orchestrator asks about is the selected app's, not a process-wide one.
    assert mgr.template == other.template_dir


def test_the_deployment_default_names_a_new_app_and_a_typo_falls_back(tmp_path: Path, monkeypatch, caplog):
    _other_stack(tmp_path, monkeypatch)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=_fake_template(tmp_path))

    monkeypatch.setenv("SAGE_DEFAULT_STACK", "other")
    assert mgr.ensure("proj1").stack_name == "other"

    monkeypatch.setenv("SAGE_DEFAULT_STACK", "othre")
    born = mgr.create_app("proj1")
    assert born.stack_name == LEGACY_STACK
    assert "SAGE_DEFAULT_STACK='othre' names no stack" in caplog.text


def test_the_react_vite_stack_is_the_shape_the_backend_always_had():
    """The constants the tests read the ORDER off are the react-vite stack's fields, unchanged."""
    assert _DEPLOY_FILES is REACT_VITE.deploy_files
    assert _OWNED_SOURCES is REACT_VITE.owned_sources
    assert REACT_VITE.sentinel == "package.json"
    assert REACT_VITE.helpers.query_path == "src/appQuery.ts"
    assert REACT_VITE.helpers.llm_config_path == "src/appLlm.config.ts"
    assert REACT_VITE.preview_config == "vite.config.ts"
    assert REACT_VITE.server_script == "serve.py"
    assert REACT_VITE.entry_file == "src/App.tsx"
    assert stackmod.default_stack_name() in stackmod.STACKS


def test_the_template_override_reaches_only_the_react_vite_entry(tmp_path: Path):
    """`SAGE_TEMPLATE` has always meant the react-vite template's directory; the argument every
    caller passes still lands there and nowhere else."""
    tmpl = _fake_template(tmp_path)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl)
    assert mgr.stack_for("nobody").template_dir == tmpl
    assert REACT_VITE.template_dir != tmpl, "the module constant is not rewritten under a caller"
