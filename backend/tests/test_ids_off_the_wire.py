"""An id that arrives from the browser names one path segment, or it names nothing.

Every id Sage stores is minted by `new_id` and could not climb out of anything. They do not stay
minted: a conversation id arrives in the body of `/api/project/build/stream`, a thread id arrives in
a URL, and both are joined onto a root and written through `mkdir(parents=True)`. A `..` segment
then walks out of the project volume and takes the write with it.

`_plan_doc_dir` has refused this since plan documents started arriving in URLs. `build_session_path`
and `ThreadStore.thread_dir` never got the same guard, and they are the two that reach `mkdir` with
a directory named off the wire. `safe_id` is that rule in one place, and these are the callers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.workspace.manager import ProjectRecord
from sage.workspace.threads import ThreadStore, safe_id

# Anything that is not one path segment. The last two matter as much as `..`: an absolute path
# ignores the root it is joined to, and a bare `/` escapes on the way into a FILENAME, which is
# where `app_id` lands rather than into a directory.
CLIMBERS = [
    "../../../../tmp/pwn",
    "..",
    "a/b",
    "/etc/passwd",
    "thr_ok/../../..",
    "",
    "x" * 65,
]


@pytest.mark.parametrize("bad", CLIMBERS)
def test_safe_id_refuses_anything_that_is_not_one_segment(bad):
    with pytest.raises(ValueError):
        safe_id(bad, "thing")


def test_safe_id_passes_the_ids_sage_actually_mints():
    from sage.workspace.threads import new_id

    for prefix in ("thr", "art", "ctx", "app"):
        minted = new_id(prefix)
        assert safe_id(minted, "thing") == minted


@pytest.mark.parametrize("bad", ["../../../../tmp/pwn", "a/b", "/etc/passwd"])
def test_a_build_session_path_refuses_a_climbing_conversation(tmp_path, bad):
    """The reported hole. `write_session_id` follows this with `mkdir(parents=True)` and
    `write_text`, so a path that escapes creates directories and writes JSON outside the volume."""
    record = ProjectRecord("proj", tmp_path)
    with pytest.raises(ValueError):
        record.build_session_path(bad)


@pytest.mark.parametrize("bad", ["../../../../tmp/pwn", "a/b"])
def test_a_build_session_path_refuses_a_climbing_app_id(tmp_path, bad):
    """`app_id` becomes part of a filename rather than a directory, which is no safer: a `/` in it
    walks out of `.sage/` exactly as `..` walks out of the volume."""
    record = ProjectRecord("proj", tmp_path)
    with pytest.raises(ValueError):
        record.build_session_path("thr_abc", app_id=bad)


def test_a_build_session_path_still_answers_for_a_real_conversation(tmp_path):
    """The guard refuses a climber and nothing else — the unscoped and scoped shapes both stand."""
    record = ProjectRecord("proj", tmp_path)
    assert record.build_session_path(None) == tmp_path / ".sage" / "session.json"
    scoped = record.build_session_path("thr_abc", app_id="app_1")
    assert scoped == tmp_path / ".sage" / "threads" / "thr_abc" / "build-session-app_1.json"


@pytest.mark.parametrize("bad", ["../../../../tmp/pwn", "a/b", "/etc/passwd"])
def test_a_thread_directory_refuses_a_climbing_id(tmp_path, bad):
    """`thread_dir` is joined and `mkdir`ed by a dozen callers in `ThreadStore`, so the guard goes
    on the one function rather than on each of them."""
    store = ThreadStore(tmp_path)
    with pytest.raises(ValueError):
        store.thread_dir(bad)
    with pytest.raises(ValueError):
        store.examples_dir(bad)


def test_a_thread_store_still_works_end_to_end(tmp_path):
    store = ThreadStore(tmp_path)
    row = store.create("A conversation")
    assert store.get(row["id"])["title"] == "A conversation"
    store.append_history(row["id"], {"role": "user", "text": "hello"})
    assert store.thread_dir(row["id"]).is_dir()


def test_the_build_stream_refuses_a_climbing_conversation_and_writes_nothing(tmp_path, monkeypatch):
    """End to end, over the wire the finding named. The refusal is an SSE `error` the composer
    already renders rather than a traceback halfway into a stream the browser has begun reading —
    and, the point of the exercise, nothing is created outside the volume."""
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    escape = tmp_path / "pwn"
    client = TestClient(appmod.control_app)
    res = client.post(
        "/api/project/build/stream",
        json={"prompt": "build me a dashboard", "conversation": f"../../../..{escape}"},
    )
    body = res.text
    assert '"type": "error"' in body or '"type":"error"' in body
    assert not escape.exists()


def test_a_plan_document_id_is_guarded_by_the_same_rule(tmp_path):
    """`_plan_doc_dir` had this guard inline first. It reads the shared one now, so the three
    callers cannot drift apart — which is how two of them came to be missing it.

    Asserted on the directory rather than on `read_plan_doc`, which answers None for a document
    that is not there and would pass whether the guard held or not."""
    record = ProjectRecord("proj", tmp_path)
    with pytest.raises(ValueError):
        record._plan_doc_dir("../../../../tmp/pwn")
    assert record._plan_doc_dir("pd_1") == tmp_path / ".sage" / "plan-docs" / "pd_1"
