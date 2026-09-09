"""A turn that dies with a `read` still open says which file, and whether that file opens.

Two unrelated failures reach the log looking identical: a Dataset mount that has stopped answering,
and a tool call the gateway cut part-way through its arguments so it never ran at all. One is
Domino's to fix and the other is Sage's. The stall itself cannot tell them apart — but the file is
right there, and opening it can.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from sage.orchestrator.service import _mount_probe, _tool_label


def test_a_long_path_keeps_the_end_that_says_which_file():
    """Clipped from the end, `/mnt/data/sage-subir-mansukhani-66a821b1-2/support_tickets.csv`
    reached the log as `…support_tickets.c`: a name that reads as a C file, names the wrong thing,
    and matches nothing anyone greps for."""
    label = _tool_label({"tool": "read", "input": {
        "filePath": "/mnt/data/sage-subir-mansukhani-66a821b1-2/support_tickets.csv"}})

    assert "support_tickets.csv" in label
    assert label.startswith("read (/mnt/data/")


def test_a_command_keeps_its_verb_instead():
    """The informative half is at the other end of a command: the verb is the first word and the
    tail is arguments, so a command clips from the end where a path clips from the middle."""
    label = _tool_label({"tool": "bash", "command": "python analyse.py " + "--flag x " * 40})

    assert label.startswith("bash (python analyse.py --flag x")


def test_a_file_that_opens_says_so_and_says_how_fast(tmp_path: Path):
    """Which is the finding, not the absence of one: a file that opens in no time means the mount
    was never the problem, and the cut stream was."""
    f = tmp_path / "support_tickets.csv"
    f.write_text("id,subject\\n1,hello\\n")

    assert "opened in" in _mount_probe(str(f))


def test_a_file_that_is_not_there_names_the_error_rather_than_timing_out(tmp_path: Path):
    assert "FileNotFoundError" in _mount_probe(str(tmp_path / "gone.csv"))


def test_an_open_that_blocks_does_not_block_the_turn_reporting_it(tmp_path: Path):
    """A read stuck in the kernel does not come back for a signal either, so the only way to time
    one out is to stop waiting for it. A diagnostic that hung the turn it was meant to explain would
    be worse than the silence it replaces.

    A FIFO with no writer blocks on open exactly the way an unresponsive mount does.
    """
    fifo = tmp_path / "stalled"
    os.mkfifo(fifo)
    started = time.monotonic()
    try:
        said = _mount_probe(str(fifo), timeout_s=1.0)
        assert said == "did not answer in 1s"
        assert time.monotonic() - started < 3.0
    finally:
        # Release the thread still parked in `open`, which a writer arriving unblocks.
        open(fifo, "wb").close()
