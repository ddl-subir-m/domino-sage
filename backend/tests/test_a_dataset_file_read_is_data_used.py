"""A Dataset file the turn read with OpenCode's own `read` is in the reply's "Data used" (#688).

`source_kind: "file"` was written only by the live-read tools, so a turn that answered from
`battlecards.md` through `read` showed no file at all. `DataUse.prepare` sees every tool result
leaving Sage and already resolves a read's path, so that is where the read is noticed. It sees the
CUMULATIVE history on every request, which is why each case that records also sends twice.
"""

import json

import pytest

from sage.liveread.data_use import DataUse
from sage.shim.chat_paths import withheld_result

from .test_a_live_read_reaches_the_person_end_to_end import Warehouse, _orch

FILE = ".sage/scratch/datasets/sales-playbooks/battlecards.md"
SOURCE = "sales-playbooks/battlecards.md"
TEXT = "1: # Battlecards\n2: Competitor A undercuts on price in EMEA.\n"


def watched():
    data, journal = DataUse(), []
    data.watch_file_reads({FILE: SOURCE}, journal.append, "turn1")
    return data, journal


def read_request(path, content, cid="call1"):
    return {"messages": [
        {"role": "user", "content": "what do the battlecards say?"},
        {"role": "assistant", "tool_calls": [{"id": cid, "type": "function", "function": {
            "name": "read", "arguments": json.dumps({"filePath": path})}}]},
        {"role": "tool", "tool_call_id": cid, "content": content},
    ]}


def rows(journal):
    return [event for row in journal if row.get("type") == "data_used" for event in row["dataUsed"]]


def test_a_read_of_an_attached_dataset_file_records_one_file_row():
    data, journal = watched()

    data.prepare(read_request("/work/chat/" + FILE, TEXT))

    [event] = rows(journal)
    assert event["source_kind"] == "file"
    assert event["operation"] == "document_reference"
    assert event["source"] == SOURCE
    assert event["status"] == "prepared"
    assert event["turn_id"] == "turn1"
    assert data.events("turn1") == [event]
    assert "Competitor A" not in json.dumps(event), "the row names the file, never its text"


def test_the_same_history_sent_twice_is_still_one_row():
    data, journal = watched()
    request = read_request("/work/chat/" + FILE, TEXT)

    data.prepare(request)
    data.prepare(request)

    assert len(rows(journal)) == 1


def test_a_read_of_an_app_source_file_records_nothing():
    data, journal = watched()

    data.prepare(read_request("/work/app/src/App.tsx", "1: export default function App() {}\n"))

    assert rows(journal) == []


def test_a_withheld_read_is_recorded_without_content():
    data, journal = watched()
    path = "/work/chat/" + FILE

    data.prepare(read_request(path, withheld_result(path)))

    [event] = rows(journal)
    assert event["source"] == SOURCE
    assert event["status"] == "withheld"
    assert "Competitor A" not in json.dumps(journal)


def test_recording_the_read_does_not_withhold_the_file_from_the_model():
    """`record` remembers an event's source, and `prepare` turns a read of a remembered source into
    a receipt. A row about a read must not take the read's content away from the next request."""
    data, _journal = watched()
    request = read_request("/work/chat/" + FILE, TEXT)

    data.prepare(request)
    prepared, _used = data.prepare(request)

    assert prepared["messages"][2]["content"] == TEXT


@pytest.mark.parametrize("mode", ["chat", "build"])
def test_the_turn_hands_its_attached_dataset_files_to_data_use(tmp_path, mode):
    orch, _ = _orch(tmp_path, Warehouse())
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    if mode == "chat":
        orch.add_thread_context(tid, {
            "kind": "file", "name": "battlecards.md", "path": FILE,
            "datasetId": "ds_play", "datasetRelPath": "battlecards.md",
            "datasetName": "sales-playbooks"})
        control_token = project.control.arm_chat(tid)
        path = str(project.record.path / FILE)
    else:
        project.attached.append({
            "dataset_id": "ds_play", "dataset": "sales-playbooks", "file": "battlecards.md",
            "path": "public/data/sales-playbooks/battlecards.md", "size": 1, "source": "dataset",
            "dataset_rel_path": "battlecards.md"})
        project.build_conversation = tid
        control_token = None
        path = str(project.workspace.path / "public/data/sales-playbooks/battlecards.md")
    try:
        orch._mint_live_read_token(tid)
        project.shim.data_use.prepare(read_request(path, TEXT))
        history = (orch.thread_history(tid) if mode == "chat"
                   else project.workspace.read_history(tid))
    finally:
        if control_token:
            project.control.disarm_chat(control_token)

    [event] = [e for row in history for e in row.get("dataUsed", [])]
    assert event["source"] == SOURCE
    assert event["source_kind"] == "file"
