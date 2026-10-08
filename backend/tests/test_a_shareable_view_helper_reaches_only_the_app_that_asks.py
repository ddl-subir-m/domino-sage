"""The shareable-view helper reaches an existing app only when its plan asks, and only that app (#701).

WHAT THIS GUARDS. New apps get `useViewState` from their starter. An app born before it has no copy,
and the attach/select refresh must keep it that way: that refresh only REFRESHES what is there, and
it has tests against adding absent helpers. The helper is installed by one narrow step instead, on
the Build turn whose app's live plan names a shareable view — through #690's `_prepare_turn_app`
and `WorkspaceManager.for_app`, so a turn for app A while app B is selected writes only into A and
leaves B's files, selection and preview as they were.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.implementation_request import apply_instruction_profile
from sage.orchestrator.service import _PLAN_SHAPE, Orchestrator, _request_view, _request_workspace
from sage.router.models import ModelCatalog
from sage.workspace.manager import WorkspaceManager, plan_wants_shareable_view
from sage.workspace.stack import FASTAPI_ANTD, REACT_VITE

REPO = Path(__file__).resolve().parents[2]
STACKS = pytest.mark.parametrize("kind", [FASTAPI_ANTD, REACT_VITE], ids=lambda k: k.name)

PLAN_WITH_A_SHAREABLE_VIEW = """# Revenue Board

Shows revenue by month and region.

## What it does
- Charts revenue by month for the chosen region.
- Shareable view — the month and region reopen from a copied link or a reload.
"""
PLAN_WITHOUT = PLAN_WITH_A_SHAREABLE_VIEW.replace(
    "- Shareable view — the month and region reopen from a copied link or a reload.\n", "")


class _StopAfterPreparation(Exception):
    pass


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


def _template(tmp: Path) -> Path:
    """The react-vite template's shape, with the real view-state helper among its owned sources."""
    t = tmp / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder\n")
    (t / "package.json").write_text("{}\n")
    (t / "vite.config.ts").write_text("// template v2\n")
    rel = REACT_VITE.view_state
    shutil.copy2(REACT_VITE.template_dir / rel, t / rel)
    return t


def _two_apps(tmp_path: Path, monkeypatch, kind):
    """App A and app B, both born before the helper shipped. B is selected, because minting selects."""
    monkeypatch.setenv("SAGE_DEFAULT_STACK", kind.name)
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
    )
    project = orch.project(start_preview=False)
    app_a = project.workspace
    app_b = orch._view_for(project, orch.create_app()["id"]).workspace
    for app in (app_a, app_b):
        (app.path / kind.view_state).unlink()
    assert orch._wm.selected_app_id() == app_b.app_id
    return orch, project, app_a, app_b


def _build_on(orch: Orchestrator, project, app_id: str, monkeypatch, *, approve_edit=None) -> None:
    """Start a Build turn the way a `?app=` tab does, and stop it once the turn is prepared.
    `approve_edit` approves with that plan edit instead, the way the plan card's Build does."""
    def stop():
        raise _StopAfterPreparation

    monkeypatch.setattr(orch, "_ensure_opencode", stop)
    view = orch._view_for(project, app_id)
    view_token = _request_view.set(view)
    workspace_token = _request_workspace.set(view.workspace)
    try:
        with pytest.raises(_StopAfterPreparation):
            if approve_edit is None:
                orch.build("add a chart")
            else:
                list(orch.approve_stream(plan_edits=approve_edit))
    finally:
        _request_workspace.reset(workspace_token)
        _request_view.reset(view_token)


@STACKS
def test_the_helper_is_a_sage_owned_source_of_each_stack(kind):
    assert kind.view_state in kind.owned_sources
    assert (kind.template_dir / kind.view_state).is_file()


@STACKS
def test_an_existing_apps_refresh_leaves_an_absent_helper_absent(tmp_path: Path, kind):
    wm = WorkspaceManager(tmp_path / "ws", template=REACT_VITE.template_dir)
    app = wm.create_app("proj1", stack=kind.name).path
    assert (app / kind.view_state).is_file(), "a new app gets the helper from its starter"
    (app / kind.view_state).unlink()

    wm.refresh_preview_config()
    wm.ensure_llm_helper()
    wm.refresh_owned_sources()
    wm.refresh_entry_script()

    assert not (app / kind.view_state).exists()


@STACKS
def test_a_build_turn_without_a_shareable_view_installs_nothing(tmp_path: Path, monkeypatch, kind):
    orch, project, app_a, _ = _two_apps(tmp_path, monkeypatch, kind)
    app_a.write_plan(PLAN_WITHOUT)

    _build_on(orch, project, app_a.app_id, monkeypatch)

    assert not (app_a.path / kind.view_state).exists()


@STACKS
def test_a_build_for_app_a_while_b_is_selected_installs_only_into_a(tmp_path: Path, monkeypatch, kind):
    orch, project, app_a, app_b = _two_apps(tmp_path, monkeypatch, kind)
    app_a.write_plan(PLAN_WITH_A_SHAREABLE_VIEW)
    app_b.write_plan(PLAN_WITH_A_SHAREABLE_VIEW)
    a_before, b_before = _tree(app_a.path), _tree(app_b.path)
    preview_b = project._selected_view.supervisor

    _build_on(orch, project, app_a.app_id, monkeypatch)

    template_copy = (orch._wm.for_app(app_a.app_id).stack.template_dir / kind.view_state).read_bytes()
    a_after = _tree(app_a.path)
    assert a_after.pop(kind.view_state) == template_copy
    assert a_after == a_before, "the installer wrote nothing but the helper"
    assert _tree(app_b.path) == b_before
    assert orch._wm.selected_app_id() == app_b.app_id
    assert project._selected_view.supervisor is preview_b


@STACKS
def test_an_approve_whose_edit_names_a_shareable_view_installs_it(tmp_path: Path, monkeypatch, kind):
    """The plan card's edit is written after the turn is prepared, so it is read again."""
    orch, project, app_a, app_b = _two_apps(tmp_path, monkeypatch, kind)
    app_a.write_plan(PLAN_WITHOUT)

    _build_on(orch, project, app_a.app_id, monkeypatch, approve_edit=PLAN_WITH_A_SHAREABLE_VIEW)

    assert (app_a.path / kind.view_state).is_file()
    assert not (app_b.path / kind.view_state).exists()


@STACKS
def test_installing_twice_changes_nothing_and_never_replaces_an_existing_copy(tmp_path: Path,
                                                                               monkeypatch, kind):
    """Idempotent. A copy already there is the refresh's to bring in line, not the installer's."""
    orch, project, app_a, _ = _two_apps(tmp_path, monkeypatch, kind)
    app_a.write_plan(PLAN_WITH_A_SHAREABLE_VIEW)
    _build_on(orch, project, app_a.app_id, monkeypatch)
    wm = orch._wm.for_app(app_a.app_id)
    first = (app_a.path / kind.view_state).stat().st_mtime_ns

    assert wm.install_view_state() is False
    assert (app_a.path / kind.view_state).stat().st_mtime_ns == first
    (app_a.path / kind.view_state).write_text("// an older copy\n")
    assert wm.install_view_state() is False
    assert (app_a.path / kind.view_state).read_text() == "// an older copy\n"


def test_the_words_the_plan_is_asked_for_are_the_installers_opt_in():
    """The plan shape and the installer are two ends of one convention; neither may drift alone."""
    asked = re.search(r"one bullet begins '([^']+)'", _PLAN_SHAPE).group(1)
    assert plan_wants_shareable_view(f"## What it does\n- {asked} the month reopens from a link.\n")
    assert not plan_wants_shareable_view(PLAN_WITHOUT)


OLDER_AGENTS = (
    "<!-- sage:build-profile:v1:common:begin -->\ncommon rules\n"
    "<!-- sage:build-profile:v1:common:end -->\n"
    "<!-- sage:build-profile:v1:implement:begin -->\nPut the app UI in the entry file.\n"
    "<!-- sage:build-profile:v1:implement:end -->\n"
)


@STACKS
def test_new_and_older_apps_are_told_where_the_hook_is(kind):
    """New apps read it in AGENTS.md; an older app's implement request carries the supplement."""
    assert f"`{kind.view_state}`" in (kind.template_dir / "AGENTS.md").read_text()
    request = {"messages": [{"role": "system", "content": OLDER_AGENTS}]}
    sent, _ = apply_instruction_profile(request, "implement", stack=kind.name)
    assert f"`{kind.view_state}`" in sent["messages"][0]["content"]


@STACKS
def test_the_installed_helper_counts_as_sage_owned(tmp_path: Path, monkeypatch, kind):
    """So a turn that only received it is not credited with the model's progress (#680)."""
    _, _, app_a, _ = _two_apps(tmp_path, monkeypatch, kind)
    assert kind.view_state in app_a.sage_owned_paths
