"""#211 — `+ New App`, pressed twice, used to give two rail rows both reading `Unnamed Built App`.

Nothing on screen told them apart, and every sentence that quotes an app's name back said the same
words about either one. The ladder in `_app_display_name` had two rungs before its placeholder — a
name somebody typed, then the plan title — and an app made by the button has neither.

These pin the two rungs added between them, and the order of the whole ladder. The order is the
part worth guarding: each rung says more about the app than the one below it, and `fallback` is the
seam between names that describe THIS app and placeholders that only stop a row being blank.
"""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator.service import _app_display_name
from sage.workspace.manager import WorkspaceManager


def _fake_template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "node_modules").mkdir()
    (t / "node_modules" / ".bin").mkdir()
    (t / "node_modules" / ".bin" / "vite").write_text("#!/bin/sh")
    return t


def _mgr(tmp: Path) -> WorkspaceManager:
    return WorkspaceManager(workspace_dir=tmp / "ws", template=_fake_template(tmp))


# ---- the rung that numbers ----------------------------------------------------------------------


def test_the_only_app_in_a_project_is_not_numbered(tmp_path: Path):
    """One app is not ambiguous with anything, and the sentences that quote this name read better
    without a number in them: "Use in Unnamed Built App" over "Use in Built App 1"."""
    mgr = _mgr(tmp_path)
    only = mgr.ensure("p", seed_app=True)

    assert _app_display_name(only) == "Unnamed Built App"


def test_apps_nobody_named_are_numbered_in_birth_order(tmp_path: Path):
    """The whole report: the button pressed three times. The number comes from position among the
    app directories, which sort by age because `new_id` leads with epoch-ms."""
    mgr = _mgr(tmp_path)
    first = mgr.ensure("p", seed_app=True).app_id
    second = mgr.create_app("p").app_id
    third = mgr.create_app("p").app_id

    names = [_app_display_name(mgr.app_workspace("p", app)) for app in (first, second, third)]
    assert names == ["Built App 1", "Built App 2", "Built App 3"]


def test_the_number_appears_the_moment_a_second_app_does(tmp_path: Path):
    """The name is derived rather than written at birth (ADR-0008), so the first app gains a number
    when it stops being the only one. That is the point: a number nobody can compare is noise."""
    mgr = _mgr(tmp_path)
    first = mgr.ensure("p", seed_app=True).app_id
    assert _app_display_name(mgr.app_workspace("p", first)) == "Unnamed Built App"

    mgr.create_app("p")

    assert _app_display_name(mgr.app_workspace("p", first)) == "Built App 1"


# ---- the rung that reads the first request ------------------------------------------------------


def test_the_first_request_names_the_app_it_was_typed_into(tmp_path: Path):
    mgr = _mgr(tmp_path)
    mgr.ensure("p", seed_app=True)
    second = mgr.create_app("p")
    second.append_history({"type": "user", "text": "a daily P&L report"})

    assert _app_display_name(second) == "a daily P&L report"


def test_a_later_request_does_not_rename_the_app(tmp_path: Path):
    """First, not last: the name would otherwise move under the person every turn."""
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)
    app.append_history({"type": "user", "text": "a daily P&L report"})
    app.append_history({"type": "done", "ok": True})
    app.append_history({"type": "user", "text": "now add a chart"})

    assert app.first_prompt() == "a daily P&L report"
    assert _app_display_name(app) == "a daily P&L report"


def test_a_log_with_no_request_in_it_names_nothing(tmp_path: Path):
    """An app whose log holds only machine rows is as nameless as one with no log at all — it falls
    through to the rungs below rather than borrowing a row nobody typed."""
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)
    assert app.first_prompt() == ""

    app.append_history({"type": "app-reset"})
    assert app.first_prompt() == ""
    assert _app_display_name(app) == "Unnamed Built App"


def test_a_request_of_only_whitespace_never_becomes_untitled(tmp_path: Path):
    """`title_from_prompt` answers "Untitled" for an empty prompt, and CONTEXT.md keeps that word
    away from names. The guard is what stops it reaching a rail row."""
    mgr = _mgr(tmp_path)
    mgr.ensure("p", seed_app=True)
    second = mgr.create_app("p")
    second.append_history({"type": "user", "text": "   \n  "})

    assert _app_display_name(second) == "Built App 2"


def test_a_long_request_is_shortened_the_way_a_thread_title_is(tmp_path: Path):
    """Through `title_from_prompt`, which Threads already name themselves with — one rule for how a
    request becomes a short name, not two."""
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)
    app.append_history({"type": "user", "text": "build me " + "a very wide dashboard " * 8})

    name = _app_display_name(app)
    assert len(name) == 60 and name.endswith("…")


# ---- the order of the whole ladder --------------------------------------------------------------


def test_a_plan_title_beats_the_request_that_asked_for_it(tmp_path: Path):
    """A plan is a considered summary of the request, so it is the better name once one exists."""
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)
    app.append_history({"type": "user", "text": "a daily P&L report"})
    app.write_plan("# Revenue by region\n\nSteps...")

    assert _app_display_name(app) == "Revenue by region"


def test_a_name_somebody_typed_beats_everything_below_it(tmp_path: Path):
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)
    app.append_history({"type": "user", "text": "a daily P&L report"})
    app.write_plan("# Revenue by region\n\nSteps...")
    app.set_display_name("Finance")

    assert _app_display_name(app) == "Finance"


def test_the_request_beats_a_callers_fallback_and_the_number_does_not(tmp_path: Path):
    """`fallback` is the seam. Publish passes the Domino project's name: a name derived from this
    app wins over it, a placeholder does not — so Publish still names an untouched app after the
    Project rather than after its position in a rail nobody deploying can see."""
    mgr = _mgr(tmp_path)
    mgr.ensure("p", seed_app=True)
    second = mgr.create_app("p")

    assert _app_display_name(second, "domino-quickstart") == "domino-quickstart"

    second.append_history({"type": "user", "text": "a daily P&L report"})
    assert _app_display_name(second, "domino-quickstart") == "a daily P&L report"
