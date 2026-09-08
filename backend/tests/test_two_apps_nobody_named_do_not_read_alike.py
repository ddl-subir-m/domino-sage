"""A Built App's name is written, never scraped from a sentence somebody typed (#216).

#211 named an unnamed app after the first prompt typed into it, so that `+ New app` pressed twice
gave two rail rows that could be told apart. It bought that at a price nobody agreed to: publish
sends this same string to Domino as the deployed App's name, so a sentence typed in a hurry could
become the public name of a deployment.

The prompt rung is gone, and so is the plan's first line. Two writers name an app — the person, and
the planner through the `# ` heading the plan shape asks for — and what tells two unnamed rows apart
is the number on the placeholder, plus the subtitle beside it (#217).

The placeholders sit outside the term vocabulary on purpose. `Draft app 2` is not a name, so it does
not spend the words CONTEXT.md reserves for names.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from sage.orchestrator.service import _app_display_name, _app_written_name
from sage.workspace.manager import WorkspaceManager

# What the live planner wrote before the shape asked for a heading: a sentence, and never a name.
SENTENCE = ("- This app will be an AI consumption dashboard for exploring daily usage, spend, and "
            "model activity across teams and users.\n\n## Problem & outcome\n\nNo app today.\n")


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


# ---- the placeholders -----------------------------------------------------------------------


def test_an_app_nobody_has_typed_into_is_a_numbered_draft(tmp_path: Path):
    """Numbered even when it is the only one: the number is half of what tells two rows apart, and
    a row that starts unnumbered and grows a number when a sibling appears reads as a rename."""
    mgr = _mgr(tmp_path)
    assert _app_display_name(mgr.ensure("p", seed_app=True)) == "Draft app 1"


def test_drafts_are_numbered_in_birth_order(tmp_path: Path):
    """The number is a position in `sibling_app_ids`, which is birth order for free: the id is an
    epoch-ms stamp and never changes (ADR-0008). It does shift when an older app is deleted, and
    that is accepted — the row's subtitle is the other half of telling two apart."""
    mgr = _mgr(tmp_path)
    first = mgr.ensure("p", seed_app=True).app_id
    second = mgr.create_app("p").app_id
    third = mgr.create_app("p").app_id

    assert [_app_display_name(mgr.app_workspace("p", app)) for app in (first, second, third)] == [
        "Draft app 1", "Draft app 2", "Draft app 3"]


def test_a_built_app_nobody_named_stops_being_a_draft(tmp_path: Path):
    """`Draft` is a claim about the app, not about its name: once it has been built there is
    something to open, and a row still reading `Draft` would be saying otherwise."""
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)
    assert _app_display_name(app) == "Draft app 1"

    app.mark_built()
    assert _app_display_name(app) == "Unnamed app 1"


def test_a_placeholder_never_spends_a_word_reserved_for_names(tmp_path: Path):
    """CONTEXT.md keeps `App` for the Domino thing and `Untitled` away from names, and `Built App`
    is the product's word for a real app rather than for the absence of a name."""
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)
    draft = _app_display_name(app)
    app.mark_built()

    for name in (draft, _app_display_name(app)):
        assert "Built App" not in name
        assert "Untitled" not in name
        assert "App" not in name


# ---- a prompt is a sentence, not a name -------------------------------------------------------


def test_a_prompt_does_not_name_the_app_it_was_typed_into(tmp_path: Path):
    """The reversal of #211. The Conversation still takes the prompt's text — a thread and an app
    are allowed to diverge, and only one of the two is deployed under its name."""
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)
    app.append_history({"type": "user", "text": "a daily P&L report"})

    assert _app_display_name(app) == "Draft app 1"


# ---- the planner's heading, and only the heading ----------------------------------------------


def test_a_plans_heading_names_the_app(tmp_path: Path):
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)
    app.write_plan("# Revenue by region\n\nSteps...")

    assert _app_display_name(app) == "Revenue by region"


def test_a_plan_that_opens_on_a_sentence_leaves_the_app_a_placeholder(tmp_path: Path):
    """`plan_title` cleans that sentence into a caption for the plan card, which is a fine use for
    it. An app is named or it is not."""
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)
    app.write_plan(SENTENCE)

    assert _app_display_name(app) == "Draft app 1"


def test_a_plan_that_opens_on_a_section_is_not_named_after_that_section(tmp_path: Path):
    """A plan written straight into `# Problem & outcome` has a heading and still has no name."""
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)
    app.write_plan("# Problem & outcome\n\nToday there is no app.\n")

    assert _app_display_name(app) == "Draft app 1"


def test_an_archived_plans_heading_still_names_the_app(tmp_path: Path):
    """A built app's plan is archived, and the name it gave must not vanish with the build."""
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)
    app.write_plan("# Revenue by region\n\nSteps...")
    app.archive_plan()
    app.mark_built()

    assert _app_display_name(app) == "Revenue by region"


# ---- the order of the whole ladder ------------------------------------------------------------


def test_a_name_somebody_typed_beats_the_plan_that_proposed_one(tmp_path: Path):
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)
    app.write_plan("# Revenue by region\n\nSteps...")
    app.set_display_name("Finance")

    assert _app_display_name(app) == "Finance"


def test_the_written_half_of_the_ladder_answers_empty_for_an_app_nobody_named(tmp_path: Path):
    """The seam is a function rather than a caller's argument (#218). `_app_written_name` returns
    the two written rungs and "" below them, which is how publish asks "is this app still wearing a
    placeholder" without matching `Draft app 1` against what the ladder rendered."""
    mgr = _mgr(tmp_path)
    app = mgr.ensure("p", seed_app=True)

    assert _app_written_name(app) == ""
    assert _app_display_name(app) == "Draft app 1"

    app.write_plan("# Revenue by region\n\nSteps...")
    assert _app_written_name(app) == "Revenue by region"

    app.set_display_name("Finance")
    assert _app_written_name(app) == "Finance"


def test_no_caller_can_slip_a_name_in_under_the_ladder(tmp_path: Path):
    """The `fallback` parameter is gone (#218). It was a rung nobody could see, decided by whichever
    caller happened to be asking — and publish, its one real caller, now shows the person a field
    holding the Domino project's name instead of sending it in on the way past."""
    assert "fallback" not in inspect.signature(_app_display_name).parameters
