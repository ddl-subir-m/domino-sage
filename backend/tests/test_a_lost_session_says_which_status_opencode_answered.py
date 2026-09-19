"""#434: a 404 and a 503 leave identical evidence, and they need opposite fixes.

`OpenCodeClient.messages` ends in `raise_for_status`, which raises the SAME `httpx.HTTPStatusError`
for every 4xx and every 5xx. Both session-recovery sites catch it bare and read no code, so:

- **404** — OpenCode genuinely does not know this session. Minting a new one is right, and #433 is
  the case that must keep working.
- **500 / 502 / 503 / 429** — OpenCode could not answer *just now*. Minting is wrong, and it is
  worse than wrong: the mint calls `write_session_id`, which OVERWRITES the good id on disk. One
  transient blip detaches a live Conversation from its session for good, and the model starts empty
  on a Thread whose transcript still shows every prior turn.

Nothing recorded which of those had happened, so the two surviving explanations for #427 could not
be told apart from any log this codebase produced. These tests pin the discriminator.

THEY PIN THE LOG, NOT A POLICY. Both sites still mint on every status, deliberately — which of them
may mint is a separate decision, and this line is the measurement that has to come first. The
parametrize below asserts that a 503 is REPORTED, not that it is survived; when someone makes 5xx
stop minting, `test_every_status_still_mints_today` is the test that should red and be rewritten,
and it says so in its own name.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from sage.workspace.threads import ThreadStore

from .test_chat_turn import _orch

# 404 is absence. The rest are "ask me again" and are the reason this ticket exists — a blind catch
# reads all four as the first.
STATUSES = [404, 429, 500, 502, 503]


def _raises(status: int):
    def messages(*_a, **_k):
        request = httpx.Request("GET", "http://127.0.0.1:1/session/s/message")
        response = httpx.Response(status, request=request)
        raise httpx.HTTPStatusError(f"{status}", request=request, response=response)
    return messages


def _chat_session(tmp_path: Path):
    """A Thread whose session id is on disk against the directory the reader will compute.

    Made by asking for it once with a working client rather than by hand: the stored `directory`
    has to equal what `_ensure_thread_session` recomputes, or the reader skips the try entirely and
    every assertion below would pass without the catch being reached at all.
    """
    orch, oc = _orch(tmp_path)
    project = orch.project(start_preview=False)
    store = ThreadStore(project.record.path)
    tid = orch.create_thread()["id"]
    first = orch._ensure_thread_session(store, tid, project, oc)
    assert first, "setup did not produce a session id"
    assert store.read_session(tid).get("session_id") == first
    return orch, oc, project, store, tid, first


@pytest.mark.parametrize("status", STATUSES)
def test_the_chat_rail_logs_the_status_opencode_answered(
        tmp_path: Path, caplog: pytest.LogCaptureFixture, status: int):
    orch, oc, project, store, tid, first = _chat_session(tmp_path)
    oc.messages = _raises(status)  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO, logger="sage.orchestrator"):
        again = orch._ensure_thread_session(store, tid, project, oc)

    assert str(status) in caplog.text, caplog.text
    assert first in caplog.text, "the id that was given up on is what makes the line traceable"
    assert again != first


@pytest.mark.parametrize("status", STATUSES)
def test_the_build_rail_logs_it_too(
        tmp_path: Path, caplog: pytest.LogCaptureFixture, status: int):
    """`_recover_session` looks safer than the Chat site because it returns rather than assigns.
    It is not: its only caller mints on `None` and writes."""
    orch, oc = _orch(tmp_path)
    record = orch.project(start_preview=False).record
    record.write_session_id("ses_build_1", "conv_1", "")
    oc.messages = _raises(status)  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO, logger="sage.orchestrator"):
        recovered = orch._recover_session(record, oc, "conv_1", "")

    assert recovered is None
    assert str(status) in caplog.text, caplog.text
    assert "ses_build_1" in caplog.text


def test_every_status_still_mints_today(tmp_path: Path):
    """THE POLICY, pinned so that changing it is deliberate rather than incidental.

    Today a 503 mints exactly as a 404 does, and that is the defect #434 describes rather than a
    behaviour worth keeping. This ticket ships the measurement only. WHOEVER MAKES 5xx STOP MINTING:
    this test is supposed to red, and the right move is to rewrite it, not to widen it.
    """
    for status in STATUSES:
        orch, oc, project, store, tid, first = _chat_session(tmp_path / f"s{status}")
        oc.messages = _raises(status)  # type: ignore[method-assign]
        again = orch._ensure_thread_session(store, tid, project, oc)
        assert again != first, status
        assert store.read_session(tid).get("session_id") == again, (
            f"{status}: the stored id was overwritten, which is the destructive half")


def test_a_read_timeout_is_not_caught_here(tmp_path: Path):
    """The boundary, and a warning for whoever widens the catch (#434's own note).

    `httpx.ReadTimeout` is a `TransportError`, not an `HTTPStatusError`, so it is not caught at
    either site and propagates — the turn fails rather than silently re-homing the Conversation,
    which is the SAFER of the two outcomes. Widening the catch to `httpx.HTTPError` to "handle
    timeouts too" would quietly convert that failure into the overwrite this ticket is about.
    """
    assert not issubclass(httpx.ReadTimeout, httpx.HTTPStatusError)
    orch, oc, project, store, tid, first = _chat_session(tmp_path)

    def timeout(*_a, **_k):
        raise httpx.ReadTimeout("too slow")

    oc.messages = timeout  # type: ignore[method-assign]
    with pytest.raises(httpx.ReadTimeout):
        orch._ensure_thread_session(store, tid, project, oc)
    assert store.read_session(tid).get("session_id") == first, "nothing was overwritten"
