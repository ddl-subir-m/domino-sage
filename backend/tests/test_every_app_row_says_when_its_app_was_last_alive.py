"""#217 — two app switcher rows used to carry no date at all, so nothing on either said which
of the two was still alive.

An app already recorded when it was built and when it was published. It did not record when it
was CREATED, so a never-built app had no time on it whatsoever — and `Not built yet` is the same
sentence about an app started this morning and one abandoned in March.

These pin the third stamp and the ladder the row reads it through: the newest true one, and only
that one. Nothing here walks `history.jsonl` — the row renders once per app per render, and the
log reaches megabytes.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace import manager
from sage.workspace.manager import WorkspaceManager

from .fake_opencode import FakeOpenCode


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def route(self, request, labels):
        yield b'data: {"choices": [{"delta": {"content": "CHAT"}}]}\n\ndata: [DONE]\n\n'


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _fake_template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _mgr(tmp: Path) -> WorkspaceManager:
    return WorkspaceManager(workspace_dir=tmp / "ws", template=_fake_template(tmp))


def _settings(ws) -> Path:
    return ws.path / ".sage" / "settings.json"


# ---- the stamp ----------------------------------------------------------------------------------


def test_an_app_records_when_it_was_created(tmp_path: Path):
    """`+ New app` mints one, and the app knows the day it was minted."""
    mgr = _mgr(tmp_path)
    born = mgr.create_app("p")

    assert born.created_at().endswith("Z")
    assert json.loads(_settings(born).read_text())["createdAt"] == born.created_at()


def test_the_first_app_of_a_project_is_stamped_too(tmp_path: Path):
    """A Project's first app is seeded by `ensure` rather than by `create_app` — the handoff is one
    door into a Built App and opening Build directly is another. Both are a birth, so the stamp
    sits where the seeding happens rather than in one of the two callers."""
    first = _mgr(tmp_path).ensure("p", seed_app=True)

    assert first.created_at() != ""


def test_returning_to_an_app_does_not_restamp_it(tmp_path: Path):
    """`ensure` runs on every build turn. The stamp says when the app was born, so only the birth
    may write it."""
    mgr = _mgr(tmp_path)
    born = mgr.create_app("p")
    first = born.created_at()

    later = mgr.ensure("p", seed_app=True)

    assert later.created_at() == first


def test_an_app_older_than_the_stamp_reads_no_creation_time(tmp_path: Path):
    """Nothing is backfilled. An app seeded before this change has no `createdAt`, and the reader
    says so rather than handing the row a date invented from a later stamp."""
    mgr = _mgr(tmp_path)
    born = mgr.create_app("p")
    settings = json.loads(_settings(born).read_text())
    del settings["createdAt"]
    _settings(born).write_text(json.dumps(settings))

    assert mgr.ensure("p", seed_app=True).created_at() == ""


def test_a_reset_app_keeps_its_birth_and_loses_its_build(tmp_path: Path):
    """Reset takes the code away and keeps `.sage/`, so the creation stamp outlives it while
    `builtAt` does not. The row falls back a rung, which is the truth: there is nothing built here
    any more.

    A reset app that had PUBLISHED keeps saying so, and that is not the same bug. `publishedAt`
    dates when the code behind the URL last moved, and a local reset does not move it — the
    deployed App is still out there, still from that day."""
    mgr = _mgr(tmp_path)
    born = mgr.create_app("p")
    born.mark_built()
    born.mark_published()
    mgr.reset()
    born.clear_built()

    assert born.created_at() != ""
    assert born.built_at() == ""
    assert born.published_at() != ""


def test_a_seed_that_died_half_way_still_left_the_app_dated(tmp_path: Path):
    """The stamp is written before the template is copied, not after. "The directory is empty" is a
    fact that only holds once, so a copy that dies part way through would otherwise leave an app
    `ensure` can never call a birth again — and so one that carries no date for the rest of its
    life, which is the state this ticket exists to remove."""
    mgr = _mgr(tmp_path)
    with mock.patch.object(manager.shutil, "copytree", side_effect=OSError("disk went away")):
        with pytest.raises(OSError):
            mgr.create_app("p")

    assert mgr.ensure("p", seed_app=True).created_at() != ""


def test_repairing_an_app_that_lost_its_package_json_does_not_redate_it(tmp_path: Path):
    """`ensure` re-seeds any app with no `package.json` — a file the build agent can delete — and
    that path must not read as a birth. An app seeded before the stamp existed would otherwise be
    dated the day it was repaired, which is a wrong date rather than no date."""
    mgr = _mgr(tmp_path)
    born = mgr.create_app("p")
    settings = json.loads(_settings(born).read_text())
    del settings["createdAt"]                     # an app from before the stamp existed
    _settings(born).write_text(json.dumps(settings))
    (born.path / "package.json").unlink()

    repaired = mgr.ensure("p", seed_app=True)

    assert (repaired.path / "package.json").exists()          # the repair happened
    assert repaired.created_at() == ""                        # and dated nothing


# ---- the row ------------------------------------------------------------------------------------


def _orch(tmp: Path) -> Orchestrator:
    root = tmp / "mnt" / "code"
    return Orchestrator(workspace_dir=root, template=_fake_template(tmp), gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(),
                        opencode_client=FakeOpenCode(root, []))


def test_a_row_carries_the_creation_stamp_beside_the_other_two(tmp_path: Path):
    """All three stamps reach the row, and the row picks the newest true one. They travel together
    because the choice is the client's: a row draws one line, and which of the three dates it is
    changes with every build and every publish."""
    orch = _orch(tmp_path)
    row = orch.create_app()

    assert row["createdAt"] != ""
    assert row["builtAt"] == ""
    assert row["publishedAt"] == ""


def test_a_row_reads_no_history_log_to_date_its_app(tmp_path: Path):
    """The whole reason there is no turn count on the row. `_app_row` runs once per app per render,
    and `history.jsonl` is append-only and reaches megabytes — so a row that dated an app by
    walking it would make the app switcher cost the transcript."""
    orch = _orch(tmp_path)
    app_id = orch.create_app()["id"]
    workspace = orch._wm.app_workspace(orch._project_id, app_id)
    history = workspace.path / ".sage" / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text("this is not JSON, and nothing that draws a row may read it\n")

    assert orch.list_apps()[0]["createdAt"] != ""


# ---- the subtitle -------------------------------------------------------------------------------
# Driven through the Build header's harness, because the rows live inside the app switcher's panel
# and the panel is what draws them. See `js/build_header_harness.mjs` for why nothing is mounted.

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _hours_ago(n: int) -> str:
    """A stamp in the format the server writes, which is what the row's comparison relies on."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - n * 3600))


def _parts(**extra) -> list[dict]:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps([{"build": "thr_many", "select": "app_a", **extra}]),
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])[-1]["parts"]


def _rows(**extra) -> list[str]:
    """What every switcher row's subtitle said, in the order the rows were drawn. One entry per row
    that drew a stamp — a row mid-build draws its word inside a span of its own instead."""
    return [t for p in _parts(**extra) if p["className"] == "sw-thread-meta" for t in p["texts"]]


@needs_node
def test_a_published_app_is_dated_by_its_publish():
    """The top rung. `app_a` has all three stamps, and the row says the newest — a re-publish is
    what last moved the code behind the URL, and that is what "still alive" means for a shipped
    app."""
    assert "Published 2 days ago" in _rows()


@needs_node
def test_an_app_nobody_has_built_is_dated_by_its_birth():
    """The rung this ticket added, and the whole report: `app_b` used to read `Not built yet` and
    nothing else, which is the same sentence about an app started this morning."""
    said = _rows()

    assert any(t.startswith("Started ") for t in said), said
    assert "Not built yet" not in said


@needs_node
def test_a_stamp_beyond_a_week_is_an_absolute_date():
    """The house rule: relative inside 7 days, `Month Day, Year` beyond it. `app_b` was started 30
    days ago, so "30 days ago" is the answer this must NOT give."""
    started = [t for t in _rows() if t.startswith("Started ")]

    assert started and "ago" not in started[0], started


@needs_node
def test_an_app_that_has_built_and_not_published_is_dated_by_its_build():
    """The middle rung, reached by taking `app_a`'s publish away. The ladder is what is under test:
    the same app, one stamp fewer, falls exactly one rung rather than to the placeholder."""
    assert "Built 6 days ago" in _rows(stamps={"app_a": {"publishedAt": ""}})


@needs_node
def test_a_row_carries_one_stamp_and_not_three():
    """A row draws one line. Three dates on it is a history nobody asked a list for, and the two
    older ones are the two that no longer say whether the app is alive.

    Read as one string per row rather than as a set of words: `app_a` and `app_d` both hold all
    three stamps, so a row that listed them would show up here as a second entry beside its own
    subtitle rather than as a missing one."""
    said = _rows()

    # Four apps, three subtitles: the fourth is mid-build and says so instead. The absolute two
    # are matched on their word rather than their date, because the fixture's stamps are counted
    # back from today and a written-out date would go stale tomorrow.
    assert len(said) == 3, said
    assert said[0] == "Published 2 days ago"
    assert said[1].startswith("Started ")
    assert said[2].startswith("Published ")


@needs_node
def test_a_running_build_still_replaces_the_stamp():
    """#77's `Building…` outranks every stamp, because what an app is doing right now is more
    useful than when it last stopped. `app_c` is mid-build and carries a build stamp underneath."""
    parts = _parts()

    assert "Building…" in [t for p in parts if p["className"] == "sw-thread-building"
                           for t in p["texts"]]
    assert "Built yesterday" not in [t for p in parts if p["texts"] for t in p["texts"]]


@needs_node
def test_an_app_older_than_the_stamps_reads_its_bare_state():
    """Nothing was backfilled, so an app seeded before any of the three stamps existed has none of
    them. It falls back to the word it always said rather than to a date invented for it."""
    said = _rows(stamps={"app_b": {"createdAt": "", "builtAt": "", "publishedAt": ""}})

    assert "Not built yet" in said


@needs_node
def test_a_rebuild_after_a_publish_is_dated_by_the_rebuild():
    """The ladder is by date, not by rung. Publish an app and then fix it, and the build is the
    later of the two — a row that answered with the publish would say `Published` about an app
    somebody rebuilt this morning, which is the older of two true stamps."""
    assert "Built 1 hour ago" in _rows(stamps={"app_a": {"builtAt": _hours_ago(1)}})


@needs_node
def test_a_publish_of_an_unchanged_build_is_dated_by_the_publish():
    """The other side of the same comparison, and the ordinary case: a re-publish ships what is
    already built, so it is the publish that last moved the code behind the URL."""
    assert "Published 1 hour ago" in _rows(stamps={"app_a": {"publishedAt": _hours_ago(1)}})


@needs_node
def test_an_app_older_than_the_stamps_says_nothing_about_being_published():
    """The fallback keeps the SMALLER vocabulary on purpose. `app_a` is published, and with every
    stamp taken away the row says `Built` rather than an undated `Published` — because a sibling
    that was published and does carry a build date says `Built` too, and one state reading as two
    words in one list is worse than one word saying less."""
    said = _rows(stamps={"app_a": {"createdAt": "", "builtAt": "", "publishedAt": ""}})

    assert said[0] == "Built"
