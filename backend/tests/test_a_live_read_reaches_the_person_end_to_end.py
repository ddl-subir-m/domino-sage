"""ADR-0041 end to end: the token reaches the prompt, and the call reaches the card.

The pieces are tested apart elsewhere. This holds the wiring between them — that a turn mints a
token into its own prompt, that an MCP call carrying that token resolves to that Conversation's
grant, and that the rows land in an Artifact while the assistant is told only their shape.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sage.orchestrator.service import Orchestrator
from sage.resources.provider import DataSource, FakeResourceProvider, SampleRows
from sage.router.models import ModelCatalog
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
    assert "live_read_" in oc.prompts[-1]["text"], "and tells the agent what it is for"


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


class ReadingOpenCode(FakeOpenCode):
    """An agent that does what the turn prompt tells it to: relays the token and reads.

    The whole loop, in the only place it can be driven from a test — the Artifact this writes has
    to be picked up by the same end-of-turn scan that finds one the agent wrote itself.
    """

    orch = None
    args: dict = {}

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
