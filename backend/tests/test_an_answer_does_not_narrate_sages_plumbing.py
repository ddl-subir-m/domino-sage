"""A tool's refusal never opens or appears in a Chat answer: Sage's plumbing is not narrated (#733).

Demo rerun on `e9bfaea`, prompt 2: the answer opened "I cannot write to the findings file through
artifact_write, but I can provide a summary." The pinned prompt already says never to mention tools,
and the model said it anyway, so the guarantee is at publish: a sentence that names one of Sage's
tools, or the findings file, is not the person's answer and does not ship. A name the person typed
themselves is theirs to ask about, and stays.
"""

from __future__ import annotations

import re

import pytest

from sage.orchestrator.plumbing import unnarrated
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn
from .test_chat_turn import IntentGateway, _orch

PROMPT_2 = ("I cannot write to the findings file through artifact_write, but I can provide a "
            "summary.\n\nBuild In-House and Lakehouse come up most often on calls; the table "
            "has every competitor.")
SUMMARY = ("Build In-House and Lakehouse come up most often on calls; the table has every "
           "competitor.")


def test_the_prompt_2_opening_is_dropped_and_the_answer_kept():
    assert unnarrated(PROMPT_2, "Which competitors come up on Gong calls?") == SUMMARY


@pytest.mark.parametrize("sentence", [
    "The live_read_query call returned four rows.",
    "I used sage-live-read_live_read_table to look.",
    "delegated_model_call was refused, so I classified nothing.",
    "I saved the measurements to findings.md.",
])
def test_a_sentence_naming_a_sage_tool_or_the_findings_file_is_dropped(sentence):
    said = f"Lakehouse leads. {sentence} Cloud ML is third."
    assert unnarrated(said, "Who leads?") == "Lakehouse leads. Cloud ML is third."


def test_the_words_the_person_typed_are_theirs():
    said = "artifact_write writes a PNG or a table under the conversation's folder."
    assert unnarrated(said, "What does artifact_write do?") == said


def test_an_answer_with_no_plumbing_is_untouched_to_the_byte():
    said = ("## Competitors\n\n- Build In-House: most calls.\n- Lakehouse: second.\n\n"
            "Key findings: two competitors dominate.")
    assert unnarrated(said, "Which competitors?") == said


def test_code_is_left_as_it_was_written():
    said = "Run this:\n\n```sql\nSELECT live_read_query FROM t\n```"
    assert unnarrated(said, "q") == said


# --- the turn -------------------------------------------------------------------------------------

class _RefusedOpenCode(FakeOpenCode):
    """Tries to keep findings through `artifact_write`, as prompt 2's model did, and is refused."""

    orch = None
    refused = ""

    def send_prompt(self, session_id, text, model=None, agent=None, attachments=None, chat=False):
        m = re.search(r"Thread id: (\S+)", text)
        if m and self.orch is not None and not self.refused:
            tid = m.group(1)
            try:
                self.orch.write_chat_artifact({"thread_id": tid,
                                               "path": f".sage/threads/{tid}/findings.md",
                                               "content": "# Findings", "encoding": "utf8"})
            except ValueError as e:
                self.refused = str(e)
        return super().send_prompt(session_id, text, model, agent, attachments, chat)


@pytest.mark.parametrize("label", ["data_answer", "data_artifact"])
def test_the_prompt_2_shape_reaches_the_person_without_the_refusal(tmp_path, label):
    orch, oc = _orch(tmp_path, [Turn(text=PROMPT_2)],
                     gateway=IntentGateway({"label": label, "confidence": 0.92}))
    oc.__class__ = _RefusedOpenCode
    oc.orch = orch
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "Which competitors come up most on Gong calls?"))

    assert oc.refused, "the premise: the write was refused"
    shown = [e["text"] for e in events if e.get("type") == "agent" and e.get("kind") == "text"][-1]
    saved = [e["text"] for e in ThreadStore(orch.project(start_preview=False).record.path)
             .read_history(tid) if e.get("type") == "agent" and e.get("kind") == "text"][-1]
    assert shown == saved == SUMMARY
    for word in ("artifact_write", "findings", "cannot write", oc.refused):
        assert word not in shown


def test_an_answer_that_was_only_plumbing_still_replaces_what_streamed(tmp_path):
    """The final text event is what replaces the streamed prose on screen, so it is owed even
    when nothing is left: otherwise the refusal stays up until a reload."""
    orch, _oc = _orch(tmp_path, [Turn(text="I cannot write the findings file through artifact_write.")],
                      gateway=IntentGateway({"label": "data_answer", "confidence": 0.92}))
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "Which competitors come up most on Gong calls?"))

    texts = [e["text"] for e in events if e.get("type") == "agent" and e.get("kind") == "text"]
    assert texts and texts[-1] == ""
