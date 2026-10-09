"""A plan step's Verify is held to the page the build rendered, and a repair that misses it does not
end clean (#750).

Live (#714, Signal Room 24): the plan's Screens named "a feature-adoption chart, a customer-topics
chart" for Product insights, and step Verify lines named "a MEDDPICC scorecard tag, the Deal Desk
approval chain" for the deal page. Product insights was built as four tables, the deal page showed
neither section, and the build ended "Done — build is clean" after one repair. The only reader of a
Verify line was the #716 review, and it was handed the code diff alone and told never to flag "how
the page looks"; the headless page check opened every screen and recorded nothing of what they drew;
and nothing read the plan again after the review's own repair.

Now the page check reports what each screen it opened shows (visible text, charts, tables), the
review reads it beside every step and the plan's Screens, and a Verify still unmet after its one
repair ends the build incomplete, naming the step.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sage.build_policy import BuildPolicy
from sage.orchestrator.service import _PLAN_REVIEW_SYSTEM

from .fake_opencode import Turn
from .test_a_dead_alias_stops_the_turn_before_it_starts import _no_waiting  # noqa: F401
from .test_a_planned_build_is_reviewed_against_its_done_when import ReviewGateway, _done, _orch
from .test_changed_page_validation import Preview

PLAN = Turn(text="""# Signal Room

A revenue team's pipeline, deal and product view.

## Problem & outcome
Deal and product signals live in five tools; one app shows them together.

## Who uses this
A revenue leader.

## What it does
- Product insights for a chosen period.
- A page per deal.

## Screens
- **Product insights** — a feature-adoption chart and a customer-topics chart for the period.
- **Deal page** — the deal's Salesforce fields, a MEDDPICC scorecard tag and the Deal Desk approval chain.

## Done when
- Each screen shows its sections.

## Plan
### 1. Product insights
- Files — static/components/ProductInsights.js
- Do — Draw feature adoption and customer topics for the chosen period.
- Verify — the Product insights tab shows a feature-adoption chart and a customer-topics chart.

### 2. Deal page
- Files — static/components/DealPage.js
- Do — Show the deal's fields, its MEDDPICC score and its Deal Desk approvals.
- Verify — the deal page shows a MEDDPICC scorecard tag and the Deal Desk approval chain.
""")

BUILT = Turn(writes={
    "static/components/ProductInsights.js": (
        "export const ProductInsights = () => tables('Product insights', 'Feature adoption', "
        "'Customer topics');\n"),
    "static/components/DealPage.js": (
        "export const DealPage = () => fields('Deal page', 'MEDDPICC', "
        "'Score based on CRM fields only');\n"),
})
REPAIRED = Turn(writes={
    "static/components/ProductInsights.js": "export const ProductInsights = () => stillTables();\n"})

# What the page check saw on the demo's first build, one screen per tab it opened. Every piece of
# text the page showed, the rows included.
SCREENS = [
    {"screen": "Product insights", "charts": 0, "tables": 4, "loading": False,
     "texts": ["Product insights", "Deal page", "Feature adoption", "Jobs", "+5.8%",
               "Experiments", "-27.5%", "Customer topics"]},
    {"screen": "Deal page", "charts": 0, "tables": 1, "loading": False,
     "texts": ["Product insights", "Deal page", "Johnson & Johnson", "$12.4M", "MEDDPICC",
               "Score based on CRM fields only"]},
]

INSIGHTS_UNMET = {"unmet": [{
    "step": 1, "done_when": "the Product insights tab shows a feature-adoption chart and a "
                            "customer-topics chart.",
    "file": "static/components/ProductInsights.js",
    "why": "the Product insights screen drew 0 charts and 4 tables"}]}
DEAL_UNMET = {"unmet": [{
    "step": 2, "done_when": "the deal page shows a MEDDPICC scorecard tag and the Deal Desk "
                            "approval chain.",
    "file": "static/components/DealPage.js",
    "why": "the Deal page screen shows no MEDDPICC score and no approvals"}]}


class Check:
    """The headless check as `_validate_page` drives it: the walk is done, and `screens` is what
    the script reported."""

    def __init__(self, screens):
        self._screens, self.closed = screens, 0

    def walked(self):
        return True

    def screens(self):
        return list(self._screens)

    def close(self):
        self.closed += 1


class Answers(ReviewGateway):
    """A review gateway that gives each review the next answer in turn, then nothing unmet, so a
    review loop that never stops still ends."""

    def __init__(self, *answers: dict):
        super().__init__(b"")
        self._answers = [json.dumps(a) for a in answers]

    def route(self, request, labels):
        if labels.component == "plan-review":
            n = len(self.reviews)
            self.answer = self._answers[n] if n < len(self._answers) else '{"unmet": []}'
        yield from super().route(request, labels)


def _approve(tmp: Path, monkeypatch, gateway: ReviewGateway, *after: Turn, screens=SCREENS):
    orch, oc = _orch(tmp, gateway, [PLAN, BUILT, *after],
                     replace(BuildPolicy(), page_ack_wait_seconds=0.5))
    project = orch.project(start_preview=False)
    monkeypatch.setattr(orch, "_restart_preview_for_config_change", lambda project: None)
    project.supervisor = Preview(project.workspace.app_id)
    checks = []

    def page_check(url, timeout):
        orch.record_preview_ack(parse_qs(urlsplit(url).query)["sageValidation"][0])
        checks.append(Check(screens))
        return checks[-1]

    orch._page_check = page_check if screens is not None else None
    list(orch.build_stream("build Signal Room", conversation="c1"))
    events = list(orch.approve_stream(conversation="c1"))
    assert all(c.closed == 1 for c in checks)
    return events, oc


def _payload(request: dict) -> str:
    return next(m["content"] for m in request["messages"] if m["role"] == "user")


def test_the_review_reads_what_each_opened_screen_showed_beside_the_plans_screens(
        tmp_path, monkeypatch):
    """Plant: leave the page out of the payload and the counts and texts go missing."""
    gateway = Answers({"unmet": []})
    _approve(tmp_path, monkeypatch, gateway)

    [request] = gateway.reviews
    text = _payload(request)
    assert "Product insights — a feature-adoption chart and a customer-topics chart" in text
    assert "Deal page — the deal's Salesforce fields, a MEDDPICC scorecard tag" in text
    assert ('Screen "Product insights": 0 charts, 4 tables\n'
            "Product insights · Deal page · Feature adoption · Customer topics") in text
    assert ('Screen "Deal page": 0 charts, 1 table\n'
            "Product insights · Deal page · MEDDPICC · Score based on CRM fields only") in text


def test_no_value_from_the_apps_data_reaches_the_review(tmp_path, monkeypatch):
    """Only text the app's code spells goes to the model; a row goes only by the person's consent
    (ADR-0041). Plant: send every text piece and the rows show up."""
    gateway = Answers({"unmet": []})
    by_account = {"screen": "Acme Corp", "charts": 1, "tables": 0, "loading": False,
                  "texts": ["Acme Corp", "Deal page"]}
    _approve(tmp_path, monkeypatch, gateway, screens=[*SCREENS, by_account])

    text = _payload(gateway.reviews[0])
    for row in ("Jobs", "+5.8%", "Experiments", "Johnson & Johnson", "$12.4M", "Acme Corp"):
        assert row not in text
    assert "A screen: 1 chart, 0 tables\nDeal page" in text


def test_the_review_is_allowed_to_judge_the_page_it_is_shown():
    """Plant: restore "Never flag ... how the page looks" and this goes red."""
    assert "how the page looks" not in _PLAN_REVIEW_SYSTEM
    assert "screen" in _PLAN_REVIEW_SYSTEM and "lacks" in _PLAN_REVIEW_SYSTEM


def test_a_screen_still_loading_is_marked_so(tmp_path, monkeypatch):
    gateway = Answers({"unmet": []})
    loading = [{**SCREENS[0], "loading": True}]
    _approve(tmp_path, monkeypatch, gateway, screens=loading)

    assert 'Screen "Product insights" (still loading when read)' in _payload(gateway.reviews[0])


def test_with_no_page_seen_the_review_is_told_so(tmp_path, monkeypatch):
    gateway = Answers({"unmet": []})
    _approve(tmp_path, monkeypatch, gateway, screens=None)

    text = _payload(gateway.reviews[0])
    assert "Screen \"" not in text
    assert "not opened" in text


def test_product_insights_built_as_tables_is_repaired_then_reported_not_done(tmp_path, monkeypatch):
    """The demo's Product insights case. Plant: skip the second review and the build ends clean."""
    gateway = Answers(INSIGHTS_UNMET, INSIGHTS_UNMET)
    events, oc = _approve(tmp_path, monkeypatch, gateway, REPAIRED)

    assert len(oc.prompts) == 3  # plan, build, one repair
    assert "feature-adoption chart" in oc.prompts[2]["text"]
    assert len(gateway.reviews) == 2
    done = _done(events)
    assert done["ok"] is False
    assert done["decision"] == "incomplete — plan step 1 not met"
    [gap] = [e for e in events if e["type"] == "plan-unbuilt"]
    assert gap["steps"] == [1]
    assert "0 charts and 4 tables" in gap["message"]
    # A reload shows it too. Plant: drop it from the persisted rows and this goes red.
    history = oc.orch.project(start_preview=False).app_for_turn().read_history("c1")
    assert [r["message"] for r in history if r["type"] == "plan-unbuilt"] == [gap["message"]]


def test_the_deal_page_missing_its_sections_is_repaired_then_reported_not_done(tmp_path, monkeypatch):
    """The demo's deal-page case."""
    gateway = Answers(DEAL_UNMET, DEAL_UNMET)
    events, oc = _approve(tmp_path, monkeypatch, gateway, Turn(writes={
        "static/components/DealPage.js": "export const DealPage = () => fieldsAgain();\n"}))

    assert len(oc.prompts) == 3
    assert "Deal Desk approval chain" in oc.prompts[2]["text"]
    done = _done(events)
    assert done["ok"] is False
    assert done["decision"] == "incomplete — plan step 2 not met"
    [gap] = [e for e in events if e["type"] == "plan-unbuilt"]
    assert gap["steps"] == [2] and "no MEDDPICC score" in gap["message"]


def test_a_repair_that_meets_the_verify_ends_clean(tmp_path, monkeypatch):
    """Plant: report the first review's answer instead of the second's and this goes red."""
    gateway = Answers(INSIGHTS_UNMET, {"unmet": []})
    events, oc = _approve(tmp_path, monkeypatch, gateway, REPAIRED)

    assert len(oc.prompts) == 3
    assert len(gateway.reviews) == 2
    assert _done(events)["ok"] is True
    assert not [e for e in events if e["type"] == "plan-unbuilt"]


def test_the_second_review_never_sends_a_second_repair(tmp_path, monkeypatch):
    """Plant: let the re-check repair too and a fourth prompt goes out."""
    gateway = Answers(INSIGHTS_UNMET, INSIGHTS_UNMET, INSIGHTS_UNMET)
    events, oc = _approve(tmp_path, monkeypatch, gateway, REPAIRED, REPAIRED)

    assert len(oc.prompts) == 3
    assert len(gateway.reviews) == 2
    assert _done(events)["ok"] is False
