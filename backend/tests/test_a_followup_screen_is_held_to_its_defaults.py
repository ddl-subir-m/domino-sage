"""A follow-up build's new screen is held to what it draws at its defaults (#765).

Live (#714, Signal Room 28, prompt 8): a follow-up turn on a built app added a Usage drift tab whose
default periods overlap, so it opened on "Periods cannot overlap", drew no card and read none of the
drift queries its plan step names. The plan's Verify and its first Done-when named cards at the
defaults. The turn ended "Page checks passed. Data access wasn't checked."
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sage.build_policy import BuildPolicy
from sage.router.models import Mode

from .fake_opencode import Turn
from .test_a_dead_alias_stops_the_turn_before_it_starts import _no_waiting  # noqa: F401
from .test_a_plan_steps_verify_is_held_to_the_page import Answers, Check
from .test_a_planned_build_is_reviewed_against_its_done_when import _done, _orch
from .test_changed_page_validation import Preview

QUERIES = ".sage/queries.json"

FIRST_PLAN = Turn(text="""# Signal Room

A revenue team's pipeline view.

## Problem & outcome
Pipeline signals live in five tools; one app shows them together.

## Who uses this
A revenue leader.

## What it does
- A pipeline overview.

## Screens
- **Pipeline overview** — open pipeline and stalled deals.

## Done when
- The overview shows the open pipeline.

## Plan
### 1. Pipeline overview
- Files — static/components/Pipeline.js, .sage/queries.json
- Do — Show open deals from `open_deals`.
- Verify — the Pipeline overview shows the open pipeline.
""")

FIRST_BUILT = Turn(writes={
    "static/components/Pipeline.js": "export const Pipeline = () => page('Pipeline overview', "
                                     "useQuery('open_deals'));\n",
    QUERIES: json.dumps([{"name": "open_deals", "sql": "select 1"}]) + "\n",
})

DRIFT_PLAN = Turn(text="""# Signal Room

Add a Usage drift tab.

## Problem & outcome
The data science team cannot see whether usage drifted.

## Who uses this
A data scientist.

## What it does
- Compares per-user activity between a reference and a current period.

## Screens
- **Usage drift** — metric cards for JSD, PSI and Wasserstein, histograms, ECDFs and a trend.

## Done when
- Opening the Usage drift tab shows cards for JSD, PSI and Wasserstein over the default 7-vs-28-day periods.

## Plan
### 1. Drift queries
- Files — .sage/queries.json
- Do — Add `drift_quantiles`, `drift_bins` and `drift_trend` to the catalog.
- Verify — each drift query is declared.

### 2. Usage drift tab
- Files — static/components/UsageDrift.js
- Do — Add the Usage drift tab reading `drift_quantiles`, `drift_bins` and `drift_trend`, defaulting to the last 7 complete days against the 28 days before.
- Verify — opening the Usage drift tab shows cards for JSD, PSI and Wasserstein at the default periods.
""")

DRIFT_CATALOG = json.dumps([{"name": n, "sql": "select 1"} for n in (
    "open_deals", "drift_quantiles", "drift_bins", "drift_trend")]) + "\n"
DRIFT_BUILT = Turn(writes={
    QUERIES: DRIFT_CATALOG,
    "static/components/UsageDrift.js": (
        "export const UsageDrift = () => page('Usage drift', overlap(defaults) ? "
        "refuse('Periods cannot overlap') : cards(useQuery('drift_quantiles'), "
        "useQuery('drift_bins'), useQuery('drift_trend')));\n"),
})

PIPELINE = {"screen": "Pipeline overview", "charts": 1, "tables": 1, "loading": False,
            "texts": ["Pipeline overview", "Usage drift"]}
DRIFT = {"screen": "Usage drift", "charts": 0, "tables": 0, "loading": False,
         "texts": ["Pipeline overview", "Usage drift", "Periods cannot overlap"]}
# The overview read its query; one read was still in flight when the check ended, which is what
# left the live turn's data stage unverified.
READS = [{"kind": "query", "path": "/api/queries/open_deals", "resourceIds": [], "status": 200,
          "outcome": "passed"},
         {"kind": "route", "path": "/api/health", "resourceIds": [], "status": None,
          "outcome": "pending"}]

DRIFT_UNMET = {"unmet": [{
    "step": 2, "done_when": "opening the Usage drift tab shows cards for JSD, PSI and Wasserstein "
                            "at the default periods.",
    "file": "static/components/UsageDrift.js",
    "why": "the Usage drift screen opened on 'Periods cannot overlap' with no cards"}]}


class Answering(Preview):
    """A preview whose app's own `answer()` served `names` in the current document."""

    names: tuple[str, ...] = ()

    def query_reads(self):
        return [{"name": n, "generation": self.status()["generation"], "status": 200}
                for n in self.names]


def _followup(tmp: Path, monkeypatch, answers: tuple[dict, ...], *after: Turn,
              plan: Turn = DRIFT_PLAN, built: Turn = DRIFT_BUILT, screens=(PIPELINE, DRIFT),
              reads=READS, answered: tuple[str, ...] = ()):
    """Signal Room built from its first plan, then a follow-up plan adding the Usage drift tab,
    approved. The page check reports the overview on the first build, and `screens` and `reads`
    on every check of the follow-up. `answers` are the follow-up's reviews, in turn, and
    `answered` the queries the app's own `answer()` served on the follow-up."""
    gateway = Answers({"unmet": []}, *answers)
    orch, oc = _orch(tmp, gateway, [FIRST_PLAN, FIRST_BUILT, plan, built, *after],
                     replace(BuildPolicy(), page_ack_wait_seconds=0.5))
    project = orch.project(start_preview=False)
    monkeypatch.setattr(orch, "_restart_preview_for_config_change", lambda project: None)
    project.supervisor = Answering(project.workspace.app_id)
    checks: list[Check] = []
    seen = {"screens": [PIPELINE], "reads": READS[:1]}

    def page_check(url, timeout):
        orch.record_preview_ack(parse_qs(urlsplit(url).query)["sageValidation"][0])
        project.page_validation.data_reads.extend(dict(r) for r in seen["reads"])
        checks.append(Check(seen["screens"]))
        return checks[-1]

    orch._page_check = page_check
    list(orch.build_stream("build Signal Room", conversation="c1"))
    first = list(orch.approve_stream(conversation="c1"))
    assert _done(first)["ok"] is True
    reviews_before = len(gateway.reviews)
    seen.update(screens=list(screens), reads=list(reads))
    project.supervisor.names = answered
    project.control.set_mode(Mode.PLAN)
    list(orch.build_stream("Add a Usage drift tab", conversation="c1"))
    project.control.set_mode(Mode.AUTO)
    events = list(orch.approve_stream(conversation="c1"))
    assert all(c.closed == 1 for c in checks)
    return events, oc, gateway.reviews[reviews_before:]


def test_a_followup_tab_that_refuses_its_defaults_does_not_end_passed(tmp_path, monkeypatch):
    """The ticket's first case, on the path where nothing else repaired the turn: the review runs
    on the follow-up and its unmet step ends the turn incomplete."""
    events, _, reviews = _followup(tmp_path, monkeypatch, (DRIFT_UNMET, DRIFT_UNMET),
                                    Turn(writes={"static/components/UsageDrift.js": "still();\n"}))

    assert len(reviews) == 2
    assert 'Screen "Usage drift": 0 charts, 0 tables' in reviews[0]["messages"][1]["content"]
    assert _done(events)["ok"] is False
    [gap] = [e for e in events if e["type"] == "plan-unbuilt"]
    assert gap["steps"] == [2] and "Periods cannot overlap" in gap["message"]


def test_a_screen_whose_plan_step_names_queries_it_never_read_fails_the_data_stage(
        tmp_path, monkeypatch):
    """The ticket's second case: zero reads of the queries a step names, on a screen the check
    opened at its defaults, is a failed data stage naming the screen, not `unverified`."""
    events, _, _ = _followup(tmp_path, monkeypatch, ())

    done = _done(events)
    assert done["ok"] is False
    assert done["verification"]["stages"]["data"] == "failed"
    reason = done["verification"]["reason"]
    assert '"Usage drift"' in reason and "plan step 2" in reason
    assert "drift_quantiles, drift_bins and drift_trend" in reason
    [notice] = [e for e in events if e["type"] == "data-source-failed"]
    assert notice["message"] == reason


def test_the_review_still_runs_after_the_unbuilt_step_repair(tmp_path, monkeypatch):
    """The live turn's path: one step's files were left unwritten on the first pass, so the one
    plan repair went to it. The review then never ran, and the turn ended passed over a screen its
    Verify plainly fails. It now runs on the pass after that repair and reports, without a second
    repair."""
    plan = Turn(text=DRIFT_PLAN.text + (
        "\n### 3. Register the tab\n- Files — static/app.js\n- Do — Mount UsageDrift in the tabs.\n"
        "- Verify — the tab bar shows Usage drift.\n"))
    events, oc, reviews = _followup(
        tmp_path, monkeypatch, (DRIFT_UNMET,), Turn(writes={"static/app.js": "tabs();\n"}),
        plan=plan, reads=[*READS, *({"kind": "query", "path": f"/api/queries/{n}",
                                     "resourceIds": [], "status": 200, "outcome": "passed"}
                                    for n in ("drift_quantiles", "drift_bins", "drift_trend"))])

    assert [("none were written" in p["text"]) for p in oc.prompts[4:]] == [True]
    assert len(reviews) == 1
    done = _done(events)
    assert done["ok"] is False
    assert done["decision"] == "incomplete — plan step 2 not met"


def test_the_diag_log_says_whether_the_review_ran_and_what_it_decided(
        tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.INFO, logger="sage.orchestrator"):
        _followup(tmp_path, monkeypatch, (DRIFT_UNMET, DRIFT_UNMET),
                  Turn(writes={"static/components/UsageDrift.js": "still();\n"}))

    said = [r.getMessage() for r in caplog.records if r.getMessage().startswith("plan review:")]
    assert "plan review: ran, unmet: step 2" in said
    assert said[-1] == "plan review: this turn ran 2 reviews; still unmet: step 2"


def test_a_query_read_past_the_cap_on_recorded_reads_still_counts(tmp_path, monkeypatch):
    """The page's first twenty reads fill `data_reads`, and the drift tab's reads come after. They
    were still asked for. Plant: stop recording names past the cap and this goes red."""
    full = [{**READS[0]} for _ in range(20)]
    events, _, _ = _followup(tmp_path, monkeypatch, (), reads=full,
                             answered=("drift_quantiles", "drift_bins", "drift_trend"))

    done = _done(events)
    assert done["verification"]["readsTruncated"] is True
    assert done["verification"]["stages"]["data"] == "unverified"
    assert not [e for e in events if e["type"] == "data-source-failed"]


def test_a_screen_the_check_never_opened_is_not_judged(tmp_path, monkeypatch):
    """A screen reached only from a row never had its defaults, so its reads prove nothing."""
    events, _, _ = _followup(tmp_path, monkeypatch, (), screens=(PIPELINE,))

    assert _done(events)["verification"]["stages"]["data"] == "unverified"
    assert not [e for e in events if e["type"] == "data-source-failed"]
