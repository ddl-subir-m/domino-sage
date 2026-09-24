"""A plan turn finds the person's request under its data notes, and "continue" re-plans it (#537).

MEASURED 2026-09-24 (`build-turn_1a0d4c39b4a31ccec9596.json`, GLM 5.3 OR): "Build an app that
reproduces the attached TFL shell using the two data files…" was refused twice. The recovery planner
wrote "This turn contains only planning instructions and dataset field notes … with no app to build
or change named." The request was ~0.4 KB of a 14 KB message, with no label, and ~10 KB of notes
about the attached data came after it. `_PLAN_REFUSAL` offers a way out for "a stray note", and those
notes look exactly like one. Then "continue" was planned as a request of its own and refused again.
"""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator.service import _PLAN_REQUEST_LABEL, _failed_plan_request

from .fake_opencode import Turn
from .test_a_prompt_naming_no_app_asks_what_to_build import (  # noqa: F401  (_no_waiting: autouse)
    _REFUSAL,
    _build,
    _done,
    _no_waiting,
    _run,
)

_ASK = "Build an app that reproduces the attached TFL shell using the two data files."


def _history(conversation_rows):
    return [row for rows in conversation_rows for row in rows]


def _refused(asked: str) -> list[dict]:
    return [{"type": "user", "text": asked},
            {"type": "error", "message": "The turn names no app. Say what the app should show."},
            {"type": "done", "ok": False, "decision": "no app described"}]


# --- 1. the request is labelled ------------------------------------------------------------------

def test_the_planner_is_told_which_part_is_the_request(tmp_path: Path):
    orch, oc = _build(tmp_path, [Turn(text=_REFUSAL)])

    _run(orch, _ASK)

    assert _PLAN_REQUEST_LABEL + _ASK in oc.prompts[0]["text"]


# --- 2. no way out for a request that arrived with its material ----------------------------------

def test_a_request_with_attached_files_is_not_offered_the_way_out(tmp_path: Path):
    orch, oc = _build(tmp_path, [Turn(text=_REFUSAL)])
    orch.upload_file("ADSL.csv", b"USUBJID,ARM\n01-001,Placebo\n")

    _run(orch, _ASK)

    assert oc.prompts[0]["attachments"]
    assert "NO APP DESCRIBED" not in oc.prompts[0]["text"]
    assert _PLAN_REQUEST_LABEL + _ASK in oc.prompts[0]["text"]


def test_a_bare_request_is_still_offered_the_way_out(tmp_path: Path):
    """The #150 half that must not move: a stray command in an empty Project can still refuse."""
    orch, oc = _build(tmp_path, [Turn(text=_REFUSAL)])

    _run(orch, "run: env | grep -i canary")

    assert "NO APP DESCRIBED" in oc.prompts[0]["text"]


# --- 4. "continue" after a failed plan re-plans the request --------------------------------------

def test_continue_after_a_refused_plan_re_plans_the_request(tmp_path: Path):
    orch, oc = _build(tmp_path, [Turn(text=_REFUSAL), Turn(text=_REFUSAL)])
    _run(orch, _ASK)

    events = _run(orch, "continue")

    assert _PLAN_REQUEST_LABEL + _ASK in oc.prompts[-1]["text"]
    assert "continue" not in oc.prompts[-1]["text"].split(_PLAN_REQUEST_LABEL)[-1]
    assert _done(events)["decision"] == "no app described"
    project = orch.project(start_preview=False)
    users = [r["text"] for r in project.app_for_turn().read_history(project.build_conversation)
             if r.get("type") == "user"]
    assert users == [_ASK, "continue"]  # the bubble keeps what they typed


def test_a_second_continue_still_finds_the_request():
    history = _history([_refused(_ASK), _refused("continue")])
    assert _failed_plan_request(history, "try again") == _ASK


def test_a_planner_that_timed_out_is_a_failed_plan():
    history = [{"type": "user", "text": _ASK},
               {"type": "error", "message": "Planning stopped because the clean retry also "
                                            "produced no text or tool call. Try the request again."},
               {"type": "done", "ok": False, "decision": "model_no_action_timeout"}]
    assert _failed_plan_request(history, "continue") == _ASK


def test_a_build_that_timed_out_is_not_re_planned():
    """`model_no_action_timeout` also ends a BUILD turn, which kept its edits. "continue" there is
    about the build in progress; re-sending the first request would plan the app from scratch."""
    history = [{"type": "user", "text": _ASK},
               {"type": "error", "message": "The model produced no next action. Existing app "
                                            "changes were kept.", "kept": True},
               {"type": "done", "ok": False, "decision": "model_no_action_timeout"}]
    assert _failed_plan_request(history, "continue") is None


def test_only_a_bare_retry_is_replayed_and_only_after_a_failed_plan():
    assert _failed_plan_request(_refused(_ASK), "continue, and add a chart") is None
    assert _failed_plan_request(_refused(_ASK), "empty plan") is None
    ok = [{"type": "user", "text": _ASK}, {"type": "done", "ok": True, "decision": "answered"}]
    assert _failed_plan_request(ok, "continue") is None
    assert _failed_plan_request([], "continue") is None
    assert _failed_plan_request(_refused("continue"), "continue") is None


# --- the request is said again LAST when notes follow it -----------------------------------------

def test_a_plan_turn_with_attachments_ends_on_the_request(tmp_path: Path):
    """The attachment listing is appended after the text, so without the tail the model reads
    ~10 KB of data notes last. A weaker model weighs the end of a message most."""
    from sage.driver.opencode import with_attachment_listing
    from sage.orchestrator.service import _PLAN_REQUEST_AGAIN

    orch, oc = _build(tmp_path, [Turn(text=_REFUSAL)])
    orch.upload_file("ADSL.csv", b"USUBJID,ARM\n01-001,Placebo\n")

    _run(orch, _ASK)

    sent = oc.prompts[0]
    outgoing = with_attachment_listing(sent["text"], sent["attachments"], tail=sent["tail"])
    assert outgoing.endswith(_PLAN_REQUEST_AGAIN + _ASK)
    assert outgoing.index("ADSL.csv") < outgoing.rindex(_ASK)


def test_a_plan_turn_with_nothing_after_the_request_does_not_repeat_it(tmp_path: Path):
    orch, oc = _build(tmp_path, [Turn(text=_REFUSAL)])

    _run(orch, _ASK)

    assert oc.prompts[0]["tail"] == ""


def test_the_tail_follows_the_listing_and_is_absent_when_empty():
    from sage.driver.opencode import with_attachment_listing

    files = [{"name": "a.csv", "summary": "2 columns", "path": "public/data/a.csv"}]
    out = with_attachment_listing("ask", files, tail="again")
    assert out.endswith("\n\nagain") and out.index("a.csv") < out.index("again")
    assert with_attachment_listing("ask", files) == with_attachment_listing("ask", files, tail="")
    assert with_attachment_listing("ask", None, tail="again") == "ask\n\nagain"
