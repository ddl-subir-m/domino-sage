"""What the Thread looks like while a Chat turn is being written.

Everything else about the Workbench JS is checked by reading it. This one is run, because the
streaming reducer decides whether the answer shows up once or twice and the failure mode is a
duplicated paragraph rather than an exception — which reading catches badly and running catches
immediately.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "chat_stream_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _turn(frames: list[dict], replay: list[dict] | None = None) -> dict:
    env = os.environ.copy()
    if replay is not None:
        env["SAGE_REPLAY"] = json.dumps(replay)
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(frames), check=False,
                         capture_output=True, text=True, timeout=60, env=env)
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
    assert "=Let me look. | ~Rev" in steps


def test_only_the_fragment_still_being_written_blinks():
    """The caret says "text is still arriving". `final` closes a block, but the closed block kept
    the flag that draws it — so a Chat turn that stopped to read three files left three carets
    blinking under each other for the rest of the turn. One flag was doing two jobs: `fromStream`
    marks what this stream wrote (and so what the transcript record replaces), `streaming` marks
    only the block still open."""
    steps = _turn(_ANSWER)["steps"]
    assert not any(s.startswith("~Let me look. | ~") for s in steps)
    assert "=Let me look. | ~Rev" in steps


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
    happened, and dropping it would leave the reader with a stopped turn and nothing to read.

    A status line rather than a paragraph: this is Sage reporting on the turn, not answering the
    question, and it is the shape the reload path builds from the same persisted event."""
    out = _turn([{"type": "user", "text": "q"},
                 {"type": "delta", "text": "Reading the file"},
                 {"type": "error", "message": "This turn took too long, so it was stopped."},
                 {"type": "done", "ok": False, "decision": "timeout"}])
    assert out["final"] == [
        {"type": "text", "value": "Reading the file", "fromStream": True, "streaming": True},
        {"type": "status", "ok": False,
         "value": "This turn took too long, so it was stopped."},
    ]


def test_a_stopped_turn_says_so_and_keeps_the_half_answer():
    """Pressing Stop used to end the turn with `done` alone: the spinner vanished and the Thread
    was left holding a question with no reply and no reason. The server now says it, and half an
    answer is still worth reading — a Chat stop reverts nothing."""
    out = _turn([{"type": "user", "text": "q"},
                 {"type": "delta", "text": "Reading the file"},
                 {"type": "stopped",
                  "message": "Stopped. Anything Sage had already written is kept."},
                 {"type": "done", "ok": False, "decision": "stopped"}])
    assert out["final"] == [
        {"type": "text", "value": "Reading the file", "fromStream": True, "streaming": True},
        {"type": "status", "ok": False,
         "value": "Stopped. Anything Sage had already written is kept."},
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
        {"type": "agent", "kind": "tool", "tool": "live_read_table", "doing": "analyze",
         "detail": "Warehouse"},
        {"type": "agent", "kind": "tool", "doing": "idle"},
        {"type": "agent", "kind": "tool", "tool": "live_read_files", "doing": "analyze",
         "detail": ""},
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
        "Analyzing Warehouse…",
        "Thinking…",
        "Analyzing text…",           # an analyze call that named no source
        "Thinking…",
        "Saving revenue.png…",       # the path is the server's; the file name is the reader's
    ]


def test_a_transcript_fallback_still_names_the_analysis():
    """When the stream is down the tool events come from the transcript, which names bash and
    nothing else. That path predates `doing` and has to keep working untouched."""
    out = _turn([{"type": "user", "text": "q"},
                 {"type": "agent", "kind": "tool", "tool": "bash", "detail": "python p.py"},
                 {"type": "agent", "kind": "text", "text": "Done."},
                 {"type": "done", "ok": True, "decision": "answered"}])
    assert "Running the analysis…" in out["typings"]


def test_the_thought_is_the_indicators_line_and_never_a_block():
    """The line is the latest sentence, a running step takes it over, and the step ending hands it
    back rather than falling to "Thinking…". None of it reaches the Thread. A thought an older turn
    saved still replays as its fold."""
    first, then, saved = ("I'll total the weekly sales.", "Then I'll chart it by desk.",
                          "An older turn's saved thought.")
    out = _turn([
        {"type": "user", "text": "q"},
        {"type": "narration", "text": first},
        {"type": "agent", "kind": "tool", "tool": "bash", "doing": "read", "detail": "sales.csv"},
        {"type": "agent", "kind": "tool", "doing": "idle"},
        {"type": "narration", "text": then},
        {"type": "delta", "text": "Revenue rose.", "final": True},
        {"type": "agent", "kind": "text", "text": "Revenue rose."},
        {"type": "done", "ok": True, "decision": "answered"},
    ], replay=[
        {"type": "user", "text": "q"},
        {"type": "reasoning", "text": saved},
        {"type": "agent", "kind": "text", "text": "Revenue rose."},
    ])
    assert out["typings"] == ["Thinking…", first, "Reading sales.csv…", first, then]
    assert out["final"] == [{"type": "text", "value": "Revenue rose."}]
    assert not any(first in s or then in s for s in out["steps"])
    assert out["replay"] == [
        {"type": "reasoning", "value": saved},
        {"type": "text", "value": "Revenue rose."},
    ]
