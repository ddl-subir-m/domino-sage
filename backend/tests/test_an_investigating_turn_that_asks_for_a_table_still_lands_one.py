"""The combination nothing covered: an open investigation AND a request for a table (#386).

`data_artifact` normally mints `arm_chat_artifact()`, and the model writes its chart or its table
through `write_chat_artifact` — the scoped writer, which refuses any path outside
`examples/<threadId>/` and any extension but `.png` or `.table.json`.

An investigating Thread does not get that token. That is deliberate: the artifact lane is read plus
`artifact_write` and nothing else, so minting it would take the shell off a turn whose whole job is
to keep measuring. But it means the scoped writer is CLOSED on exactly this turn, and the artifact
has to arrive the other way — written straight into `examples/<threadId>/` by a turn that has a
shell, and collected by `new_artifact_paths` at turn end.

The #381 review found that path live and uncovered. Both halves are pinned here, because a reader
who meets only the first will think the artifact is lost, and a reader who meets only the second
will think the scoped writer is available.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, _orch

TABLE = json.dumps({"title": "Top candidates", "columns": ["account"], "rows": [["VLTA"]]})


def _investigating(tmp_path: Path):
    orch, oc = _orch(tmp_path, gateway=IntentGateway({"label": "data_artifact", "confidence": 0.92}))
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    # A store on the Thread, because that is what the offer requires — a Thread with only a file
    # chip is never offered an investigation, so opening one on it would set up a state the product
    # cannot reach and this whole file would be about a configuration nobody can be in.
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})
    # And a file beside it, because `data_artifact` is downgraded with no file bound and the lane
    # this is about would never open.
    path = ".sage/scratch/accounts.csv"
    bound = project.workspace.path / path
    bound.parent.mkdir(parents=True, exist_ok=True)
    bound.write_text("account,events\nVLTA,291\n")
    orch.add_thread_context(tid, {"kind": "file", "name": "accounts.csv", "path": path})
    orch.decide_thread_investigation(tid, "open")
    return orch, oc, tid


def test_the_table_lands_and_is_listed(tmp_path: Path):
    orch, oc, tid = _investigating(tmp_path)
    rel = f"examples/{tid}/top-candidates.table.json"
    oc.turns = [Turn(text="The table is at " + rel, writes={rel: TABLE})]

    events = list(orch.chat_stream(tid, "give me a table of the top candidates"))

    items = next(e for e in events if e.get("type") == "artifacts")["items"]
    assert any(a["path"] == rel and a["kind"] == "table" for a in items)
    assert (orch.project(start_preview=False).record.path / rel).exists()
    assert orch.get_thread(tid)["artifacts"][0]["path"] == rel


def test_the_scoped_writer_is_closed_on_that_turn(tmp_path: Path):
    """Not a defect to repair by minting the token anyway: the token is the bounded lane, and
    handing it to an investigating turn would take away the shell the investigation runs on."""
    orch, oc, tid = _investigating(tmp_path)
    rel = f"examples/{tid}/top-candidates.table.json"
    refused: list[str] = []

    original_send = oc.send_prompt

    def send_and_try_the_writer(*args, **kwargs):
        with pytest.raises(ValueError) as caught:
            orch.write_chat_artifact({"thread_id": tid, "path": rel, "content": TABLE,
                                      "encoding": "utf8"})
        refused.append(str(caught.value))
        original_send(*args, **kwargs)

    oc.send_prompt = send_and_try_the_writer
    oc.turns = [Turn(text="Measured first.")]

    list(orch.chat_stream(tid, "give me a table of the top candidates"))

    assert refused and "data artifact Chat turn" in refused[0]
