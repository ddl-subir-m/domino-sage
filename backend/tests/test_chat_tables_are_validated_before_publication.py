"""#349: observe Chat output, saved history and files through the existing driver harness."""
import json

import pytest

from .fake_opencode import Turn
from .test_a_table_chat_wrote_commits_its_shape_not_its_rows import SHAPES
from .test_chat_turn import _orch


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)


def test_an_empty_table_is_repaired_before_it_is_published(tmp_path):
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    chart = f"examples/{tid}/trend.png"
    table = f"examples/{tid}/moves.table.json"
    project = orch.project(start_preview=False)
    project.record.set_kept_rows(True)
    valid = json.dumps({"columns": ["name", "total"], "rows": [["A", 42]]})
    oc.turns = [Turn(text=f"The total is 42. The table is ready: [file:{table}]",
                     writes={chart: "a chart", table: ""}),
                Turn(text="Repaired.", writes={table: valid})]

    events = []
    for event in orch.chat_stream(tid, "summarize the file"):
        events.append(event)
        if event["type"] == "artifacts":
            assert (project.record.path / table).read_text() == valid

    assert len(oc.prompts) == 2
    assert oc.prompts[0]["session"] == oc.prompts[1]["session"]
    assert oc.prompts[1]["agent"] == "sage-chat"
    assert table in oc.prompts[1]["text"]
    assert [a["path"] for e in events if e["type"] == "artifacts" for a in e["items"]] == [table, chart]
    assert len(orch.get_thread(tid)["artifacts"]) == 2
    assert next(e for e in events if e["type"] == "done")["ok"] is True


def setup_turn(tmp_path, *, kept_rows=True):
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    project = orch.project(start_preview=False)
    project.record.set_kept_rows(kept_rows)
    return orch, oc, tid, project.record.path, f"examples/{tid}/moves.table.json"


@pytest.mark.parametrize("raw", ["", " \n\t", "{broken", "null", "42", '{"sheets": {}}',
                                 '{"columns": ["value"], "rows": [[NaN]]}', None])
@pytest.mark.parametrize("kept_rows", [True, False])
def test_invalid_content_gets_one_repair_and_a_persisted_failure(tmp_path, raw, kept_rows):
    from sage import timing

    orch, oc, tid, root, table = setup_turn(tmp_path, kept_rows=kept_rows)
    chart = f"examples/{tid}/trend.png"
    oc.turns = [Turn(text=f"The total is 42. The table is ready: [file:{table}]",
                     writes={chart: "chart", **({table: raw} if raw is not None else {})}),
                Turn(text=f"The table is ready: [Download]({table}).")]

    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == 2
    assert next(e for e in events if e["type"] == "done")["ok"] is False
    for rows in (events, orch.get_thread(tid)["history"]):
        texts = " ".join(e.get("text", "") for e in rows if e["type"] == "agent")
        assert texts == ""
        assert table not in texts
        assert "table is ready" not in texts
        failures = [e for e in rows if e.get("reason") == "table generation failed"]
        # The table is named since #435: one turn can write several, and this sentence can stand
        # beside a card that worked — here, the chart it already says is ready.
        assert [e["message"] for e in failures] == [
            "The chart is ready, but I could not generate the table: moves."]
        assert [a["path"] for e in rows if e["type"] == "artifacts" for a in e["items"]] == [chart]
    assert [a["path"] for a in orch.get_thread(tid)["artifacts"]] == [chart]
    assert not (root / table).exists()
    spans = [s.fields for s in timing.last_finished().spans if s.name == "chat.table_validation"]
    assert len(spans) == 1
    assert spans[0]["path"] == table and spans[0]["repair_ran"] is True
    assert spans[0]["result"] != "valid"


@pytest.mark.parametrize("shape", list(SHAPES))
def test_supported_tables_need_no_repair(tmp_path, shape):
    orch, oc, tid, root, table = setup_turn(tmp_path)
    raw = json.dumps(SHAPES[shape][0])
    oc.turns = [Turn(text="The result.", writes={table: raw})]
    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == 1
    assert (root / table).read_text() == raw
    assert next(e for e in events if e["type"] == "done")["ok"] is True
    assert [a["path"] for a in orch.get_thread(tid)["artifacts"]] == [table]


@pytest.mark.parametrize("body", [
    {"columns": ["total"], "rows": []}, {"columns": [], "rows": []}, [],
    {"columns": [], "rows": [], "keptRows": False, "readAt": "2026-09-10"},
    {"columns": ["name"], "rows": [], "rowCount": 5, "keptRows": False},
    {"A": {"A": 1, "B": 0.5}, "B": {"A": 0.5, "B": 1}},
    {"columns": ["total"], "rows": [[42], {"total": 17}]},
])
def test_empty_results_receipts_and_labelled_pandas_tables_are_valid(tmp_path, body):
    orch, oc, tid, _, table = setup_turn(tmp_path)
    oc.turns = [Turn(text="The result.", writes={table: json.dumps(body)})]
    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == 1
    assert next(e for e in events if e["type"] == "done")["ok"] is True
    assert [a["path"] for a in orch.get_thread(tid)["artifacts"]] == [table]


def test_multiple_tables_share_one_repair_and_keep_the_successes(tmp_path):
    orch, oc, tid, root, table = setup_turn(tmp_path, kept_rows=False)
    second = f"examples/{tid}/second.table.json"
    good = f"examples/{tid}/good.table.json"
    raw = json.dumps({"columns": ["secret"], "rows": [["private-cell"]]})
    oc.turns = [Turn(text="The total is 42.", writes={table: "", second: "{bad", good: raw}),
                Turn(writes={table: raw})]
    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == 2
    assert table in oc.prompts[1]["text"] and second in oc.prompts[1]["text"]
    assert good not in oc.prompts[1]["text"]
    assert {a["path"] for a in orch.get_thread(tid)["artifacts"]} == {table, good}
    assert next(e for e in events if e["type"] == "done")["ok"] is False
    assert not (root / second).exists()
    for rel in (table, good):
        assert "private-cell" not in (root / rel).read_text()
        assert json.loads((root / rel).read_text())["keptRows"] is False


@pytest.mark.parametrize("failure", ["delete", "dispatch", "refusal", "poll"])
def test_failed_repair_keeps_valid_output_without_a_second_attempt(tmp_path, failure):
    import httpx

    orch, oc, tid, root, table = setup_turn(tmp_path)
    chart = f"examples/{tid}/trend.png"
    oc.turns = [Turn(text=f"Total: 42. [file:{table}]", writes={table: "", chart: "chart"}),
                Turn(error={"name": "APIError", "data": {"message": "request refused"}}
                     if failure == "refusal" else None)]
    send = oc.send_prompt
    def send_with_failure(*args, **kwargs):
        send(*args, **kwargs)
        if len(oc.prompts) == 2:
            if failure == "delete":
                (root / table).unlink()
            elif failure == "dispatch":
                raise httpx.ConnectError("repair transport failed")
    oc.send_prompt = send_with_failure
    running = oc.is_running
    def poll(*args):
        if failure == "poll" and len(oc.prompts) == 2:
            raise httpx.ConnectError("session unavailable")
        return running(*args)
    oc.is_running = poll

    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == 2
    assert next(e for e in events if e["type"] == "done")["ok"] is False
    assert [a["path"] for a in orch.get_thread(tid)["artifacts"]] == [chart]
    assert len([e for e in orch.get_thread(tid)["history"]
                if e.get("reason") == "table generation failed"]) == 1


@pytest.mark.parametrize("when", ["before", "during"])
@pytest.mark.parametrize("ending", ["stop", "timeout", "refusal"])
def test_stop_timeout_and_refusal_do_not_restart_work(tmp_path, monkeypatch, when, ending):
    from sage.orchestrator import service

    orch, oc, tid, root, table = setup_turn(tmp_path, kept_rows=False)
    chart = f"examples/{tid}/trend.png"
    good = f"examples/{tid}/good.table.json"
    raw = json.dumps({"columns": ["value"], "rows": [["private-cell"]]})
    target = 1 if when == "before" else 2
    error = {"name": "APIError", "data": {"message": "request refused"}}
    oc.turns = [Turn(text=f"Total: 42. [file:{table}]", writes={table: "", chart: "chart", good: raw},
                     error=error if ending == "refusal" and target == 1 else None),
                Turn(error=error if ending == "refusal" and target == 2 else None)]
    running = oc.is_running
    def end_at_idle(*args):
        result = running(*args)
        if not result and len(oc.prompts) == target:
            if ending == "stop":
                orch.project(start_preview=False).stop_requested = True
            elif ending == "timeout":
                monkeypatch.setattr(service, "_CHAT_TURN_MAX_S", -1)
        return result
    oc.is_running = end_at_idle
    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == target
    assert next(e for e in events if e["type"] == "done")["ok"] is False
    assert {a["path"] for a in orch.get_thread(tid)["artifacts"]} == {chart, good}
    assert "private-cell" not in (root / good).read_text()
    assert not orch.project(start_preview=False).stop_requested
    assert len([e for e in orch.get_thread(tid)["history"]
                if e.get("reason") == "table generation failed"]) == 1


def test_chart_only_and_old_table_files_are_left_alone(tmp_path):
    orch, oc, tid, root, old = setup_turn(tmp_path, kept_rows=False)
    path = root / old
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(" ")
    chart = f"examples/{tid}/trend.png"
    oc.turns = [Turn(text="The total is 42.", writes={chart: "chart"})]
    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == 1
    assert path.read_text() == " "
    assert next(e for e in events if e["type"] == "done")["ok"] is True
    assert [a["path"] for a in orch.get_thread(tid)["artifacts"]] == [chart]


@pytest.mark.parametrize("final_valid", [False, True])
def test_shell_completion_status_does_not_replace_file_validation(tmp_path, final_valid):
    orch, oc, tid, _, table = setup_turn(tmp_path)
    oc.turns = [Turn(text="The total is 42.", tools=["bash"], writes={table:
                    json.dumps({"columns": ["total"], "rows": [[42]]}) if final_valid else ""})]
    send = oc.send_prompt
    def failed_shell(*args, **kwargs):
        send(*args, **kwargs)
        if len(oc.prompts) == 1:
            oc._by_session[args[0]][-1]["content"][0]["state"]["metadata"] = {"exit": 1}
    oc.send_prompt = failed_shell
    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == (1 if final_valid else 2)
    assert next(e for e in events if e["type"] == "done")["ok"] is final_valid


def test_a_reference_to_an_old_blank_table_gets_one_repair_then_reports_failure(tmp_path):
    orch, oc, tid, root, table = setup_turn(tmp_path, kept_rows=False)
    path = root / table
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(" ")
    oc.turns = [Turn(text=f"Total: 42. See the table: [file:{table}]")]
    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == 2
    assert path.read_text() == " "
    assert next(e for e in events if e["type"] == "done")["ok"] is False
    assert any(e.get("reason") == "table generation failed" for e in events)


def test_a_terminal_driver_exception_keeps_the_chart_and_cleans_up_rows(tmp_path):
    orch, oc, tid, root, table = setup_turn(tmp_path, kept_rows=False)
    chart = f"examples/{tid}/trend.png"
    good = f"examples/{tid}/good.table.json"
    oc.turns = [Turn(writes={table: "", chart: "chart", good:
                            json.dumps({"columns": ["name"], "rows": [["private-cell"]]})})]
    send = oc.send_prompt
    def fail(*args, **kwargs):
        send(*args, **kwargs)
        raise RuntimeError("session failed")
    oc.send_prompt = fail
    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == 1
    assert next(e for e in events if e["type"] == "done")["ok"] is False
    assert any(e.get("reason") == "table generation failed" for e in orch.get_thread(tid)["history"])
    assert "private-cell" not in (root / good).read_text()
    assert {a["path"] for a in orch.get_thread(tid)["artifacts"]} == {chart, good}


def test_a_gateway_refusal_cannot_trigger_table_repair(tmp_path):
    orch, oc, tid, _, table = setup_turn(tmp_path)
    oc.turns = [Turn(text="The total is 42.", writes={table: ""})]
    send = oc.send_prompt
    def refused(*args, **kwargs):
        send(*args, **kwargs)
        orch.project(start_preview=False).last_gateway_error = {"message": "request refused"}
    oc.send_prompt = refused
    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == 1
    assert next(e for e in events if e["type"] == "done")["ok"] is False
    assert any(e.get("reason") == "table generation failed" for e in events)


def test_table_offers_do_not_stream_before_file_validation(tmp_path):
    from .fake_opencode import FakeOpenCode
    from .test_chat_turn import StreamingFake, _live

    class TableStream(StreamingFake):
        def is_running(self, sid):
            if len(self.prompts) > 1:
                return FakeOpenCode.is_running(self, sid)
            return super().is_running(sid)
    orch, oc = _orch(tmp_path, client=lambda ws: TableStream(ws, [], []))
    tid = orch.create_thread()["id"]
    table = f"examples/{tid}/moves.table.json"
    oc.turns = [Turn(text=f"Total: 42. The table is ready: [file:{table}]", writes={table: ""}),
                Turn(text="It is ready.")]
    oc.stream._events = [_live("message", text=oc.turns[0].text, final=True)]
    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == 2
    assert not any(e.get("type") == "delta" for e in events)
    assert "table is ready" not in " ".join(e.get("text", "") for e in events)


def test_a_failed_table_removes_unverified_prose_and_keeps_the_chart_card(tmp_path):
    orch, oc, tid, _, table = setup_turn(tmp_path)
    chart = f"examples/{tid}/trend.png"
    oc.turns = [Turn(text=f"Total: 42; chart: [file:{chart}]; table: [file:{table}]",
                     writes={chart: "chart", table: ""}), Turn()]
    events = list(orch.chat_stream(tid, "summarize the file"))
    for rows in (events, orch.get_thread(tid)["history"]):
        text = " ".join(e.get("text", "") for e in rows if e["type"] == "agent")
        assert text == ""
        assert [a["path"] for e in rows if e["type"] == "artifacts" for a in e["items"]] == [chart]


def test_a_table_changed_after_a_stream_reference_still_gets_repaired(tmp_path):
    from .fake_opencode import FakeOpenCode
    from .test_chat_turn import StreamingFake, _live

    class ChangedAfterReference(StreamingFake):
        def is_running(self, sid):
            if len(self.prompts) > 1:
                return FakeOpenCode.is_running(self, sid)
            running = super().is_running(sid)
            if not running:
                (root / table).write_text("")
            return running

    orch, oc = _orch(tmp_path, client=lambda ws: ChangedAfterReference(ws, [], []))
    tid = orch.create_thread()["id"]
    root = orch.project(start_preview=False).record.path
    table = f"examples/{tid}/moves.table.json"
    path = root / table
    path.parent.mkdir(parents=True, exist_ok=True)
    valid = json.dumps({"columns": ["total"], "rows": [[42]]})
    path.write_text(valid)
    oc.turns = [Turn(text=f"Total: 42. [file:{table}]"), Turn(writes={table: valid})]
    oc.stream._events = [_live("message", text=oc.turns[0].text, final=True)]
    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == 2
    assert next(e for e in events if e["type"] == "done")["ok"] is True


def test_repair_uses_the_original_turn_deadline(tmp_path, monkeypatch):
    from sage.orchestrator import service

    orch, oc, tid, _, table = setup_turn(tmp_path)
    clock = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    monkeypatch.setattr(service, "_CHAT_TURN_MAX_S", 10)
    oc.turns = [Turn(text="Total: 42.", writes={table: ""}),
                Turn(writes={table: json.dumps({"columns": ["total"], "rows": [[42]]})})]
    send = oc.send_prompt
    def dispatch(*args, **kwargs):
        send(*args, **kwargs)
        if len(oc.prompts) == 2:
            clock[0] = 111.0
    oc.send_prompt = dispatch
    running = oc.is_running
    def first_request_takes_nine_seconds(*args):
        result = running(*args)
        if not result and len(oc.prompts) == 1:
            clock[0] = 109.0
        return result
    oc.is_running = first_request_takes_nine_seconds
    events = list(orch.chat_stream(tid, "summarize the file"))
    assert len(oc.prompts) == 2
    done = next(e for e in events if e["type"] == "done")
    assert done["decision"] == "timeout" and done["ok"] is False


def test_a_disconnected_reader_still_saves_table_failure_and_removes_rows(tmp_path):
    orch, oc, tid, root, table = setup_turn(tmp_path, kept_rows=False)
    good = f"examples/{tid}/good.table.json"
    chart = f"examples/{tid}/trend.png"
    oc.turns = [Turn(tools=["bash"], writes={table: "", chart: "chart", good:
                    json.dumps({"columns": ["name"], "rows": [["private-cell"]]})})]
    stream = orch.chat_stream(tid, "summarize the file")
    for event in stream:
        if event.get("kind") == "tool":
            stream.close()
            break
    assert len(oc.prompts) == 1
    assert any(e.get("reason") == "table generation failed" for e in orch.get_thread(tid)["history"])
    assert "private-cell" not in (root / good).read_text()
    assert {a["path"] for a in orch.get_thread(tid)["artifacts"]} == {chart, good}


def test_failed_replacement_does_not_damage_an_earlier_turns_table(tmp_path):
    orch, oc, tid, root, table = setup_turn(tmp_path, kept_rows=False)
    chart = f"examples/{tid}/trend.png"
    oc.turns = [Turn(text="The first result.", writes={table:
                    json.dumps({"columns": ["total"], "rows": [[42]]})}),
                Turn(text=f"The new total is 84. The table is ready: [file:{table}]",
                     writes={table: "", chart: "chart"}), Turn()]
    list(orch.chat_stream(tid, "summarize the file"))
    previous = (root / table).read_bytes()
    prior_artifacts = orch.get_thread(tid)["artifacts"]

    events = list(orch.chat_stream(tid, "summarize it again"))

    assert len(oc.prompts) == 3
    assert (root / table).read_bytes() == previous
    assert [a["path"] for e in events if e["type"] == "artifacts" for a in e["items"]] == [chart]
    assert orch.get_thread(tid)["artifacts"][:len(prior_artifacts)] == prior_artifacts
    assert any(e.get("reason") == "table generation failed" for e in events)


def test_failed_table_replaces_unrestricted_success_prose(tmp_path):
    orch, oc, tid, _, table = setup_turn(tmp_path)
    chart = f"examples/{tid}/trend.png"
    claim = "Done. I found 36 customers and saved all results successfully."
    oc.turns = [Turn(text=claim, writes={table: "[local data withheld]", chart: "chart"}), Turn()]
    events = list(orch.chat_stream(tid, "summarize the file"))
    for rows in (events, orch.thread_history(tid)):
        text = " ".join(e.get("text", "") for e in rows if e.get("kind") == "text")
        assert "36 customers" not in text
        assert "successfully" not in text
        assert any(e.get("reason") == "table generation failed" for e in rows)
        assert [a["path"] for e in rows if e["type"] == "artifacts" for a in e["items"]] == [chart]


@pytest.mark.parametrize("kept_rows", [False, True])
def test_an_unpublished_failed_table_remains_repairable_on_a_later_turn(tmp_path, kept_rows):
    orch, oc, tid, root, table = setup_turn(tmp_path, kept_rows=kept_rows)
    valid = '{"columns":["total"],"rows":[[42]]}'
    oc.turns = [Turn(text="Saved successfully.", writes={table: "[local data withheld]"}), Turn(),
                Turn(text=f"The result: [file:{table}]"), Turn(writes={table: valid})]
    first = list(orch.chat_stream(tid, "summarize the file"))
    assert next(e for e in first if e["type"] == "done")["ok"] is False
    assert not (root / table).exists(), "failed bytes must not become an empty valid receipt"
    assert not orch.get_thread(tid)["artifacts"]
    second = list(orch.chat_stream(tid, "repair the failed table"))
    assert len(oc.prompts) == 4
    for rows in (second, orch.thread_history(tid)):
        assert [e for e in rows if e["type"] == "done"][-1]["ok"] is True
    assert [a["path"] for a in orch.get_thread(tid)["artifacts"]] == [table]
    assert json.loads((root / table).read_text())["rows"] == ([[42]] if kept_rows else [])


def test_an_old_invalid_table_reference_can_be_repaired(tmp_path):
    orch, oc, tid, root, table = setup_turn(tmp_path)
    path = root / table
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[local data withheld]")
    valid = '{"columns":[],"rows":[]}'
    oc.turns = [Turn(text=f"The result: [file:{table}]"), Turn(writes={table: valid})]
    events = list(orch.chat_stream(tid, "repair the failed table"))
    assert len(oc.prompts) == 2
    assert path.read_text() == valid
    assert next(e for e in events if e["type"] == "done")["ok"] is True


@pytest.mark.parametrize("prior", [False, True])
@pytest.mark.parametrize("repaired", [False, True])
def test_a_rejected_controlled_write_cannot_claim_success_without_a_file_reference(
        tmp_path, prior, repaired):
    from .test_chat_turn import IntentGateway

    orch, oc = _orch(tmp_path, gateway=IntentGateway({"label": "data_artifact", "confidence": 0.92}))
    tid = orch.create_thread()["id"]
    project = orch.project(start_preview=False)
    project.record.set_kept_rows(True)
    source = project.record.path / ".sage" / "scratch" / "totals.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("total\n42\n")
    orch.add_thread_context(tid, {"kind": "file", "name": "totals.csv",
                                  "path": ".sage/scratch/totals.csv"})
    table = f"examples/{tid}/result.table.json"
    path = project.record.path / table
    old = '{"columns":["total"],"rows":[[1]]}'
    valid = '{"columns":["total"],"rows":[[42]]}'
    if prior:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(old)
    claim = "Saved all 42 results successfully."
    oc.turns = [Turn(text=claim), Turn(), Turn(text="A later answer.")]
    send = oc.send_prompt
    def write_then_answer(*args, **kwargs):
        request = len(oc.prompts)
        if request == 0:
            with pytest.raises(ValueError, match="table"):
                orch.write_chat_artifact({"thread_id": tid, "path": f"examples/{tid}/./result.table.json",
                                         "content": "[local data withheld]"})
        elif request == 1 and repaired:
            orch.write_chat_artifact({"thread_id": tid, "path": table, "content": valid})
        send(*args, **kwargs)
    oc.send_prompt = write_then_answer

    events = list(orch.chat_stream(tid, "make the table"))
    assert len(oc.prompts) == 2
    assert table in oc.prompts[1]["text"]
    for rows in (events, orch.thread_history(tid)):
        assert next(e for e in rows if e["type"] == "done")["ok"] is repaired
        assert (claim in " ".join(e.get("text", "") for e in rows)) is repaired
        assert any(e.get("reason") == "table generation failed" for e in rows) is not repaired
    if repaired or prior:
        assert path.read_text() == (valid if repaired else old)
    else:
        assert not path.exists()

    later = list(orch.chat_stream(tid, "explain the result"))
    assert len(oc.prompts) == 3, "a rejected write belongs only to the turn that attempted it"
    assert next(e for e in later if e["type"] == "done")["ok"] is True
