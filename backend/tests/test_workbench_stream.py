"""What the Thread looks like while a Chat turn is being written.

Everything else about the Workbench JS is checked by reading it. This one is run, because the
streaming reducer decides whether the answer shows up once or twice and the failure mode is a
duplicated paragraph rather than an exception — which reading catches badly and running catches
immediately.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "chat_stream_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _turn(frames: list[dict]) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(frames), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


_ANSWER = [
    {"type": "user", "text": "q"},
    {"type": "delta", "text": "Let me "},
    {"type": "delta", "text": "look."},
    {"type": "delta", "text": "Let me look.", "final": True},
    {"type": "agent", "kind": "tool", "tool": "bash", "detail": "python p.py"},
    {"type": "delta", "text": "Rev"},
    {"type": "delta", "text": "enue rose."},
    {"type": "delta", "text": "Revenue rose.", "final": True},
    {"type": "agent", "kind": "text", "text": "Revenue rose."},
    {"type": "done", "ok": True, "decision": "answered"},
]


def test_the_answer_is_written_into_the_thread_as_it_arrives():
    steps = _turn(_ANSWER)["steps"]
    assert "~Let me " in steps          # the first fragment is on screen, not buffered
    assert "~Let me look." in steps
    # `final` closes a block, so the next fragment starts a new one rather than overwriting it.
    assert "~Let me look. | ~Rev" in steps


def test_what_streamed_is_replaced_by_the_record_of_it_not_appended_to():
    """Only the last text part reaches the transcript, so a Thread that kept its live blocks would
    show the answer twice while it ran and once after a reload. Same Thread, two appearances."""
    out = _turn(_ANSWER)
    assert out["final"] == [{"type": "text", "value": "Revenue rose."}]
    assert out["steps"][-1] == "=Revenue rose."


def test_a_turn_that_never_streams_looks_exactly_as_it_did_before():
    """A provider that does not stream, or a stream that could not be opened, must cost the Thread
    nothing: the text event at the end of the turn is the whole rendering path."""
    out = _turn([{"type": "user", "text": "q"},
                 {"type": "agent", "kind": "text", "text": "Still answered."},
                 {"type": "done", "ok": True, "decision": "answered"}])
    assert out["final"] == [{"type": "text", "value": "Still answered."}]
    assert out["steps"] == ["", "=Still answered."]


def test_a_turn_that_dies_mid_sentence_keeps_what_it_managed_to_say():
    """The timeout path sends an error and no text. What arrived is still the best account of what
    happened, and dropping it would leave the reader with a stopped turn and nothing to read."""
    out = _turn([{"type": "user", "text": "q"},
                 {"type": "delta", "text": "Reading the file"},
                 {"type": "error", "message": "This turn took too long, so it was stopped."},
                 {"type": "done", "ok": False, "decision": "timeout"}])
    assert out["final"] == [
        {"type": "text", "value": "Reading the file", "streaming": True},
        {"type": "text", "value": "This turn took too long, so it was stopped."},
    ]


def test_the_spinner_names_the_slow_work_instead_of_just_spinning():
    """The reported turn spent minutes on a Data Source query with nothing on screen, and looked
    exactly like a turn that had hung. Sage tells the agent how to reach a Data Source and how to
    read a Dataset file, so both arrive as bash — naming only the tool would have said "Running
    Python…" for all of it."""
    out = _turn([
        {"type": "user", "text": "q"},
        {"type": "agent", "kind": "tool", "tool": "bash", "doing": "read",
         "detail": "price_data.csv"},
        {"type": "agent", "kind": "tool", "doing": "idle"},
        {"type": "agent", "kind": "tool", "tool": "bash", "doing": "query",
         "detail": "BigQuery_Demo"},
        {"type": "agent", "kind": "tool", "doing": "idle"},
        {"type": "agent", "kind": "tool", "tool": "write", "doing": "write",
         "detail": "examples/thr_1/revenue.png"},
        {"type": "delta", "text": "Revenue rose.", "final": True},
        {"type": "agent", "kind": "text", "text": "Revenue rose."},
        {"type": "done", "ok": True, "decision": "answered"},
    ])
    assert out["typings"] == [
        "Thinking…",                 # before the first tool says otherwise
        "Reading price_data.csv…",
        "Thinking…",                 # the read finished; the label stops claiming it has not
        "Querying BigQuery_Demo…",
        "Thinking…",
        "Saving revenue.png…",       # the path is the server's; the file name is the reader's
    ]


def test_a_transcript_fallback_still_says_running_python():
    """When the stream is down the tool events come from the transcript, which names bash and
    nothing else. That path predates `doing` and has to keep working untouched."""
    out = _turn([{"type": "user", "text": "q"},
                 {"type": "agent", "kind": "tool", "tool": "bash", "detail": "python p.py"},
                 {"type": "agent", "kind": "text", "text": "Done."},
                 {"type": "done", "ok": True, "decision": "answered"}])
    assert "Running Python…" in out["typings"]
