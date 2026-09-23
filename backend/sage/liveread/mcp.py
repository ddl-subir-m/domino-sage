"""The Live read tools, as an MCP server OpenCode can call (ADR-0041).

OpenCode is given tools by `mcp` in `opencode.json`, and its remote form takes a URL — which the
orchestrator already serves. The shim cannot do this job: it sits on the LLM request, so a tool it
injected there would be one OpenCode could not execute.

Framing only. What a call actually does is `run`, injected by the caller, so the protocol can be
tested without a provider, a workspace or a network.

The `token` argument is how a call says which turn it belongs to. One OpenCode server hosts many
Conversations, so neither the URL nor a header can carry it, and asking the assistant to name its
own Conversation would put scope in the hands of the thing scope is protecting against (ADR-0038).
Instead the orchestrator mints a token per turn and puts it in that turn's prompt: an assistant can
relay the one it was given and has no way to name another Conversation's.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("sage.liveread")

# Verified live against the PINNED OpenCode 1.18.4 (darwin-arm64), not just the published schema.
# It opens with `protocolVersion: "2025-11-25"`, accepts this older one in reply, and reports the
# server connected — so negotiation is tolerant and there is no reason to claim a revision whose
# requirements are not implemented here.
PROTOCOL_VERSION = "2025-06-18"

# OpenCode NAMESPACES an MCP tool by its `opencode.json` key when it offers it to the model, and
# strips that prefix again before calling the server. Verified live on 1.18.4: the model is offered
# `sage-live-read_live_read_table`, and what arrives here is `live_read_table`. So this file keys on
# the BARE name and is right to — do not "fix" it to expect the prefix. What must carry the prefix
# is every instruction that names a tool to the agent, and the two are pinned to each other by
# test_the_live_read_tools_reach_opencode_as_an_mcp_server.

_TOKEN = {
    "type": "string",
    "description": "The read token from this turn's prompt. Pass it back exactly as given.",
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "live_read_table",
        "description": (
            "Read a few real rows out of one bound table and show them to the person as a table "
            "card. Use this whenever they ask what the data looks like, or to see a sample row. "
            "You get back the columns, a row count and a path — not the rows themselves, which go "
            "straight to the card the person sees. Say what the table holds; do not claim to be "
            "quoting values you were not given."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": _TOKEN,
                "source": {"type": "string", "description": "The Data Source name."},
                # Optional, and genuinely so: left out, the read uses the database and schema the
                # table was picked at. Name one only to reach elsewhere in the same store.
                "database": {"type": "string", "description": "Omit to use the recorded one."},
                "schema": {"type": "string", "description": "Omit to use the recorded one."},
                # Dotted or bare. Everything else the agent reads names this table
                # `DWH.MARTS.SALES`, so that is what it sends, and taking it apart here costs less
                # than teaching one screen to disagree with the rest.
                "table": {"type": "string", "description": "The table. A dotted "
                                                          "database.schema.table is fine."},
                "operation": {"type": "string", "enum": ["sum"],
                              "description": "Calculate a table locally and return selected totals."},
                "group_by": {"type": "string"},
                "sum_column": {"type": "string"},
                "selected_fields": {"type": "array", "items": {"type": "string"},
                                    "description": "Result columns and/or total. Omit for structure only."},
                "row_limit": {"type": "integer", "description": "Explicit user row limit; omit for all rows."},
                "result_name": {"type": "string"},
                "purpose": {"type": "string"},
                "limit": {"type": "integer", "description": "Rows to read. Default 5, capped."},
                "title": {"type": "string", "description": "A short title for the card."},
            },
            "required": ["token", "source", "table"],
        },
    },
    {
        # NO ROLLOUT SCOPE IN ANY DESCRIPTION HERE, and that is a rule rather than a style choice
        # (#423, closed by #431). Four shipped descriptions used to open "Fresh projects: …", in a
        # field only the model sees, as a precondition on the parameter. A model applied it to
        # itself, decided its Project was not "fresh", and told a person a working capability
        # belonged to other projects — it declined without ever calling the tool, so no gate was
        # reached and no refusal was involved. The clause has been deleted rather than reworded:
        # `dataUseVersion` is gone (#431), there is no live scope to state, and a description is
        # not where scope would be stated even if there were one.
        # `test_a_tool_description_never_says_where_a_feature_is_rolled_out` reds if one comes back.
        "name": "live_read_query",
        "description": (
            "Work out a number from a bound Data Source by writing one SQL statement. Use this "
            "whenever the answer needs the numbers WORKED OUT rather than looked at — a count, a "
            "total, an average, a ranking, a correlation, a group-by, a join across tables. "
            "Sage runs the statement server-side and puts the result on a table card the person "
            "sees. You get the numbers back when every column you select is one worked out from "
            "the rows — COUNT, SUM, AVG, MEDIAN, CORR and the like, plus whatever you GROUP BY. "
            "One unsupported column keeps the entire result on the card; none of its values "
            "come back to you. For a regression, return REGR_* statistics and numeric counts "
            "together. Keep MIN/MAX of dates or text in a separate display query if needed; "
            "mixing them with numeric statistics withholds those statistics too. Prefer this "
            "over reading rows and adding them up yourself."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": _TOKEN,
                "source": {"type": "string", "description": "The Data Source name."},
                # Dotted names, because that is what everything else the agent reads shows it — the
                # Chat context line names a table `DWH.MARTS.SALES` — and because a statement is
                # sent as written. Nothing here takes the name apart: `_split_qualified` exists for
                # a tool that BUILDS the SQL, and this one does not.
                "sql": {"type": "string", "description": (
                    "One SELECT statement, written for this store's own SQL. Name tables in full, "
                    "as database.schema.table. One statement: no semicolons, no second query."
                )},
                "title": {"type": "string", "description": "A short title for the card."},
                "purpose": {"type": "string", "description": (
                    "What this is being worked out for, in a few words. It goes in the record, not "
                    "to the person."
                )},
                # The declared half of the role rule (ADR-0063). No rule over SQL can reach this:
                # `SELECT COUNT(*) FROM GONG_CALLS` is a step when the question is which customers
                # use monitoring and is the answer when the question is how many calls there are.
                # Only the caller knows which, so only the caller can say. Left out means the
                # answer, and the card draws.
                "step": {"type": "boolean", "description": (
                    "True when this statement is a step towards the answer rather than the "
                    "answer itself — counting rows to see whether a table is worth using, "
                    "measuring how often a column is filled, checking that a join key matches. "
                    "Its card is folded out of the way so the answer is readable. Leave it out "
                    "for the statement that answers the question. Catalogue reads count as steps "
                    "without being told."
                )},
            },
            "required": ["token", "source", "sql"],
        },
    },
    {
        "name": "live_read_files",
        "description": (
            "List the files in a bound Dataset, or read the head of one of them. Use this to say "
            "what a Dataset holds. A listing that stopped short of the end says so — never report "
            "a capped listing as all of them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": _TOKEN,
                "dataset": {"type": "string", "description": "The Dataset name."},
                "operation": {"type": "string", "enum": ["sum", "analyze_text", "document"],
                              "description": "Calculate CSV totals, analyze CSV text, or select "
                                             "bounded attached document text."},
                "group_by": {"type": "string"},
                "sum_column": {"type": "string"},
                "text_column": {"type": "string"},
                "id_column": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "output_field": {"type": "string"},
                "batch_size": {"type": "integer"},
                "max_concurrency": {"type": "integer"},
                "selected_fields": {"type": "array", "items": {"type": "string"},
                                    "description": "Result columns and/or total. Omit for structure only."},
                "row_limit": {"type": "integer", "description": "Explicit user row limit; omit for all rows."},
                "result_name": {"type": "string"},
                "purpose": {"type": "string"},
                "heading": {"type": "string", "description": "Exact unique Markdown heading."},
                "path": {
                    "type": "string",
                    "description": "One file below it. Omit to list the Dataset instead.",
                },
            },
            "required": ["token", "dataset"],
        },
    },
]

_TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


def _failed_text(why: str) -> str:
    """What the assistant reads when a read broke.

    The driver's message on its own is a sentence about SQL, and an assistant handed one goes off
    to answer the question another way — which is right. What it did next was say "here are the
    first 5 rows" over a card nobody had written, because nothing had told it the screen was still
    empty. So that is said first and the message second.
    """
    return (f"The live read did not happen: {why}. Nothing was put on the person's screen. "
            "Query the data with Python and write the table file yourself, and do not say "
            "anything is showing that you did not write.")


def _result(mid: Any, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": payload}


def _error(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(message: dict, *, run: Callable[[str, dict], str]) -> dict | None:
    """Answer one JSON-RPC message. `None` for a notification, which takes no reply.

    `run(name, arguments)` returns the text the assistant sees. It raises to report a failure the
    assistant should read as one — a refusal is NOT a failure, and comes back as ordinary text, so
    that the sentence the person is owed survives instead of becoming a protocol error nobody words.
    """
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request")
    method = message.get("method")
    mid = message.get("id")

    if mid is None:
        return None  # A notification: `notifications/initialized` and friends.

    if method == "initialize":
        return _result(mid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "sage-live-read", "version": "1"},
        })
    if method in ("tools/list", "prompts/list", "resources/list"):
        if method != "tools/list":
            return _result(mid, {method.split("/")[0]: []})
        return _result(mid, {"tools": TOOLS})
    if method == "ping":
        return _result(mid, {})
    if method != "tools/call":
        return _error(mid, -32601, f"Method not found: {method}")

    params = message.get("params") or {}
    name = params.get("name")
    if name not in _TOOL_NAMES:
        return _error(mid, -32602, f"No tool named {name}")
    # Checked before the default, not after: `or {}` turns an empty list into a valid empty call,
    # so a malformed request would have read as a well-formed one with nothing in it.
    args = params.get("arguments")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _error(mid, -32602, "arguments must be an object")

    try:
        text = run(str(name), args)
    # Broad on purpose: the assistant reads this, so nothing may escape as a 500.
    except Exception as e:
        # WARNING, so it outlives one turn in `/api/diag`'s warn ring. A raise here reaches the
        # assistant as ordinary text, which it reads as an answer and goes off to Python with — so
        # until this line a read that broke and a read nobody made left the same evidence: an
        # answer with no card under it. The message is the driver's, and it can name the statement
        # it choked on; that goes to the creator's own diagnostics, never to the model.
        log.warning("live read: %s failed — %s: %s — the turn answers from Python instead, "
                    "so the rows reach the model rather than the card (ADR-0041)",
                    name, type(e).__name__, e)
        return _result(mid, {
            "content": [{"type": "text", "text": _failed_text(str(e))}],
            "isError": True,
        })
    return _result(mid, {"content": [{"type": "text", "text": text}]})
