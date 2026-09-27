"""A later question is answered as itself, not as another pass on the first one.

The first investigation turn still says to take the next step. A follow-up ends on the new
sentence, and an implement turn whose notes follow the request ends on that request too.
"""
from __future__ import annotations

from pathlib import Path

from sage.driver.opencode import with_attachment_listing
from sage.orchestrator.service import _PLAN_REQUEST_AGAIN, _THIS_TURN_QUESTION
from sage.router.models import Mode
from sage.workspace.threads import findings_file

from .fake_opencode import Turn
from .test_a_prompt_naming_no_app_asks_what_to_build import _build, _run
from .test_chat_turn import IntentGateway, _orch

_FIRST = "Fuse cases and transcripts and tell me who asked for ARM support."
_FOLLOW = "what about carbon arc have they asked?"


def _investigating(tmp_path: Path, turns: list[Turn]):
    orch, oc = _orch(tmp_path, turns, gateway=IntentGateway(
        {"label": "data_answer", "confidence": 0.93}))
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "data_source", "name": "Snowflake-Data-Warehouse",
        "bindingKey": ["data_source", "ds-dwh"],
        "scope": {"database": "DWH", "schema": "MARTS", "table": "SFDC__ACCOUNT"},
    })
    orch.decide_thread_investigation(tid, "open")
    return orch, oc, tid


def test_the_first_investigation_turn_still_takes_the_next_step(tmp_path: Path):
    orch, oc, tid = _investigating(tmp_path, [Turn(text="334 customers.")])

    list(orch.chat_stream(tid, _FIRST))

    prompt = oc.prompts[-1]["text"]
    assert "take the next step" in prompt
    assert _THIS_TURN_QUESTION not in prompt
    assert prompt.endswith(_FIRST)


def test_a_chat_follow_up_ends_on_the_new_sentence(tmp_path: Path):
    orch, oc, tid = _investigating(tmp_path, [
        Turn(text="334 customers."),
        Turn(text="Carbon Arc is not in the list."),
    ])
    list(orch.chat_stream(tid, _FIRST))
    findings_file(orch.project(start_preview=False).record.path, tid).write_text(
        "334 customers mentioned ARM.\n")

    list(orch.chat_stream(tid, _FOLLOW))

    prompt = oc.prompts[-1]["text"]
    assert prompt.endswith(_THIS_TURN_QUESTION + "\n" + _FOLLOW)
    assert "take the next step" not in prompt
    assert "classifying, summarising, extracting, or deciding" in prompt
    assert "Read it before you plan this turn" not in prompt
    assert "measurements from earlier turns" in prompt


def test_an_implement_turn_with_a_file_ends_on_the_request(tmp_path: Path):
    orch, oc = _build(tmp_path, [Turn(writes={"src/App.tsx": "// table\n"})])
    orch.upload_file("ADSL.csv", b"USUBJID,ARM\n01-001,Placebo\n")

    _run(orch, "Add a table of accounts.", mode=Mode.IMPLEMENT)

    sent = oc.prompts[0]
    outgoing = with_attachment_listing(sent["text"], sent["attachments"], tail=sent["tail"])
    assert outgoing.endswith(_PLAN_REQUEST_AGAIN + "Add a table of accounts.")
    assert outgoing.index("ADSL.csv") < outgoing.rindex("Add a table of accounts.")
    assert _THIS_TURN_QUESTION not in outgoing


def test_an_implement_follow_up_with_notes_ends_on_the_new_sentence(tmp_path: Path):
    orch, oc = _build(tmp_path, [
        Turn(writes={"src/App.tsx": "// table\n"}),
        Turn(writes={"src/App.tsx": "// filtered\n"}),
    ])
    _run(orch, "Add a table of accounts.", mode=Mode.IMPLEMENT)
    orch.upload_file("ADSL.csv", b"USUBJID,ARM\n01-001,Placebo\n")

    _run(orch, "Exclude placebo rows.", mode=Mode.IMPLEMENT)

    sent = oc.prompts[-1]
    outgoing = with_attachment_listing(sent["text"], sent["attachments"], tail=sent["tail"])
    assert outgoing.endswith(_THIS_TURN_QUESTION + "\n" + "Exclude placebo rows.")
    assert outgoing.index("ADSL.csv") < outgoing.rindex("Exclude placebo rows.")
