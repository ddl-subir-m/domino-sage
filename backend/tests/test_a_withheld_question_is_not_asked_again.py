"""Re-running a refused turn only makes sense while the question is still there.

`surviving` answers "is there anything left to answer FROM". It does not answer "is there still a
question", and those come apart in the one case that reaches a person most often: the guardrail
matches the message they typed, the file they attached survives, and `surviving` counts it.

What follows is deterministic, not unlucky. The withhold key is a SHA-256 of the message content
(`chat_paths.text_key`), so the re-run's identical text hashes to the identical key and
`apply_withheld` replaces it again — with a placeholder that instructs the model to say it cannot
see the message and answer from what is left. That is the reply on screen. And because the OpenCode
driver has no delete, no revert and no fork (ADR-0022), the re-run leaves a permanent second copy of
the exact text the gateway refuses.

So the guard is narrow on purpose. Withholding an EARLIER question, or an answer above, leaves this
turn's question standing and the re-run is worth the call.
"""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator import recall, withhold
from sage.shim.chat_paths import text_key
from sage.workspace.threads import ThreadStore

from .fake_opencode import Turn
from .test_a_refused_request_is_not_a_silent_turn import BLOCKED
from .test_a_refused_turn_says_which_file_it_was import POISON, _Guardrail
from .test_a_withhold_click_lands_on_the_build_transcript import PASTED, RAW, _click
from .test_chat_turn import _orch

ASKED = "sample 3 rows from card_panel_transactions_RAW.csv"


def _turn(question: str = ASKED) -> list[dict]:
    """A turn that read one file to answer one typed question."""
    return [
        {"role": "system", "content": "Sage's own instructions."},
        {"role": "user", "content": question},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read",
                          "arguments": '{"filePath": "/mnt/data/card_panel_RAW.csv"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "card,ssn\n4111111111111111,222-33-4444"},
    ]


def test_the_turns_own_question_is_recognised_among_the_withheld():
    """The case on screen. The gateway matched the typed message, the file survives, and re-sending
    the same words would hand the model a placeholder where its instructions used to be."""
    messages = _turn()
    question = withhold.Carrier(text_key(messages[1]), "the message you sent", False)
    assert withhold.prompt_withheld(messages, [question]) is True


def test_a_withheld_file_leaves_the_question_standing():
    """The case the guard must NOT catch, and the reason the re-run exists at all. A probe has
    already proved the payload comes back clean without the file, so the turn is worth asking
    again — it has a question and something to answer it from."""
    messages = _turn()
    read = withhold.Carrier("file:/mnt/data/card_panel_RAW.csv", "card_panel_RAW.csv", True)
    assert withhold.prompt_withheld(messages, [read]) is False


def test_an_earlier_question_can_be_withheld_and_this_one_still_asked():
    """Why the guard reads the LAST user message rather than any carrier labelled "the message you
    sent". Both messages carry that label, and stopping on either would refuse a re-run that works:
    what the gateway matched is two turns up, and what was asked here is intact."""
    messages = [{"role": "user", "content": "here is my card number 4111111111111111"},
                {"role": "assistant", "content": "I cannot help with that."},
                *_turn()[1:]]
    earlier = withhold.Carrier(text_key(messages[0]), "the message you sent", False)
    assert withhold.prompt_withheld(messages, [earlier]) is False


def _poisoned_question() -> list[dict]:
    """The screenshot's payload: what the person typed is what the gateway matched, and the file
    the turn opened is clean. `surviving` counts that file, so it alone would say "run it again"."""
    return [
        {"role": "system", "content": "You are Sage."},
        {"role": "user", "content": f"is {POISON} anywhere in the panel?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "t1", "type": "function",
             "function": {"name": "read", "arguments": '{"filePath": "clean.csv"}'}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "ticker,week\nVLTA,2026-01-02"},
    ]


def _found_row(tmp_path: Path, payload: list[dict]) -> dict:
    """The `withhold-found` row a real refused Chat turn writes for `payload`."""
    orch, _ = _orch(tmp_path, [Turn(error=BLOCKED)], gateway=_Guardrail())
    orch.project(start_preview=False).last_refused = ("gpt-5.4", payload)
    tid = orch.create_thread()["id"]
    out = list(orch.chat_stream(tid, "chart the panel spend"))
    return next(e for e in out if e["type"] == recall.FOUND)


def test_the_row_says_the_question_itself_is_what_is_being_taken_away(tmp_path: Path):
    """The fact the client cannot work out for itself. Deciding this in JS would mean a second
    implementation of `text_key`'s SHA-256 — a copy of somebody else's rule, which is the thing
    `withhold`'s own docstring refuses to keep."""
    found = _found_row(tmp_path, _poisoned_question())
    assert [c["label"] for c in found["carriers"]] == ["the message you sent"]
    assert found["surviving"] >= 1, "the clean file survives, which is why `surviving` is not enough"
    assert found["prompt"] is True


def test_a_refused_file_leaves_the_row_saying_the_question_stands(tmp_path: Path):
    """The common case keeps its re-run. Nothing about this row changes."""
    payload = [
        {"role": "system", "content": "You are Sage."},
        {"role": "user", "content": "chart the panel spend"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "t1", "type": "function",
             "function": {"name": "read", "arguments": '{"filePath": "clean.csv"}'}},
            {"id": "t2", "type": "function",
             "function": {"name": "read", "arguments": '{"filePath": "raw.csv"}'}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "ticker,week\nVLTA,2026-01-02"},
        {"role": "tool", "tool_call_id": "t2", "content": f"name,ssn\nJ Doe,{POISON}"},
    ]
    found = _found_row(tmp_path, payload)
    assert [c["label"] for c in found["carriers"]] == ["raw.csv"]
    assert found["prompt"] is False


def test_the_click_does_not_ask_the_question_again_when_it_is_the_thing_withheld(tmp_path: Path):
    """The whole point, through the door a person actually presses. `surviving` is 2 here — the
    files the turn read are untouched — so the old rule re-ran, the same words hashed to the same
    key, and the model answered the placeholder instead of the question.

    Build's harness, because it is the one that runs a real click. The decision itself is shared:
    `withholdContent` picks `again` once, before either surface's door is called.
    """
    r = _click("withhold", carrier=PASTED, surviving=2, prompt=True)
    assert [p["path"] for p in r["posted"]] == ["/project/recall/withhold"], (
        "the withhold lands, and nothing is asked again")
    assert r["posted"][0]["body"]["prompt"] is True, (
        "and the door is told, because the receipt has to still know after a reload")


def test_a_withheld_file_still_runs_the_turn_again(tmp_path: Path):
    """The re-run is not what is being removed. A probe has proved the payload comes back clean
    without the file, the question is intact, and the person gets the answer they asked for."""
    r = _click("withhold", carrier=RAW, surviving=2, prompt=False)
    assert [p["path"] for p in r["posted"]] == [
        "/project/recall/withhold", "/project/build/stream"]


def test_the_door_writes_down_that_the_question_was_what_went(tmp_path: Path):
    """The receipt is re-derived from this row, so a reload is where the sentence would be lost.
    The client knows the fact at click time — it read it to decide not to re-run — and the row is
    the only thing that still knows it tomorrow."""
    orch, _ = _orch(tmp_path, [])
    tid = orch.create_thread()["id"]
    ev = orch.withhold_content(tid, ["text:abc123"], ["the message you sent"], prompt=True)
    assert ev["prompt"] is True
    history = ThreadStore(orch.project(start_preview=False).record.path).read_history(tid)
    kept = [r for r in history if r["type"] == recall.WITHHELD]
    assert [r["prompt"] for r in kept] == [True], "a reload must still know to say it"


def test_a_withheld_file_writes_a_row_that_claims_nothing_about_the_question(tmp_path: Path):
    orch, _ = _orch(tmp_path, [])
    tid = orch.create_thread()["id"]
    assert orch.withhold_content(tid, ["file:raw.csv"], ["raw.csv"])["prompt"] is False
