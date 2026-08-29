"""The advisory usage label on a Binding (#93), and the disciplines that keep it advisory.

#92's header row reports the DECLARED record faithfully, and the record can hold a Binding the app
never calls. `_resource_usage` already knows which: an LLM Alias by its name in the source, a Data
Source by the names of the queries recorded against it in `.sage/queries.json`. (Not `_data_usage`,
which scans for uses of an attached FILE — #85 named the wrong one and ADR-0010 says the next reader
will too.)

Three rules make it a label rather than a gate, and each has a test below.

  WHERE IT IS COMPUTED. At the end of a build turn, never on render. `_scan_app_sources` walks the
  whole app tree and reads every code file into memory, and the row redraws on every app switch, so
  the answer is WRITTEN and the row reads it off the disk — `publish_check`'s discipline: local,
  pure, no network.

  WHAT NO ANSWER MEANS. `.sage/usage.json` absent is "nobody has looked", which is not "nothing is
  used". An app built before this scan existed must draw no mark, not a wrong one.

  WHAT IT CHANGES. Nothing. Publish, bind and unbind read the declaration exactly as they did
  (ADR-0010), including for a Data Source nothing queries: "unused" only ever means "reachable as
  soon as the agent writes one query", because the grant is already held.

Staleness is acceptable and only errs in the true direction — a Binding bound since the last turn
genuinely is not used yet — which is the last test in the first group.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.provision.domino import FakeControlPlane
from sage.resources.provider import DataSource, FakeResourceProvider, LlmAlias, ModelApi
from sage.resources.publish_guard import INDIVIDUAL_CREDENTIAL, PublishRefused
from sage.router.models import ModelCatalog

ALIASES = [
    LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {"input": 3.0}),
    LlmAlias("id-mimo", "mimo-v2.5", "MiMo v2.5", None, ["chat"], {"input": 1.0}),
]
MODEL_APIS = [ModelApi("id-churn", "churn-risk", "Scores churn.", "Running")]
# One of each credential kind, because the last group asks whether an unused Data Source still
# faces the publish guard — and only the individual one has a refusal to skip.
SOURCES = [
    DataSource("ds-dwh", "Snowflake-Data-Warehouse", "Snowflake", "Shared", None, True,
               connector_type="SnowflakeConfig"),
    DataSource("ds-test", "test", "Snowflake", "Individual", None, True,
               connector_type="SnowflakeConfig"),
]


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")   # Domino's entry script
    return t


def _orch(tmp_path: Path, cp: FakeControlPlane | None = None) -> Orchestrator:
    """A real workspace on disk: the file the row reads is the thing under test, so it is not faked."""
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider(list(ALIASES), list(MODEL_APIS), list(SOURCES)),
        control_plane=cp,
        domino_project_id="proj-1",
        domino_project_name="Sales dashboard",
    )
    orch.project(start_preview=False)  # memoize it, so nothing under test starts a dev server
    return orch


def _write_source(orch: Orchestrator, text: str, rel: str = "src/App.tsx") -> None:
    path = orch.project().workspace.path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _label(orch: Orchestrator, resource_id: str) -> bool | None:
    return next(b["used"] for b in orch.list_bindings() if b["id"] == resource_id)


# ---- what the scan answers ----------------------------------------------------------------------


def test_an_alias_the_source_names_is_used_and_one_it_does_not_is_not(tmp_path: Path):
    """The two halves of the answer in one app, which is the only shape that shows the label is
    per Binding rather than per app."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch.bind_llm_alias("id-mimo")
    _write_source(orch, 'askModel(msgs, { alias: "sonnet" });')

    orch._record_resource_usage()

    assert _label(orch, "id-sonnet") is True
    assert _label(orch, "id-mimo") is False


def test_a_data_source_is_found_through_the_queries_recorded_against_it(tmp_path: Path):
    """A Data Source is never named in the app at all — the app calls queries by name and the SQL
    lives in `.sage/queries.json`, so the tokens are the names recorded against THIS store."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh", "ANALYTICS", "MARTS")
    catalog = orch.project().workspace.path / ".sage" / "queries.json"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps([{"name": "daily_pnl", "binding": "ds-dwh", "sql": "select 1"}]))

    orch._record_resource_usage()

    assert _label(orch, "ds-dwh") is True


def test_the_scan_reads_the_bindings_scanner_and_not_the_attachment_one(tmp_path: Path):
    """`_data_usage` scans for uses of an attached FILE, for the detach and delete guards. Wiring
    this to it would answer a different question — the mistake #85 made and ADR-0010 calls out."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    _write_source(orch, 'askModel(msgs, { alias: "sonnet" });')

    def _boom(*a, **k):
        raise AssertionError("_data_usage is the attachment scanner, not the Binding one")

    orch._data_usage = _boom  # type: ignore[method-assign]
    orch._record_resource_usage()

    assert _label(orch, "id-sonnet") is True


def test_a_binding_bound_after_the_last_build_turn_reads_as_unused(tmp_path: Path):
    """The staleness criterion, and the direction it errs in. The Binding was made two minutes ago,
    before the agent wrote a line against it, so "not used yet" is the correct answer rather than a
    tolerable one — and asking for it must not raise for want of a fresher scan."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    _write_source(orch, 'askModel(msgs, { alias: "sonnet" });')
    orch._record_resource_usage()          # the last build turn ends here

    orch.bind_llm_alias("id-mimo")         # ...and this happens after it

    assert _label(orch, "id-sonnet") is True
    assert _label(orch, "id-mimo") is False


# ---- what no answer means -----------------------------------------------------------------------


def test_an_app_no_turn_has_scanned_is_labelled_nothing(tmp_path: Path):
    """None, not False. "Nobody has looked" and "nothing uses it" are different answers, and
    collapsing them marks every Binding of an app built before this scan existed as unused. The
    line `publish_check` draws with `checked`, drawn again here."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")

    assert not orch.project().workspace.usage_path.exists()
    assert _label(orch, "id-sonnet") is None


def test_an_unreadable_answer_reads_as_no_answer(tmp_path: Path):
    """Half a file is not evidence of anything. It costs the labels, not the row."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch._record_resource_usage()
    orch.project().workspace.usage_path.write_text("{not json")

    assert _label(orch, "id-sonnet") is None


def test_an_app_with_no_bindings_still_records_that_it_was_scanned(tmp_path: Path):
    """The walk is skipped — it could label nothing — but the answer is still written, which is
    what makes the first Binding bound after this turn read as unused rather than as unknown."""
    orch = _orch(tmp_path)

    def _boom(*a, **k):
        raise AssertionError("an app with no Binding has nothing to scan for")

    orch._scan_app_sources = _boom  # type: ignore[method-assign]
    orch._record_resource_usage()

    assert orch.project().workspace.read_resource_usage() == []
    orch.bind_llm_alias("id-sonnet")
    assert _label(orch, "id-sonnet") is False


# ---- where it is computed -----------------------------------------------------------------------


def test_the_answer_is_written_at_the_end_of_a_build_turn(tmp_path: Path):
    """Not on render, and not on a schedule of its own. The turn is the moment the source changed,
    so it is the moment the derived answer can have changed."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    _write_source(orch, 'askModel(msgs, { alias: "sonnet" });')
    orch._build_stream = lambda *a, **k: iter([])  # type: ignore[method-assign]

    list(orch.build_stream("add a summarise button"))

    assert orch.project().workspace.read_resource_usage() == ["llm_alias:id-sonnet"]


def test_reading_the_row_runs_no_source_scan(tmp_path: Path):
    """`_scan_app_sources` walks the whole app tree and reads every code file into memory, and the
    row it feeds redraws on every app switch. So the read is JSON off the disk and nothing else —
    `publish_check`'s discipline (ADR-0010)."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    _write_source(orch, 'askModel(msgs, { alias: "sonnet" });')
    orch._record_resource_usage()

    def _boom(*a, **k):
        raise AssertionError("the row must never trigger a source scan")

    orch._scan_app_sources = _boom  # type: ignore[method-assign]

    assert _label(orch, "id-sonnet") is True


def test_the_scan_writes_into_the_app_the_turn_built(tmp_path: Path):
    """The turn's app, not the one on screen. A switch mid-build (#77) leaves the person looking at
    an app this turn never touched, and writing the scanned tree's answer into ITS file would put
    one app's derived answer under another app's name — the wrong pairing #95 is about."""
    orch = _orch(tmp_path)
    built = orch.project().workspace
    orch.bind_llm_alias("id-sonnet")
    _write_source(orch, 'askModel(msgs, { alias: "sonnet" });')
    other = orch.create_app()
    project = orch.project()
    project.turn_app = built                      # the turn pinned the first app

    orch._record_resource_usage()

    assert built.read_resource_usage() == ["llm_alias:id-sonnet"]
    assert project.workspace.app_id == other["id"] != built.app_id
    assert project.workspace.read_resource_usage() is None


def test_a_scan_that_fails_does_not_fail_the_turn(tmp_path: Path):
    """Best-effort, like the two cleanups it stands beside in the turn's `finally`. A build that
    worked must not end badly over a label."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")

    def _boom(*a, **k):
        raise OSError("disk went away")

    orch._scan_app_sources = _boom  # type: ignore[method-assign]
    orch._record_resource_usage()   # no raise

    assert _label(orch, "id-sonnet") is None


# ---- what it changes: nothing -------------------------------------------------------------------


def test_an_unused_data_source_still_faces_the_publish_guard(tmp_path: Path):
    """No exemption, and #85 Q4 settled why twice: skipping the guard on this scan would make an
    advisory signal a gate in the fail-open direction, and "unused" only means "reachable as soon
    as the agent writes one query" — the grant is already held."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    orch.bind_data_source("ds-test", "ANALYTICS", "PUBLIC")
    orch._record_resource_usage()
    assert _label(orch, "ds-test") is False       # nothing queries it

    with pytest.raises(PublishRefused) as ei:
        orch.publish()

    assert [p.reason for p in ei.value.problems] == [INDIVIDUAL_CREDENTIAL]
    assert not cp.published


def test_an_unused_binding_still_publishes_in_the_manifest(tmp_path: Path):
    """The declaration is what the published app's own server reads to decide what a query may
    touch. A record the label thinned out would be a grant the agent's own SQL could widen."""
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    orch.bind_data_source("ds-dwh", "ANALYTICS", "MARTS")
    orch.bind_llm_alias("id-mimo")
    orch._record_resource_usage()

    assert orch.publish()["published"] is True
    recorded = json.loads(orch.project().workspace.bindings_path.read_text())
    assert [e["id"] for e in recorded] == ["ds-dwh", "id-mimo"]


def test_unbind_reports_the_same_refs_whatever_the_label_says(tmp_path: Path):
    """Unbind keeps its own LIVE scan — one deliberate act can afford one — and the label neither
    feeds it nor blocks it."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch._record_resource_usage()                 # recorded while nothing used it
    _write_source(orch, 'askModel(msgs, { alias: "sonnet" });')   # ...and then something did

    assert _label(orch, "id-sonnet") is False     # stale, by design
    assert orch.unbind("llm_alias", "id-sonnet")["refs"] == ["src/App.tsx"]


def test_the_label_never_reaches_the_committed_manifest(tmp_path: Path):
    """`Binding.to_dict` is the manifest entry as well as the HTTP row. A derived answer written
    into `.sage/bindings.json` would outlive the scan that produced it, and would ride into the
    published app's container as though it were part of the record."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch._record_resource_usage()

    recorded = json.loads(orch.project().workspace.bindings_path.read_text())
    assert all("used" not in e for e in recorded)


def test_bind_and_unbind_hand_back_the_labels_too(tmp_path: Path):
    """Every route that returns the list returns the same shape. One that did not would blank the
    marks on screen until the next refresh put them back — the row would say an app whose source
    calls both Aliases calls neither."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    _write_source(orch, 'askModel(msgs, { alias: "sonnet" });')
    orch._record_resource_usage()

    after_bind = orch.bind_llm_alias("id-mimo")
    assert [(b["id"], b["used"]) for b in after_bind] == [("id-sonnet", True), ("id-mimo", False)]
    after_unbind = orch.unbind("llm_alias", "id-mimo")["bindings"]
    assert [(b["id"], b["used"]) for b in after_unbind] == [("id-sonnet", True)]


def test_the_written_answer_stays_out_of_the_apps_git_history(tmp_path: Path):
    """Derived whole from the tree beside it on every build turn, and written after that turn's
    commit. Committed, it would conflict between two Sage Builders in one Project over an answer
    either one can rebuild, and leave a dirty file behind every turn — `.sage/history.md`'s
    reasons, which is why the template ignores this one too."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch._record_resource_usage()

    ignored = (orch.project().workspace.path / ".gitignore").read_text()
    assert ".sage/usage.json" in ignored
    template_ignore = (Path(__file__).resolve().parents[2]
                       / "template" / "react-vite" / ".gitignore").read_text()
    assert ".sage/usage.json" in template_ignore


def test_reset_takes_the_answer_with_the_code_it_was_about(tmp_path: Path):
    """Reset keeps `.sage/`, which is right for the Bindings themselves — they are setup, and
    surviving is the point (#36). The label is not setup: it is derived from the source that just
    went back to the template, so keeping it would have the row calling a Binding used on the
    strength of a file that no longer exists."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    _write_source(orch, 'askModel(msgs, { alias: "sonnet" });')
    orch._record_resource_usage()
    assert _label(orch, "id-sonnet") is True

    orch.reset_app()

    assert _label(orch, "id-sonnet") is None      # nobody has looked at the new tree
    assert [b["id"] for b in orch.list_bindings()] == ["id-sonnet"]   # the record survives
