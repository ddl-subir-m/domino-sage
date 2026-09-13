"""A refused turn that read ONE file has nothing left to answer from, and the row has to say so.

`surviving` is read by two places that both make a promise with it: the card says "Nothing else
this turn read is affected" and offers to carry on, and `store.withholdContent` re-runs the failed
turn. Counted over CARRIERS, a one-file conversation answered 2 — the person's question and Sage's
earlier answer — so both promises were made over a turn whose only data was going away. The file
was everything it read; the re-run spent a whole turn arriving at "I cannot read that".

So the count is over DATA. Prose is not something a turn answers FROM, and a conversation that
read no files at all falls back to counting carriers, because there the prose IS the material.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator import recall

from .fake_opencode import Turn
from .test_a_refused_request_is_not_a_silent_turn import BLOCKED
from .test_a_refused_turn_says_which_file_it_was import POISON, _Guardrail
from .test_chat_turn import _orch

_HARNESS = Path(__file__).resolve().parent / "js" / "build_withhold_card_harness.mjs"


def _read(cid: str, path: str, body: str) -> list[dict]:
    return [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": "read", "arguments": json.dumps({"filePath": path})}}]},
        {"role": "tool", "tool_call_id": cid, "content": body},
    ]


def _one_file() -> list[dict]:
    """The payload off the screenshot in #288: one attached file, and prose around it."""
    return [
        {"role": "system", "content": "You are Sage."},
        {"role": "user", "content": "chart the panel spend"},
        {"role": "assistant", "content": "Reading the file you attached."},
        *_read("t1", "transactions.csv", f"name,ssn\nJ Doe,{POISON}"),
    ]


def _found_row(tmp_path: Path, payload: list[dict]) -> dict:
    """The `withhold-found` row a real refused Chat turn writes for `payload`."""
    orch, _ = _orch(tmp_path, [Turn(error=BLOCKED)], gateway=_Guardrail())
    orch.project(start_preview=False).last_refused = ("gpt-5.4", payload)
    tid = orch.create_thread()["id"]
    out = list(orch.chat_stream(tid, "chart the panel spend"))
    return next(e for e in out if e["type"] == recall.FOUND)


def test_the_only_file_a_turn_read_leaves_nothing_to_carry_on_with(tmp_path: Path):
    """#288. Three carriers, one of them the file, and the two survivors are both prose."""
    found = _found_row(tmp_path, _one_file())
    assert [c["label"] for c in found["carriers"]] == ["transactions.csv"]
    assert found["surviving"] == 0, "the file was everything this turn read"


def test_a_second_file_still_leaves_something_to_carry_on_with(tmp_path: Path):
    """The neighbour the fix must not take down. Another file answers, so the turn is worth re-running."""
    payload = [*_one_file()[:3], *_read("t1", "clean.csv", "ticker,week\nVLTA,2026-01-02"),
               *_read("t2", "raw.csv", f"name,ssn\nJ Doe,{POISON}")]
    found = _found_row(tmp_path, payload)
    assert [c["label"] for c in found["carriers"]] == ["raw.csv"]
    assert found["surviving"] == 1, "clean.csv still answers the question"


def test_a_refused_question_over_a_clean_file_still_has_something_to_answer_from(tmp_path: Path):
    """The case `prompt_withheld` exists for, and the common one: the guardrail matched what the
    person typed and every file the turn read survives. Counting files must not read that as zero —
    the file is still there to answer from, and the re-run is stopped by `prompt`, not by this."""
    payload = [{"role": "system", "content": "You are Sage."},
               {"role": "user", "content": f"is {POISON} anywhere in the panel?"},
               *_read("t1", "clean.csv", "ticker,week\nVLTA,2026-01-02")]
    found = _found_row(tmp_path, payload)
    assert [c["label"] for c in found["carriers"]] == ["the message you sent"]
    assert found["surviving"] == 1, "the clean file is still there to answer from"
    assert found["prompt"] is True, "and the question going is what stops the re-run"


def _cat(cid: str, path: str, body: str) -> list[dict]:
    """The same file, read the way `read_path_from_tool_call` cannot name — `bash cat`.

    It is data all the same, and the payload says so by its role. Only the CARD needs a filename;
    the count needs to know material from prose.
    """
    return [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": "bash", "arguments": json.dumps({"command": f"cat {path}"})}}]},
        {"role": "tool", "tool_call_id": cid, "content": body},
    ]


def test_data_a_tool_read_without_a_filename_still_counts_as_data(tmp_path: Path):
    """`is_file` needs a path to put on the card and only `read`-shaped calls carry one. Counting
    files alone would tell a conversation whose only rows were `cat`'d to carry on over nothing —
    the same waste as #288, one tool name away."""
    payload = [*_one_file()[:3], *_cat("t1", "transactions.csv", f"name,ssn\nJ Doe,{POISON}")]
    found = _found_row(tmp_path, payload)
    assert [c["label"] for c in found["carriers"]] == ["something a tool read"]
    assert found["surviving"] == 0, "the cat'd rows were everything this turn read"


def test_unnamed_data_that_survives_keeps_the_re_run(tmp_path: Path):
    """The mirror, and the one a files-only count gets backwards: the `read` file is refused and a
    `cat` of a clean one is still there to answer from, so the turn is worth running again."""
    payload = [*_one_file()[:3], *_cat("t1", "clean.csv", "ticker,week\nVLTA,2026-01-02"),
               *_read("t2", "raw.csv", f"name,ssn\nJ Doe,{POISON}")]
    found = _found_row(tmp_path, payload)
    assert [c["label"] for c in found["carriers"]] == ["raw.csv"]
    assert found["surviving"] == 1, "clean.csv is still there, however it was read"


def test_a_conversation_that_read_no_file_keeps_counting_prose(tmp_path: Path):
    """Nothing was attached, so the prose IS the material. An earlier answer refused leaves the
    person's question standing, and re-running it is worth the call."""
    payload = [{"role": "system", "content": "You are Sage."},
               {"role": "user", "content": "what did you just say?"},
               {"role": "assistant", "content": f"The row reads J Doe,{POISON}."}]
    found = _found_row(tmp_path, payload)
    assert [c["label"] for c in found["carriers"]] == ["an earlier answer in this conversation"]
    assert found["surviving"] == 1, "the question stands and there are no files to count"


# ---- the two readers -----------------------------------------------------------------------------
# The node gate goes on these four by hand, NOT in a `pytestmark`. A module-level mark is read off
# the namespace after the whole file has run, so its position means nothing — one declared here
# would skip the seven counting tests above as well, and on a machine without node the whole of
# #288's regression cover would disappear without a single failure to say so.

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

RAW = {"key": "file:/mnt/data/transactions.csv", "label": "transactions.csv", "is_file": True}


def _click(act: str) -> dict:
    events = [
        {"type": "error", "message": 'Blocked by guardrail: "Block PII"'},
        {"type": recall.SEARCH},
        {"type": recall.FOUND, "carriers": [RAW], "complete": True,
         "surviving": 0, "prompt": False, "stopped": ""},
        {"type": "done", "ok": False, "decision": "gateway error"},
    ]
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"history": [], "events": events, "act": act}),
        capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@_needs_node
def test_withholding_the_only_file_starts_no_second_turn():
    """The other half of the waste. The count now says zero, and the rule that reads it was always
    right — this is the assertion that the two are wired to each other."""
    r = _click("withhold")
    assert [p["path"] for p in r["posted"]] == ["/project/recall/withhold"]


def _said(carriers: list[dict]) -> str:
    card = Path(__file__).resolve().parent / "js" / "withhold_card_harness.mjs"
    block = {"type": "withhold", "searching": False, "carriers": carriers, "complete": True,
             "surviving": 0, "stopped": "", "surface": "chat", "live": True}
    out = subprocess.run(["node", str(card)], input=json.dumps({"block": block}),
                         capture_output=True, text=True, check=True)
    return " ".join(n["text"] for n in json.loads(out.stdout)["nodes"] if n["text"])


@_needs_node
def test_the_card_says_what_stopping_the_only_file_buys():
    """A button with no stated benefit is one people do not press. The conversation is the thing
    being repaired, not the turn — ADR-0022's premise is that the refusal outlives the turn."""
    said = _said([RAW])
    assert "Stop sending it and this conversation will work again" in said
    assert "attach a different file" in said


@_needs_node
def test_the_sentence_and_the_button_use_the_same_word_for_the_files():
    """One file or several, the sentence has to read as the button beneath it."""
    both = [RAW, {"key": "file:/mnt/data/export.csv", "label": "export.csv", "is_file": True}]
    assert "Stop sending them and this conversation will work again" in _said(both)


@_needs_node
def test_a_conversation_with_no_file_is_not_told_to_attach_a_different_one():
    """Same branch, no file anywhere: two matched messages and nothing attached. The advice has to
    stop at the part that is true."""
    two_texts = [{"key": "text:a", "label": "the message you sent", "is_file": False},
                 {"key": "text:b", "label": "an earlier answer in this conversation",
                  "is_file": False}]
    said = _said(two_texts)
    assert "this conversation will work again — ask something else." in said
    assert "attach a different file" not in said
