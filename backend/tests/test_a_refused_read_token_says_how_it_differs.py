"""A refused read token is logged by its shape, never by its value (#606).

A live replay refused four `live_read_query` calls in one turn while a `live_read_table` on the
same token went through, and the log said only "not this turn's". Whether the model sent the
last turn's token, wrapped this one in quotes, or garbled it cannot be told from that line.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .test_a_live_read_reaches_the_person_end_to_end import Warehouse, _call, _orch, _token


def _refusal(caplog) -> str:
    said = [r.getMessage() for r in caplog.records if "not this turn's" in r.getMessage()]
    assert said, "the refusal is logged"
    return said[-1]


def test_a_quoted_token_is_logged_as_matching_once_trimmed(tmp_path: Path, caplog):
    orch, oc = _orch(tmp_path, Warehouse())
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "show me 1 sample conversation"))
    token = _token(oc)

    with caplog.at_level(logging.INFO, logger="sage.orchestrator"):
        _call(orch, "live_read_query", {"token": f'"{token}"', "source": "x", "sql": "SELECT 1"})

    said = _refusal(caplog)
    assert "matches this turn's once trimmed" in said
    assert token not in said, "the token itself never reaches the log"


def test_last_turns_token_is_logged_as_an_earlier_turns(tmp_path: Path, caplog):
    orch, oc = _orch(tmp_path, Warehouse())
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "show me 1 sample conversation"))
    earlier = _token(oc)
    list(orch.chat_stream(tid, "and another one"))

    with caplog.at_level(logging.INFO, logger="sage.orchestrator"):
        _call(orch, "live_read_query", {"token": earlier, "source": "x", "sql": "SELECT 1"})

    said = _refusal(caplog)
    assert "an earlier turn's" in said
    assert earlier not in said


def test_a_garbled_token_says_how_much_of_it_matched(tmp_path: Path, caplog):
    orch, oc = _orch(tmp_path, Warehouse())
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "show me 1 sample conversation"))
    token = _token(oc)
    garbled = token[:10] + ("A" if token[10] != "A" else "B") + token[11:]

    with caplog.at_level(logging.INFO, logger="sage.orchestrator"):
        _call(orch, "live_read_query", {"token": garbled, "source": "x", "sql": "SELECT 1"})

    said = _refusal(caplog)
    assert f"{len(garbled)} chars" in said
    assert "first 10 match" in said
    assert garbled not in said
