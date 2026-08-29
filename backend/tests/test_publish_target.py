"""Publish targets the Built App, not the Project's first app (#70, ADR-0008).

A Domino project holds one App per Built App now, so asking it for "the app" cannot answer. The
lookup returned the first of them — right while a project had one, an arbitrary choice once it has
several — so publishing the second Built App shipped its code as a new version of the first, the
app people had already been given a link to.

Each Built App records the Domino App its first publish created and re-publishes that one. The id
is what keeps the URL stable, and it is written in the app's own settings for the same reason the
app list is a directory scan: one writer per app.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator, TurnBusy
from sage.provision.domino import ControlPlane, DominoControlPlane, FakeControlPlane
from sage.resources.publish_guard import PublishRefused
from sage.router.models import ModelCatalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")  # entry script, no serve.py
    return t


def _orch(tmp: Path, cp: FakeControlPlane) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),  # never called: nothing here builds
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        control_plane=cp,
        domino_project_id="proj-1",
        domino_project_name="Sales dashboard",
    )
    orch.project(start_preview=False)  # attach + seed the first app without starting Vite
    return orch


def _new_app(orch: Orchestrator) -> str:
    """Mint a second Built App and put Build in front of it. #74 gives this a rail button; until
    then a confirmed handoff is the only caller, and this is the same two steps it takes."""
    app_id = orch._wm.create_app("Sage").app_id
    orch.select_app(app_id)
    return app_id


def test_publishing_records_the_domino_app_against_the_built_app(tmp_path: Path):
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    app = orch.project(start_preview=False).workspace

    out = orch.publish()

    assert out["published"] is True and out["republished"] is False
    assert app.domino_app_id() == out["app_id"]
    # The App is created to run THIS app's entry script, one directory down.
    assert cp.app_entry_points[out["app_id"]] == f"apps/{app.app_id}/app.sh"


def test_republishing_ships_a_new_version_of_the_recorded_app(tmp_path: Path):
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    first = orch.publish()

    again = orch.publish()

    assert again["republished"] is True
    assert again["app_id"] == first["app_id"]
    assert again["url"] == first["url"]              # the link already shared keeps working
    assert list(cp.published) == [first["app_id"]]   # and no second App was created


def test_a_second_built_app_publishes_to_its_own_domino_app(tmp_path: Path):
    """The failure this ticket closes: publishing app B deployed B's code over app A."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    first_app = orch.project(start_preview=False).workspace
    first = orch.publish()

    second_app_id = _new_app(orch)
    second = orch.publish()

    assert second["republished"] is False
    assert second["app_id"] != first["app_id"]
    assert second["url"] != first["url"]
    assert cp.app_entry_points[second["app_id"]] == f"apps/{second_app_id}/app.sh"
    # The first app is untouched, and still re-publishes to its own deployment.
    assert first_app.domino_app_id() == first["app_id"]
    orch.select_app(first_app.app_id)
    assert orch.publish() == {**first, "republished": True}


def test_each_domino_app_is_named_for_the_built_app_it_deploys(tmp_path: Path):
    """Two Apps in one project both named after the project are two identical cards in the Gallery
    and two identical rows in Domino. The Built App's name is the one that tells them apart; the
    project's is the fallback for an app nobody has named."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    first = orch.publish()

    second_app_id = _new_app(orch)
    orch.rename_app(second_app_id, "Desk exposure")
    second = orch.publish()

    assert cp.app_names[first["app_id"]] == "Sales dashboard"
    assert cp.app_names[second["app_id"]] == "Desk exposure"


def test_the_manage_link_still_names_the_domino_project(tmp_path: Path):
    # The deep link is /u/{owner}/{project}/apps/… — the project's name, not the app's, even now
    # that the App carries its own.
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    orch.rename_app(orch.project(start_preview=False).workspace.app_id, "Desk exposure")

    out = orch.publish()

    assert out["manage_url"].startswith("/u/owner/Sales dashboard/apps/")


def test_a_publish_is_refused_while_a_turn_holds_the_working_tree(tmp_path: Path):
    """Publishing ships the code on disk, and a turn streaming into that disk has not finished
    writing it. `_save_to_git` commits the PROJECT ROOT — one repo holds every Built App — then
    pulls and may run an agent turn over the merge, which is the exact collision `_turn_lock`
    exists to prevent (#39). Project-wide, so a build in ANOTHER app is equally in the way: the
    commit takes that app's half-written tree with it.

    Non-blocking, like Delete's: there is nothing to wait out, and a Publish that sat silently
    until a long build finished would look like a control that did nothing (#89).
    """
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)

    assert orch._turn_lock.acquire(blocking=False)      # simulate a turn in flight
    try:
        with pytest.raises(TurnBusy):
            orch.publish()
    finally:
        orch._turn_lock.release()

    # Refused before anything was created, recorded or stamped.
    assert list(cp.published) == []
    assert orch.project(start_preview=False).workspace.domino_app_id() == ""
    # And the lock is handed back, so the publish that follows the build works.
    assert orch.publish()["published"] is True


def test_a_publish_hands_the_turn_lock_back_when_it_fails(tmp_path: Path):
    """A refusal that kept the lock would wedge every turn after it — the builder would answer
    "a build is already running" forever, with no build behind it."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    orch.publish()
    cp.published.clear()                                # the App was deleted in Domino (#80)

    with pytest.raises(PublishRefused):
        orch.publish()

    assert orch._turn_lock.acquire(blocking=False)
    orch._turn_lock.release()


@pytest.mark.parametrize("cls", [ControlPlane, DominoControlPlane, FakeControlPlane])
def test_the_project_wide_app_lookup_is_gone(cls):
    """`find_project_app` answered "the first App in this project". Nothing may ask that question
    again, so nothing offers it — including the Protocol every caller types against."""
    assert not hasattr(cls, "find_project_app")
