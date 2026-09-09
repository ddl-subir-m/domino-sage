"""ADR-0041 end to end: the token reaches the prompt, and the call reaches the card.

The pieces are tested apart elsewhere. This holds the wiring between them — that a turn mints a
token into its own prompt, that an MCP call carrying that token resolves to that Conversation's
grant, and that the rows land in an Artifact while the assistant is told only their shape.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import ClassVar

from sage.orchestrator.service import Orchestrator
from sage.resources.provider import DataSource, FakeResourceProvider, SampleRows
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn
from .test_chat_turn import OkFeedback, ScriptedGateway, _catalog


class Warehouse(FakeResourceProvider):
    def __init__(self):
        super().__init__()
        self.data_sources = [DataSource(id="ds1", name="Snowflake-Data-Warehouse", connector="Snowflake",
                       credential_type="Individual")]
        self.asked: list[tuple] = []

    def sample_rows(self, source, database, schema, table, limit=5):
        self.asked.append((source.name, database, schema, table, limit))
        return SampleRows(table, ["ID", "TITLE"], [[7, "Acme <> Domino"]][:limit])


def _orch(tmp: Path, resources):
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, [Turn(text="ok")])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=resources)
    orch.project(start_preview=False)
    return orch, oc


def _token(oc) -> str:
    m = re.search(r"Read token: (lrt_[A-Za-z0-9_-]+)", oc.prompts[-1]["text"])
    assert m, f"no token in the turn prompt:\n{oc.prompts[-1]['text'][:400]}"
    return m.group(1)


def _call(orch, tool, args):
    reply = orch.live_read_call({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    })
    return reply["result"]["content"][0]["text"]


def test_the_turn_mints_a_token_into_its_own_prompt(tmp_path: Path):
    orch, oc = _orch(tmp_path, Warehouse())
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "show me 1 sample conversation"))

    assert _token(oc).startswith("lrt_")
    # The name the model is actually offered. Custom tools carry no server prefix, and the MCP one
    # never reached a turn — see test_the_live_read_tools_are_named_the_same_either_way.
    assert "live_read_table" in oc.prompts[-1]["text"], "and tells the agent what it is for"


def test_a_chip_in_this_conversation_is_what_the_read_goes_through(tmp_path: Path):
    resources = Warehouse()
    orch, oc = _orch(tmp_path, resources)
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})
    list(orch.chat_stream(tid, "show me 1 sample conversation"))

    said = _call(orch, "live_read_table", {
        "token": _token(oc), "source": "Snowflake-Data-Warehouse",
        "database": "DWH", "schema": "MARTS", "table": "GONG__CALLS", "limit": 1,
    })

    assert resources.asked == [("Snowflake-Data-Warehouse", "DWH", "MARTS", "GONG__CALLS", 1)]
    assert "Columns: ID, TITLE" in said
    assert "NOT been shown the values" in said
    assert "Acme" not in said, "the row must not reach the assistant"

    assert f"examples/{tid}/gong-calls.table.json" in said, "the receipt names where it landed"
    card = json.loads((orch.project(start_preview=False).record.path / "examples" / tid
                       / "gong-calls.table.json").read_text())
    assert card["rows"] == [[7, "Acme <> Domino"]], "and it must reach the person"


def test_a_store_this_conversation_never_named_is_refused(tmp_path: Path):
    resources = Warehouse()
    orch, oc = _orch(tmp_path, resources)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "show me a row"))

    said = _call(orch, "live_read_table", {
        "token": _token(oc), "source": "Snowflake-Data-Warehouse", "table": "GONG__CALLS",
    })
    assert "Use it in this conversation" in said
    assert resources.asked == [], "and the store is never touched"


def test_a_token_from_no_turn_at_all_reads_nothing(tmp_path: Path):
    resources = Warehouse()
    orch, _ = _orch(tmp_path, resources)
    said = _call(orch, "live_read_table", {"token": "lrt_invented", "source": "x", "table": "y"})
    assert "not current" in said
    assert resources.asked == []


def test_the_tools_are_offered_over_the_wire(tmp_path: Path):
    orch, _ = _orch(tmp_path, Warehouse())
    listed = orch.live_read_call({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert {t["name"] for t in listed["result"]["tools"]} == {"live_read_table", "live_read_files"}


def test_a_connection_and_a_read_both_say_so_in_the_log(tmp_path: Path, caplog):
    """The absence of these lines is the finding, which is why they exist.

    This path was silent end to end, so a Thread where no Live read happened could not be told
    apart from one where OpenCode never connected to the server that offers the tools — and
    `/api/diag` cannot settle it either, because the probe it runs is ours rather than OpenCode's.
    Two hypotheses, one piece of evidence: nothing.
    """
    import logging

    orch, _ = _orch(tmp_path, Warehouse())
    with caplog.at_level(logging.INFO, logger="sage.orchestrator"):
        orch.live_read_call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        _call(orch, "live_read_table", {"token": "lrt_invented", "source": "x", "table": "y"})

    said = [r.getMessage() for r in caplog.records]
    assert "live read: OpenCode connected — initialize" in said
    # The refused call is named too. A turn that moved on and a store nobody granted read the same
    # from the Thread — silence — and only the first is nothing to worry about.
    assert any("live read: live_read_table" in m and "not this turn's" in m for m in said)


def test_the_diag_probe_does_not_pass_itself_off_as_opencode(tmp_path: Path, caplog):
    """`/api/diag` reaches the same route and asks the same `tools/list` OpenCode asks on connect.

    Unnamed, opening the diagnostics page would WRITE the evidence the page exists to go looking
    for: read the log afterwards and OpenCode looks connected whether or not it ever was. A
    diagnostic that manufactures its own finding is worse than none.
    """
    import logging

    orch, _ = _orch(tmp_path, Warehouse())
    with caplog.at_level(logging.INFO, logger="sage.orchestrator"):
        orch.live_read_call({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, probe=True)

    said = [r.getMessage() for r in caplog.records]
    assert any("a /api/diag probe, not OpenCode" in m for m in said)
    assert not any("OpenCode connected" in m for m in said)


def test_the_log_never_carries_the_token_or_a_row(tmp_path: Path, caplog):
    """A token is a turn's authority and the rows are the person's data. The log ring is served by
    `/api/diag/log` to anyone who can open the Builder, so neither belongs in it — the tool name
    and whether the token resolved are the whole diagnosis."""
    import logging

    orch, _ = _orch(tmp_path, Warehouse())
    with caplog.at_level(logging.INFO, logger="sage.orchestrator"):
        _call(orch, "live_read_table", {"token": "lrt_secret_value", "source": "x", "table": "y"})

    said = " | ".join(r.getMessage() for r in caplog.records)
    assert "lrt_secret_value" not in said
    assert "Acme <> Domino" not in said


class ReadingOpenCode(FakeOpenCode):
    """An agent that does what the turn prompt tells it to: relays the token and reads.

    The whole loop, in the only place it can be driven from a test — the Artifact this writes has
    to be picked up by the same end-of-turn scan that finds one the agent wrote itself.
    """

    orch = None
    args: ClassVar[dict] = {}

    def send_prompt(self, session_id, text, model=None, agent=None, attachments=None, chat=False):
        m = re.search(r"Read token: (lrt_[A-Za-z0-9_-]+)", text)
        if m and self.orch is not None:
            self.said = _call(self.orch, "live_read_table", {"token": m.group(1), **self.args})
        return super().send_prompt(session_id, text, model, agent, attachments, chat)


def test_the_card_a_live_read_wrote_is_on_the_conversation_when_the_turn_ends(tmp_path: Path):
    resources = Warehouse()
    orch, oc = _orch(tmp_path, resources)
    oc.__class__ = ReadingOpenCode
    oc.args = {"source": "Snowflake-Data-Warehouse", "database": "DWH", "schema": "MARTS",
               "table": "GONG__CALLS", "limit": 1}
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})
    oc.orch = orch

    list(orch.chat_stream(tid, "show me 1 sample conversation"))

    # Written mid-turn by the tool, not by the agent — and found by the same scan either way, so it
    # reaches the transcript as an ordinary Artifact with no second path to maintain.
    store = ThreadStore(orch.project(start_preview=False).record.path)
    paths = [a["path"] for a in store.read_artifacts(tid)]
    assert f"examples/{tid}/gong-calls.table.json" in paths


def _build_orch(tmp: Path, resources):
    """A Project where a Build turn actually runs, unlike the Chat harness above."""
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text('{"name": "template"}')
    (template / "AGENTS.md").write_text("# Building an app\n")
    oc = FakeOpenCode(tmp / "mnt" / "code", [Turn(text="Here is what that table holds.")])
    orch = Orchestrator(workspace_dir=oc.workspace, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=resources)
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    return orch, oc


def _bind(orch, **over):
    ws = orch.project(start_preview=False).workspace
    ws.bindings_path.parent.mkdir(parents=True, exist_ok=True)
    row = {"kind": "data_source", "id": "ds1", "name": "Snowflake-Data-Warehouse",
           "display_name": "Snowflake-Data-Warehouse", "database": "DWH",
           "schema": "MARTS", "table": "GONG__CALLS"}
    row.update(over)
    ws.bindings_path.write_text(json.dumps([row]))


def test_a_build_turn_mints_a_token_and_the_card_rides_its_done(tmp_path: Path):
    """The transcript in ADR-0041 was a BUILD turn, so this is the one that had to work.

    Build's stream never emitted an `artifacts` event, so a Live read there wrote its file and the
    person saw nothing. The card rides the `done` instead — `_watchBuild` reads them off either.
    """
    resources = Warehouse()
    orch, oc = _build_orch(tmp_path, resources)
    _bind(orch)
    tid = orch.create_thread()["id"]
    oc.__class__ = ReadingOpenCode
    oc.orch = orch
    oc.args = {"source": "Snowflake-Data-Warehouse", "database": "DWH", "schema": "MARTS",
               "table": "GONG__CALLS", "limit": 1}

    events = list(orch.build_stream("show me 1 sample conversation", conversation=tid))

    assert "Read token: lrt_" in oc.prompts[-1]["text"], "a Build turn mints one too"
    done = [e for e in events if e.get("type") == "done"][-1]
    paths = [a["path"] for a in (done.get("artifacts") or [])]
    assert f"examples/{tid}/gong-calls.table.json" in paths, f"done carried: {paths}"
    assert resources.asked, "and the store really was read"


def test_a_binding_is_what_puts_the_store_in_range_for_a_build_turn(tmp_path: Path):
    # Build has no Session context chips of its own; the Binding is the grant (ADR-0041).
    resources = Warehouse()
    orch, oc = _build_orch(tmp_path, resources)
    _bind(orch, name="Some-Other-Warehouse")
    tid = orch.create_thread()["id"]
    oc.__class__ = ReadingOpenCode
    oc.orch = orch
    oc.args = {"source": "Snowflake-Data-Warehouse", "table": "GONG__CALLS"}

    list(orch.build_stream("show me 1 sample conversation", conversation=tid))

    assert "Use it in this conversation" in oc.said
    assert resources.asked == []
