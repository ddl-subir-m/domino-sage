"""A planned build that passed every check is read once against its plan's Done-when (#716).

Live (Haiku demo run, #714): the Usage Drift tab rendered only "Invalid period selection" with its
defaults, every period preset ending yesterday, which broke the plan's own Done-when "the defaults
show results". The turn ended "build is clean": no crash, no failed query and no missing file, so no
structural check could see it. Now one call to the plan-tier model reads the plan's steps beside the
turn's code diff, and a Done-when it names as plainly unmet goes back once, through the same budget
the unbuilt-step repair spends. Every other answer ends the turn as it ended before.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import replace
from pathlib import Path

from sage.build_policy import BuildPolicy
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import FakeResourceProvider

from .fake_opencode import Turn, execution_plan
from .test_a_dead_alias_stops_the_turn_before_it_starts import (  # noqa: F401
    ALIASES,
    LIVE,
    BreakingOpenCode,
    OkFeedback,
    _no_waiting,
    _skip_planning,
    _template,
)

PLAN = Turn(text=execution_plan(
    "Usage Drift", "A usage drift tab.", "Draw the drift tab", files="static/UsageDrift.js",
    work="Add period presets and the drift table.", done_when="The defaults show results."))

BUILT = Turn(writes={"static/UsageDrift.js": "const presets = periodsEndingYesterday();\n"})

UNMET = {"unmet": [{"step": 1, "done_when": "The defaults show results.",
                    "file": "static/UsageDrift.js",
                    "why": "presets overlap, so the reference period contains the current one"}]}


class ReviewGateway:
    """Answers the plan review with `answer` (bytes, or an exception to raise) and every other
    direct call with the word the scope classifier wants."""

    def __init__(self, answer: object = b"", *, hold: threading.Event | None = None) -> None:
        self.answer = answer
        self.hold = hold
        self.reviews: list[dict] = []

    def route(self, request, labels):
        if labels.component != "plan-review":
            body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
            yield f"data: {body}\n\ndata: [DONE]\n\n".encode()
            return
        self.reviews.append(request)
        if self.hold is not None:
            self.hold.wait(5)
        if isinstance(self.answer, BaseException):
            raise self.answer
        body = json.dumps({"choices": [{"delta": {"content": self.answer}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _orch(tmp: Path, gateway: ReviewGateway, turns: list[Turn], policy: BuildPolicy | None = None):
    ws = tmp / "mnt" / "code"
    oc = BreakingOpenCode(ws, turns)
    orch = Orchestrator(
        workspace_dir=ws, template=_template(tmp), gateway=gateway, catalog=LIVE,
        project_id="Sage", feedback=OkFeedback(), opencode_client=oc,
        resources=FakeResourceProvider(list(ALIASES)), gateway_mode="domino",
        build_policy=policy or BuildPolicy(),
    )
    oc.orch = orch
    orch.project(start_preview=False)
    return orch, oc


def _approve(tmp: Path, answer: object, *after: Turn, plan: Turn = PLAN, built: Turn = BUILT,
             policy: BuildPolicy | None = None, hold: threading.Event | None = None):
    gateway = ReviewGateway(answer, hold=hold)
    orch, oc = _orch(tmp, gateway, [plan, built, *after], policy)
    list(orch.build_stream("build me a usage drift tab", conversation="c1"))
    events = list(orch.approve_stream(conversation="c1"))
    return events, gateway, oc


def _done(events: list[dict]) -> dict:
    [done] = [e for e in events if e["type"] == "done"]
    return done


def _review_text(request: dict) -> str:
    return "\n".join(m["content"] for m in request["messages"])


def test_an_unmet_done_when_is_sent_back_once_with_its_text(tmp_path: Path):
    events, gateway, oc = _approve(tmp_path, json.dumps(UNMET), Turn(writes={
        "static/UsageDrift.js": "const presets = periodsEndingToday();\n"}))

    repairs = [p["text"] for p in oc.prompts[2:]]
    assert len(repairs) == 1
    assert "The defaults show results." in repairs[0]
    assert "presets overlap" in repairs[0] and "static/UsageDrift.js" in repairs[0]
    assert any(e["type"] == "iterate" and "Done-when" in e["reason"] for e in events)
    assert len(gateway.reviews) == 2


def test_the_review_reads_the_plan_and_the_turns_code(tmp_path: Path):
    _, gateway, _ = _approve(tmp_path, json.dumps({"unmet": []}))

    [request] = gateway.reviews
    text = _review_text(request)
    assert "Add period presets and the drift table." in text
    assert "The defaults show results." in text
    assert "periodsEndingYesterday" in text
    assert request["model"] == LIVE.plan


def test_an_empty_answer_ends_clean(tmp_path: Path):
    events, gateway, oc = _approve(tmp_path, json.dumps({"unmet": []}))

    assert len(gateway.reviews) == 1
    assert len(oc.prompts) == 2
    assert _done(events)["ok"] is True


def test_a_malformed_answer_ends_clean_and_says_why(tmp_path: Path, caplog):
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        events, gateway, oc = _approve(tmp_path, "Looks great to me!")

    assert len(gateway.reviews) == 1
    assert len(oc.prompts) == 2
    assert _done(events)["ok"] is True
    assert any("plan review" in r.getMessage() and "malformed" in r.getMessage()
               for r in caplog.records)


def test_a_failed_call_ends_clean_and_says_why(tmp_path: Path, caplog):
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        events, gateway, oc = _approve(tmp_path, RuntimeError("gateway returned 502"))

    assert len(gateway.reviews) == 1
    assert len(oc.prompts) == 2
    assert _done(events)["ok"] is True
    assert any("plan review" in r.getMessage() and "gateway returned 502" in r.getMessage()
               for r in caplog.records)


def test_a_call_past_its_time_cap_ends_clean_and_says_why(tmp_path: Path, caplog):
    hold = threading.Event()
    policy = replace(BuildPolicy(), plan_review_timeout_seconds=0.01)
    try:
        with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
            events, gateway, oc = _approve(tmp_path, json.dumps(UNMET), policy=policy, hold=hold)
    finally:
        hold.set()

    assert len(gateway.reviews) == 1
    assert len(oc.prompts) == 2
    assert _done(events)["ok"] is True
    assert any("plan review" in r.getMessage() and "timed out" in r.getMessage()
               for r in caplog.records)


def test_the_setting_off_means_no_call(tmp_path: Path):
    policy = replace(BuildPolicy(), plan_review=False)
    events, gateway, oc = _approve(tmp_path, json.dumps(UNMET), policy=policy)

    assert gateway.reviews == []
    assert len(oc.prompts) == 2
    assert _done(events)["ok"] is True


def test_a_turn_without_an_approved_plan_is_not_reviewed(tmp_path: Path):
    gateway = ReviewGateway(json.dumps(UNMET))
    orch, oc = _orch(tmp_path, gateway, [BUILT])
    _skip_planning(orch)

    events = list(orch.build_stream("add a drift tab", conversation="c1"))

    assert len(oc.prompts) == 1
    assert gateway.reviews == []
    assert _done(events)["ok"] is True


def test_a_follow_up_after_an_approved_build_is_not_reviewed(tmp_path: Path):
    gateway = ReviewGateway(json.dumps({"unmet": []}))
    orch, oc = _orch(tmp_path, gateway, [PLAN, BUILT, Turn(writes={
        "static/UsageDrift.js": "const presets = periodsEndingToday();\n"})])
    list(orch.build_stream("build me a usage drift tab", conversation="c1"))
    list(orch.approve_stream(conversation="c1"))
    assert len(gateway.reviews) == 1
    _skip_planning(orch)

    events = list(orch.build_stream("end the presets today", conversation="c1"))

    assert len(oc.prompts) == 3
    assert len(gateway.reviews) == 1
    assert _done(events)["ok"] is True


def test_a_turn_whose_queries_failed_is_not_reviewed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Orchestrator, "_query_failures",
                        lambda self, project: {"usage": "Object 'USAGE' does not exist."})
    events, gateway, _ = _approve(tmp_path, json.dumps(UNMET))

    assert gateway.reviews == []
    assert _done(events)["ok"] is False


def test_the_review_shares_the_unbuilt_step_repair_budget(tmp_path: Path):
    """Step 2 is unwritten on the first pass, so the one plan-level repair goes to it. The pass that
    writes it is still reviewed, once, and what it names ends the turn incomplete without a second
    repair (#765). Plant: skip the review once plan_fixes is 1 and this goes red on the review
    count; let it repair and a fourth prompt goes out."""
    plan = Turn(text=PLAN.text + (
        "\n\n### 2. Register the tab\n- Files — static/index.html\n"
        "- Do — Load UsageDrift.js.\n- Done when — The tab is in the nav."))
    events, gateway, oc = _approve(
        tmp_path, json.dumps(UNMET), Turn(writes={"static/index.html": "<script></script>\n"}),
        plan=plan)

    assert [("none were written" in p["text"]) for p in oc.prompts[2:]] == [True]
    assert len(gateway.reviews) == 1
    assert _done(events)["decision"] == "incomplete — plan step 1 not met"


def test_a_second_unmet_answer_is_not_sent_back_again(tmp_path: Path):
    """The pass that answers the repair is reviewed once more, and what is still unmet ends the
    turn incomplete rather than going back a second time (#750)."""
    events, gateway, oc = _approve(tmp_path, json.dumps(UNMET), Turn(writes={
        "static/UsageDrift.js": "const presets = stillWrong();\n"}))

    assert len(gateway.reviews) == 2
    assert len(oc.prompts) == 3
    assert _done(events)["ok"] is False


def test_the_prompt_carries_code_only(tmp_path: Path):
    built = Turn(writes={
        "static/UsageDrift.js": "const presets = periodsEndingYesterday();\n",
        "data/usage.csv": "account,ROW-SENTINEL-1\n",
        "static/rows.json": '{"row": "ROW-SENTINEL-2"}\n',
        ".env": "TOKEN=SECRET-SENTINEL\n",
        ".sage/queries.json": '{"usage": "SAGE-SENTINEL"}\n',
        "vendor/lib.js": "// VENDOR-SENTINEL\n",
    })
    _, gateway, _ = _approve(tmp_path, json.dumps({"unmet": []}), built=built)

    [request] = gateway.reviews
    text = _review_text(request)
    assert "periodsEndingYesterday" in text
    for sentinel in ("ROW-SENTINEL", "SECRET-SENTINEL", "SAGE-SENTINEL", "VENDOR-SENTINEL"):
        assert sentinel not in text


def test_a_large_diff_is_cut_and_says_so(tmp_path: Path):
    big = "".join(f"const line{i} = {i};\n" for i in range(20_000))
    built = Turn(writes={"static/UsageDrift.js": big})
    _, gateway, _ = _approve(tmp_path, json.dumps({"unmet": []}), built=built)

    [request] = gateway.reviews
    text = _review_text(request)
    assert "truncated" in text
    assert len(text.encode()) < len(big.encode()) // 2
