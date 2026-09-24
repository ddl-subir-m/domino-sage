"""Performing one Live read (ADR-0041).

What `mcp.handle` calls when a tool call arrives. Everything the read needs about the world is
injected on `Turn`, so this is testable with no provider, no workspace and no network — and so that
resolving a Dataset's mount, which only the orchestrator knows how to do, stays where it is known.

Every path through here returns text the ASSISTANT reads. A refusal is text too: it is a sentence
the person is owed, and returning it as a failure leaves the assistant to invent why it could not
answer, which is the transcript ADR-0041 opens with.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..orchestrator import brand
from ..resources.provider import ResourceUnavailable, ScopeIncomplete
from . import disclosure, grant, reference, result

log = logging.getLogger("sage.liveread")

# A file head is a look at what the file holds, not a download of it. Both halves are capped: rows
# by the table cap everything else uses, bytes so that one long line cannot defeat the row cap.
HEAD_BYTES = 256 * 1024


@dataclass(frozen=True)
class Turn:
    """What one Live read may see about the turn that asked for it.

    `bound` and `chips` are the grant. `shared` is the creator's own `.sage/samples.json`, and it is
    the only thing that can put values in front of the assistant.
    """

    thread_id: str
    examples_dir: Path
    # Whether this Project commits real data rows into its own files (ADR-0045). Not a grant: it
    # governs what the Artifact keeps, never what this turn may read or what the assistant is told.
    keep_rows: bool = False
    # Keyed by kind, because names live in separate namespaces: a Dataset called DWH must not
    # authorise a read of a Data Source called DWH.
    bound: dict[str, tuple[str, ...]] = field(default_factory=dict)
    chips: dict[str, tuple[str, ...]] = field(default_factory=dict)
    shared: tuple[tuple[str, str], ...] = ()
    # The Binding id behind each name, keyed by kind for the reason `bound` is: a Dataset called DWH
    # and a Data Source called DWH are two Bindings, and a card that named the wrong one would send
    # a **Read again** into the wrong store (#256).
    binding_for: dict[tuple[str, str], str] = field(default_factory=dict)
    # Where a table already sits, so a read does not have to ask the model to say it again. Keyed
    # (Data Source, table), with (Data Source, "") holding that store's last recorded position.
    scope_for: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    source_for: Callable[[str], Any] | None = None
    sample_rows: Callable[..., Any] | None = None
    # One statement the AGENT composed, run server-side (ADR-0058). Beside `sample_rows` rather than
    # replacing it: showing a few real rows on a card and working a number out of the whole table are
    # different jobs, and ADR-0058 says so in as many words. The agent never gets a shell, a token or
    # a gateway URL — it composes SQL and Sage runs it.
    run_statement: Callable[..., Any] | None = None
    # Listing and reading are two different reaches. Every Dataset can be LISTED, mounted or not —
    # an unmounted one is listed through the data library, which is how a Dataset shared from
    # another project is reached at all. Only a mounted one can have a file read out of it here.
    list_files: Callable[[str], Any] | None = None
    dataset_root: Callable[[str], Path | None] | None = None
    upload_for: Callable[[str], Path | None] | None = None
    reference_for: Callable[[str], reference.Authorized | None] | None = None
    record_data_use: Callable[..., None] | None = None
    analyze_text_batch: Callable[[dict[str, Any]], Any] | None = None
    # Told the sentence a `not-in-range` refusal handed the model (#488). The Turn is built fresh
    # per call, so the record cannot live on it; the caller keeps it per Conversation, and a turn
    # the repeat brake stops can then say what the model was told and ignored. A refusal names a
    # source or a Dataset, never a row — the same class `_no_card` already logs.
    record_refusal: Callable[[str], None] | None = None


def _slug(*parts: str) -> str:
    raw = "-".join(str(p) for p in parts if p)
    keep = [c.lower() if c.isalnum() else "-" for c in raw]
    out = "".join(keep).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:60] or "live-read"


def _refused(turn: Turn, refusal: grant.Refusal | None) -> grant.Refusal | None:
    """A grant refusal on its way to the model, told to the Conversation's record first (#488).

    Four sites make one, and each returns it in its own shape — a `Read` for the two the button
    shares, a sentence for the two only the model reaches — so the record is taken here, where the
    object is still in hand and its tag can be read, rather than downstream where the text is all
    that is left and matching it would be matching a quote of the signal.

    Wraps `grant.reachable` itself, so `None` — the grant passing, the common case — goes through
    untouched: every caller's `if refused:` is what decides, and this line decides nothing.
    """
    if refusal is not None and turn.record_refusal and refusal.tag == "not-in-range":
        turn.record_refusal(refusal.says)
    return refusal


def _no_card(says: str) -> str:
    """A read that ends with no card. The sentence is returned unchanged, and said out loud on the
    way past.

    A refusal, a Data Source this turn cannot open and a file that is not there all reach the person
    as an answer with no table under it — the same thing a crash looks like from outside. Until this
    line they left the same evidence a crash did: nothing. What is logged is the sentence the
    assistant was handed, which names a table or a Dataset and never a row.
    """
    log.info("live read: no card — %s", says)
    return says


def _receipt_text(receipt: result.Receipt, what: str) -> str:
    """What the assistant is told. The rows are on the person's card, not in this sentence.

    The path is deliberately NOT in it. This sentence used to name the file the card was written
    to, one line above the sentence saying the values had not been shown — so the tool answered
    "you may not see these rows" and "they are in this file" in the same breath, and the file was
    an ordinary one any `read` could open. ADR-0041 keeps rows out of the model's context; a
    filename handed to the model is a way back in, and the assistant taking it would be doing what
    it was told rather than reaching past anything.

    Nothing downstream wanted the path either: `run.py` was its only reader, here and in the log
    line below, which keeps it. The assistant still learns a card exists, its shape and its
    columns, which is everything it needs to talk about the table without quoting it.
    """
    shape = f"{receipt.rows} row{'' if receipt.rows == 1 else 's'}"
    if receipt.truncated:
        shape = f"the first {shape} (there are more)"
    lines = [
        # What is on screen, and it is not the same screen in both states. "Now on screen as a
        # table" over a card that holds this table's shape puts the count of rows read and the
        # count of rows visible in one sentence as though they were the same number, and the
        # assistant resolves that however it likes.
        f"Read {what}: {shape}, now on screen as a table." if receipt.kept else
        f"Read {what}: {shape}, now on screen as a card giving this table's shape.",
        f"Columns: {', '.join(receipt.columns) or '(none)'}.",
    ]
    if receipt.values is None:
        # Said plainly, because an assistant that thinks it has the rows will quote rows it invented.
        #
        # And said DIFFERENTLY where the Project keeps no data rows in its files (ADR-0045), because
        # "the person can see them on the card" is then untrue: the card carries the shape too, and
        # an assistant sent to describe a screen nobody is looking at will talk about rows the person
        # is not seeing. The reason travels with it for the same cause ADR-0041 opens with — an
        # assistant left to invent why it cannot answer names a mechanism that does not exist.
        lines.append(
            "You have NOT been shown the values — the person can see them on the card. Answer about "
            "what the table holds and never quote a value."
            if receipt.kept else
            "NOBODY has been shown the values — not you, and not the card, which carries the shape "
            "of this table because this Project does not keep data rows in its files. Answer about "
            "what the table holds, never quote a value, and say that is why if asked."
        )
    else:
        shown = len(receipt.values)
        of = "" if shown == receipt.rows else f" — {shown} of the {receipt.rows} that were read"
        lines.append(f"Rows (the creator shared this table{of}): {receipt.values}")
        if not receipt.kept:
            # The one case where the model holds rows the person does not: the creator shared this
            # table with the agent, and the Project keeps no rows in its files. An assistant that
            # says "here they are, see the table below" is pointing at a card that has none.
            lines.append(
                "The person is NOT looking at these rows — the card carries this table's shape, "
                "because this Project does not keep data rows in its files. Quote them in your "
                "answer if they help, and do not send the person to the card to read them."
            )
    # Every card passes through here, so this is the one place a read that WORKED can say so. The
    # shape, the column count and the path — never the values, which are the person's even on the
    # branch above where the creator shared them.
    log.info("live read: %s — %s, %d columns -> %s",
             what, shape, len(receipt.columns), receipt.path)
    return "\n".join(lines)


def _split_qualified(table: str) -> tuple[str, str, str]:
    """A dotted `database.schema.table` taken apart, or ("", "", table) for a plain name.

    The model sends the dotted form because it is the only form anything shows it. The Chat context
    line names the table `DWH.MARTS.ANTHROPIC__API_KEY_DAILY_USAGE` and hands it a
    `SELECT * FROM DWH.MARTS.ANTHROPIC__API_KEY_DAILY_USAGE` to copy; this tool then asks for "the
    table" and gets back the name it was taught. Measured on 2026-09-09, and it reached the store as
    one identifier: "will not send 'DWH.MARTS.ANTHROPIC__API_KEY_DAILY_USAGE' to a database as a
    name". Our own prose disagreeing with our own argument, not a model mistake.

    `safe_identifier` was right to refuse it and must keep refusing: it is an allowlist standing in
    front of a credential that reads the whole warehouse, and a name is taken apart HERE, above it,
    so that every piece still goes through it one at a time.

    Two and three parts only. Anything longer, or with an empty level, is not a name this can read —
    it goes down whole to be refused, rather than quietly losing a level on the way.
    """
    parts = table.split(".")
    if len(parts) not in (2, 3) or not all(parts):
        return "", "", table
    if len(parts) == 2:
        return "", parts[0], parts[1]
    return parts[0], parts[1], parts[2]


@dataclass(frozen=True)
class Read:
    """What one read came back with, before anything is written down or said out loud.

    `refused` is the sentence the person is owed, and it is the only failure shape here: a caller
    handed a bare failure invents a reason for it, which is the transcript ADR-0041 opens with.
    Exactly one of `refused` and the rows means anything.
    """

    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    truncated: bool = False
    refused: str = ""


def _scoped(args: dict, turn: Turn) -> tuple[str, str, str, str, int]:
    """Which store, which levels, which table and how many rows one table read means.

    Its own function because **Read again** resolves the same way the agent did (#256): a card that
    recorded a bare table name gets the Binding's recorded position back, and one that recorded the
    whole path keeps it.
    """
    name = str(args.get("source") or "")
    # A level the model spelled INSIDE the name wins over one it named beside it: a model that
    # wrote the whole path meant that path, and the two disagreeing is not a case to split down
    # the middle.
    named_db, named_schema, table = _split_qualified(str(args.get("table") or ""))
    limit = max(1, min(int(args.get("limit") or 5), result.CAP_ROWS))
    # The person already said where this table is: the picker recorded a database and a schema when
    # they chose it, and a Binding records the same. Asking the model to repeat them is how a read
    # ends up with neither — `statement` fills a level it was given nothing for with nothing at all,
    # so a missing database turns `DWH.MARTS.T` into `..T` and the store rejects a statement no one
    # can read. What the model DOES name still wins: a table it reached for in another schema of the
    # same store is a real request, and the recorded position is a default, not a fence.
    known = turn.scope_for.get((name, table)) or turn.scope_for.get((name, "")) or ("", "")
    return (name,
            named_db or str(args.get("database") or "") or known[0],
            named_schema or str(args.get("schema") or "") or known[1],
            table,
            limit)


def _table_rows(turn: Turn, name: str, database: str, schema: str, table: str, limit: int) -> Read:
    """Rows out of one Data Source in range, or the sentence saying why there are none.

    Shared by the agent's read and by **Read again**, so the grant one passes is the grant the other
    passes. A viewer whose access went away is refused on the same line the agent would be, which is
    what makes "the button reads as the viewer" a property rather than a promise (#256).
    """
    refused = _refused(turn, grant.reachable("datasource", name,
                              bound=turn.bound.get("datasource", ()), chips=turn.chips.get("datasource", ())))
    if refused:
        return Read(refused=refused.says)

    source = turn.source_for(name) if turn.source_for else None
    if source is None:
        return Read(refused=brand.text(
            "{assistantName} could not find {name} among the {dataSourcePlural} it can open here.",
            name=name or "that",
        ))

    try:
        rows = turn.sample_rows(source, database, schema, table, limit)
    except ScopeIncomplete as e:
        # The one failure the model can repair by itself: it usually spells the dotted name, and a
        # turn that did not is a turn that can be told to (#404). Caught by its own type, never as a
        # bare ValueError — a driver raising one of those would hand its own words to the page.
        return Read(refused=str(e))
    # A statement that came back full is a statement that hit its own LIMIT, and there is almost
    # certainly more behind it. This is the opposite of the listing rule in ADR-0029, where a walk
    # that exactly fills the cap is NOT truncated — a walk knows it enumerated everything, and a
    # LIMIT knows only that it stopped.
    return Read(list(rows.columns), [list(r) for r in rows.rows], len(rows.rows) >= limit)


def _source(kind: str, binding: str, limit: int, **named: str) -> dict:
    """What the card records about the read that made it, so a viewer can run it again (#256).

    Empty without a Binding or without a target, and an empty source is a card with no **Read again**
    button (#258). That is the right floor rather than a gap. A read reached through a Session chip
    alone is a thing this Conversation is looking at, not something the Project holds, so there is
    nothing for a different viewer to re-read it through; and a record naming a store and no table
    would put a button on a card whose press can only come back with a failure.

    Identifiers only — a Binding id and a table name or a path. `table_shape` re-checks every field
    on the way into the file, because this is the one key on that whitelist that could otherwise
    carry a row under a name the rule allows.

    The database and the schema are written as the levels they are, and never folded into the table
    name. A dotted name can only carry both or neither, and "neither" sends the press back down the
    ladder in `_scoped` to the Binding's recorded position — so a read the model aimed at
    `MARTS.CALLS` in a store whose Binding records `SALES` would read `SALES.CALLS` on the press and
    put a DIFFERENT table's rows on that card, under its title, saying they were today's.

    A level that was empty at read time is still written as nothing, and the press takes the
    Binding's answer for it. That is the same ladder the agent's own read climbs, so the two agree;
    what is closed here is a recorded level being overwritten by a different one.
    """
    target = {k: v for k, v in named.items() if v}
    if not binding or not (target.get("table") or target.get("path")):
        return {}
    return {"kind": kind, "binding": binding, "limit": limit, **target}


def _table(args: dict, turn: Turn) -> str:
    if args.get("operation") == "sum":
        from .calculate import calculate

        name, database, schema, table, _ = _scoped({**args, "limit": 1}, turn)
        return calculate({**args, "source": name, "database": database, "schema": schema,
                          "table": table, "dataset": ""}, turn)
    name, database, schema, table, limit = _scoped(args, turn)
    read = _table_rows(turn, name, database, schema, table, limit)
    if read.refused:
        return _no_card(read.refused)

    binding = turn.binding_for.get(("datasource", name), "")
    receipt = result.record(
        turn.examples_dir,
        _slug(table or name),
        str(args.get("title") or table or name),
        read.columns,
        read.rows,
        cap=limit,
        truncated=read.truncated,
        binding=binding,
        table=table,
        shared=turn.shared,
        keep_rows=turn.keep_rows,
        source=_source("table", binding, limit, table=table, database=database, schema=schema),
    )
    return _receipt_text(receipt, f"{table or name}")


def _files(args: dict, turn: Turn) -> str:
    if args.get("operation") == "sum":
        from .calculate import calculate
        return calculate(args, turn)
    if args.get("operation") == "analyze_text":
        from .text_analysis import analyze
        return analyze(args, turn)
    if args.get("operation") == "document":
        return _document(args, turn)
    name = str(args.get("dataset") or "")
    rel = str(args.get("path") or "")
    if not rel:
        # The listing arm checks the grant itself. The file arm gets it from `_file_rows`, which is
        # where **Read again** gets it too — one check on that road rather than two written alike.
        refused = _refused(turn, grant.reachable("dataset", name,
                                  bound=turn.bound.get("dataset", ()), chips=turn.chips.get("dataset", ())))
        if refused:
            return _no_card(refused.says)
        listing = turn.list_files(name) if turn.list_files else None
        if listing is None:
            return _no_card(brand.text(
                "{assistantName} could not open {name} to see what it holds.", name=name or "that",
            ))
        receipt = result.record(
            turn.examples_dir, _slug(name, "files"), f"{name} files",
            ["File", "Bytes"],
            [[f.path, f.size] for f in listing.files],
            truncated=listing.truncated,
            # The one writer this Project's answer does not reach. `[[path, size]]` is filenames,
            # and a filename is not a row — withholding it would take away the read that answers
            # "what does this Dataset hold" and give up nothing in exchange (ADR-0045).
            keep_rows=True,
        )
        return _receipt_text(receipt, f"the files in {name}")

    read = _file_rows(turn, name, rel)
    if read.refused:
        return _no_card(read.refused)
    receipt = result.record(
        turn.examples_dir, _slug(name, Path(rel).stem), f"{Path(rel).name}",
        read.columns, read.rows,
        truncated=read.truncated,
        keep_rows=turn.keep_rows,
        source=_source("file", turn.binding_for.get(("dataset", name), ""), result.CAP_ROWS,
                       path=rel),
    )
    return _receipt_text(receipt, rel)


def _document(args: dict, turn: Turn) -> str:
    """Select bounded text from one exact authorized attachment."""
    rel = str(args.get("path") or "")
    if str(args.get("dataset") or "") != "upload" or not rel:
        return _no_card(
            "Name one authorized attached path and use dataset=upload for a document reference."
        )
    authorized = turn.reference_for(rel) if turn.reference_for else None
    if authorized is None:
        return _no_card(
            f"{rel or 'That document'} is not an authorized attached file in this conversation."
        )
    prepared = reference.prepare(
        authorized,
        selector=str(args.get("heading") or ""),
        pages=args.get("pages"),
    )
    if prepared is None:
        return _no_card(
            f"{rel} is not a supported text, Markdown, DOCX, or PDF document. "
            "Use its bounded typed operation "
            "instead of a generic file read."
        )
    event, reply = reference.data_use(
        prepared, purpose="Use an explicitly referenced attachment"
    )
    if turn.record_data_use:
        turn.record_data_use(event, reply)
    return json.dumps(reply)


def _file_rows(turn: Turn, name: str, rel: str) -> Read:
    """The head of one file inside a mounted Dataset, or the sentence saying why not.

    The other half of the pair `_table_rows` is half of: the agent's read and **Read again** take
    the same road here too, including the grant, so a Dataset that stopped being reachable refuses
    the button the same way it refuses the agent (#256).
    """
    refused = _refused(turn, grant.reachable("dataset", name,
                              bound=turn.bound.get("dataset", ()), chips=turn.chips.get("dataset", ())))
    if refused:
        return Read(refused=refused.says)

    root = turn.dataset_root(name) if turn.dataset_root else None
    if root is None or not Path(root).is_dir():
        return Read(refused=brand.text(
            "{name} isn't mounted here, so files in it can't be opened. Ask about the listing "
            "instead.",
            name=name or "that {dataset}",
        ))

    # One file below the Dataset. Resolved inside the mount, so a path climbing out of it reads as
    # a file that is not there rather than as a file somewhere else.
    #
    # `is_relative_to` and not `startswith`: mounts are siblings under one parent, so a string
    # prefix lets `../sales-private/rows.csv` out of `/mnt/data/sales` and into the Dataset next to
    # it — a grant this function just refused. It mattered less when only the model could name the
    # path; **Read again** takes it from a request body (#256).
    target = (Path(root) / rel).resolve()
    if not target.is_relative_to(Path(root).resolve()) or not target.is_file():
        return Read(refused=brand.text(
            "There is no file at {path} in {name}. List the {dataset} first and name one it holds.",
            path=rel, name=name,
        ))

    head = target.read_bytes()[:HEAD_BYTES].decode("utf-8", "replace")
    rows = list(csv.reader(io.StringIO(head)))
    if len(rows) < 2:
        return Read(refused=(
            f"{rel} is not laid out as rows and columns, so there is no table to show. "
            "Say what kind of file it is and stop."
        ))
    return Read(rows[0], [r for r in rows[1:] if r], len(head.encode()) >= HEAD_BYTES)


class NoSuchRead(ValueError):
    """A source record no card could have written.

    Its own type so a caller can tell it from a `ValueError` raised anywhere below — a limit that
    will not parse, a driver that raises one of its own. Those are refusals a person is owed a
    sentence for; this one is a caller that made the record up.
    """


def read_again(source: dict, turn: Turn) -> Read:
    """Run the read one card came from again, now, as whoever is looking at the card (#256).

    The whole of ADR-0045's answer to a stale transcript, and its cost: this writes NOTHING. The
    rows go back in the response and reach the browser only, so a **Kept rows** opt-out cannot be
    defeated by a click, and a save straight after one commits the shape it always did.

    The Binding id in the record is resolved against the Project's Bindings as they stand now, which
    is what makes the read the viewer's own: a name that is no longer bound, or a store this person
    cannot open, refuses here exactly as it would refuse the agent.
    """
    record = source if isinstance(source, dict) else {}
    kind = str(record.get("kind") or "")
    binding = str(record.get("binding") or "")
    try:
        limit = max(1, min(int(record.get("limit") or result.CAP_ROWS), result.CAP_ROWS))
    except (TypeError, ValueError, OverflowError):
        # Read as "no limit given" rather than as a fault. The cap is the read's floor either way,
        # and a number that will not parse says nothing about what the person may see.
        limit = result.CAP_ROWS
    bound_kind = {"table": "datasource", "file": "dataset"}.get(kind, "")
    if not bound_kind or not binding:
        # Not a refusal anybody is owed a sentence for: a card with no source shows no button, so
        # arriving here at all means a caller made this record up.
        raise NoSuchRead("that card records no read to run again")

    name = next((n for (k, n), i in turn.binding_for.items() if k == bound_kind and i == binding), "")
    if not name:
        # Named for what the card is, because a file card's viewer never asked about a table.
        return Read(refused=brand.text(
            "{assistantName} cannot reach what this {what} was read from — it is no longer one of "
            "this {project}'s {dataSourcePlural} or {datasetPlural}.",
            what="file" if kind == "file" else "table",
        ))
    if kind == "file":
        # Capped here, where the agent's read is capped by `result.record` on the way to the file.
        # 256KB of short rows is several thousand of them, and a card saying "4,217 rows" over a
        # receipt stamped 500 is two numbers about one read.
        read = _file_rows(turn, name, str(record.get("path") or ""))
        return _capped(read, limit)
    _, database, schema, table, limit = _scoped(
        {"source": name,
         "table": str(record.get("table") or ""),
         "database": str(record.get("database") or ""),
         "schema": str(record.get("schema") or ""),
         "limit": limit}, turn)
    return _table_rows(turn, name, database, schema, table, limit)


def _capped(read: Read, limit: int) -> Read:
    """At most `limit` rows, saying so where it cut.

    A table read is capped by the store's own LIMIT before it gets here. A file head is not: it is
    whatever fits in `HEAD_BYTES`, and only `result.record` trimmed it on the way to disk.
    """
    if read.refused or len(read.rows) <= limit:
        return read
    return Read(read.columns, read.rows[:limit], True)


def _statement(args: dict, turn: Turn) -> str:
    """Run one statement the agent composed, and say what it may repeat out loud (ADR-0058).

    This exists because the read-only lane had no way to compute, not because it had no way to
    reach the warehouse. Measured live on `cd9fdd9` and worth recording as the thing this replaces:
    asked for `COUNT(*)` over one table, a turn read one row of 106 columns, composed Python on a
    lane with no shell, then read the sample's own `.table.json` three times until the repeat guard
    killed it — `ok=false`, `decision="repeated"`, 61s, eight model calls. #402 saw the same hole end
    as `decision="table generation failed"` on the artifact lane. Two deaths, one cause.

    The reach for Python is NOT a prompt artefact. #412 removed the `AGENTS.md` sentences that told
    the model to use Python, and on a tree without them the model composed Python anyway. It is what
    a model does when it is asked for a number and has no way to produce one, which is why the fix
    is a tool rather than more prompt.

    THE STATEMENT IS NOT WRITTEN DOWN ANYWHERE. Not in the card's `.sql` sidecar, not in the
    `data_use` event, not in the sentence returned here — only its hash. `result.record`'s sidecar
    is committed in both Kept-rows states, and a statement with a `WHERE EMAIL = '…'` in it is a row
    value; putting it in the Project's git history is precisely what ADR-0041 refuses. Kept rows
    does not govern this and must not be made to: that setting is about ROWS, and a literal in a
    predicate is disclosed by the statement whatever it says.
    """
    name = str(args.get("source") or "")
    sql = str(args.get("sql") or "").strip()
    if not sql:
        return _no_card("Send the statement to run as `sql`. Nothing was put on the person's screen.")

    refused = _refused(turn, grant.reachable("datasource", name,
                              bound=turn.bound.get("datasource", ()),
                              chips=turn.chips.get("datasource", ())))
    if refused:
        return _no_card(refused.says)
    source = turn.source_for(name) if turn.source_for else None
    if source is None or turn.run_statement is None:
        return _no_card(brand.text(
            "{assistantName} could not find {name} among the {dataSourcePlural} it can open here.",
            name=name or "that",
        ))

    try:
        answer = turn.run_statement(source, sql, limit=result.CAP_ROWS)
    except ScopeIncomplete as e:
        return _no_card(str(e))
    except ResourceUnavailable as e:
        # The store's own words, or Sage's own timeout, already told apart upstream and already
        # scrubbed. Surfaced rather than replaced: reporting the analysis as impossible when the
        # store merely objected is the distinction #399 had to be reopened to make. A turn that
        # cannot express its question in SQL should offer the other lane from here (#411).
        return _no_card(str(e))

    verdict = disclosure.decide(sql, answer.rows)
    title = str(args.get("title") or "Query result")
    receipt = result.record(
        turn.examples_dir,
        _slug(title),
        title,
        answer.columns,
        answer.rows,
        truncated=answer.truncated,
        keep_rows=turn.keep_rows,
        # `binding` and `table` are left empty on purpose, which keeps `result.record`'s own `values`
        # None. That field is gated on `grant.values_allowed` — whether the CREATOR shared a named
        # table's rows — and this result has no single table behind it and is not governed by that
        # question at all. Two disclosure gates on one path would be one gate too many, and the one
        # that answers this question is `disclosure.decide` below.
        #
        # `source` is left out for the same reason: **Read again** replays a table or a file, and a
        # computed answer is neither. A card with no button beats a button whose press can only come
        # back with a failure (#258).
    )
    return _computed_text(receipt, verdict, answer, sql, args, turn)


# The schemas a store keeps its own catalogue in. A read of one is finding the way by definition
# (ADR-0063) — names, then columns for a shortlist — and that is the ~29 cards of the sixty this
# rule was measured against.
_CATALOGUE_SCHEMAS = frozenset({"INFORMATION_SCHEMA", "PG_CATALOG", "SYS"})
# The catalogue surfaces that are a bare table name rather than a schema.
_CATALOGUE_TABLES = frozenset({"SQLITE_MASTER"})
# `SHOW TABLES`, `SHOW SCHEMAS IN DATABASE DWH`, `DESC TABLE …`. `sqlglot` has no node for these on
# the default dialect and parses them as `Command`, carrying the leading keyword as `this` — so
# they are read off that keyword rather than off a shape that does not exist here.
_CATALOGUE_COMMANDS = frozenset({"SHOW", "DESC", "DESCRIBE"})


def catalogue_read(sql: str) -> bool:
    """Whether this statement reads a catalogue surface rather than data (ADR-0063).

    PARSED, never pattern-matched, and the difference is the whole safety of this rule. A regex
    over SQL cannot tell the word `INFORMATION_SCHEMA` in a `FROM` clause from the same word inside
    a string literal or a comment, so `WHERE NOTE = 'information_schema'` over a real table would
    read as scaffolding and its card would be folded away. That is the one failure ADR-0063 refuses
    — a rule that can hide an answer — and it is exactly the shape of open bug #450, a bare
    substring test over an artifact path, which this ticket's fourth constraint names.

    Every table in the statement must be a catalogue one, not merely some of them. A statement that
    joins the catalogue to real rows is measuring real rows, and the conservative reading is the
    one the ADR asks for: when unsure, say no and let the card draw.

    A statement that does not parse is NOT a catalogue read. Everything here fails towards
    `'answer'`, which draws, for the reason ADR-0063 gives at length: too much shown is a cluttered
    transcript, an answer folded away is a lie nobody can see.
    """
    try:
        import sqlglot
        from sqlglot import expressions as exp
    except ImportError:
        return False
    try:
        parsed = sqlglot.parse(sql or "")
    # Broad for the reason `disclosure.decide` is broad: `sqlglot` raises more than `ParseError` on
    # malformed input, and every one of them means the same thing here.
    except Exception:
        return False
    statements = [node for node in parsed if node is not None]
    if not statements:
        return False
    return all(_catalogue_statement(node, exp) for node in statements)


def declared_step(args: dict) -> bool:
    """The optional flag, as the caller actually sends it (ADR-0063).

    Both doors declare this as a boolean (`liveread/mcp.py`, `tools/live_read.ts`), and the string
    `"true"` is accepted beside it because a model relaying a JSON schema's boolean as its word is
    a call that meant the flag and would otherwise have it silently dropped. Nothing else counts:
    an absent, null or unreadable value is `'answer'`, which draws.
    """
    value = args.get("step")
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() == "true"


def _catalogue_statement(node, exp) -> bool:
    if isinstance(node, exp.Command):
        return str(node.this or "").strip().upper() in _CATALOGUE_COMMANDS
    if isinstance(node, exp.Describe):
        return True
    # A CTE's name parses as a `Table` where it is selected from, so a `WITH c AS (…) SELECT … FROM
    # c` would otherwise carry one table nothing can classify and fail the `all` below. The alias is
    # not a table; the tables are inside the CTE's own body, which `find_all` already reached.
    aliases = {str(cte.alias or "").upper() for cte in node.find_all(exp.CTE)}
    tables = [t for t in node.find_all(exp.Table)
              if not (not t.db and not t.catalog and t.name.upper() in aliases)]
    if not tables:
        return False
    return all(t.db.upper() in _CATALOGUE_SCHEMAS or t.name.upper() in _CATALOGUE_TABLES
               for t in tables)


def _computed_text(receipt: result.Receipt, verdict, answer, sql: str, args: dict,
                   turn: Turn) -> str:
    """What the assistant is told, and what gets written down about it.

    Recorded whatever the verdict, because the record is about the READ and not about the
    disclosure: a statement that ran and put a card on screen is a data use even when its values
    stayed on the card. This is an ADDITION to a working record rather than a repair of a silent
    one — `live_read` already logs and `calculate` already emits, and both were verified firing on
    `cd9fdd9`. What was missing was a `data_use` event for THIS route, not for any other.
    """
    shape = f"{receipt.rows} row{'' if receipt.rows == 1 else 's'}"
    if receipt.truncated:
        shape = f"the first {shape} (there are more)"
    lines = [f"The query ran: {shape}, now on screen as a table." if receipt.kept else
             f"The query ran: {shape}, now on screen as a card giving this result's shape.",
             f"Columns: {', '.join(receipt.columns) or '(none)'}."]

    # Through `json_safe` for the reason the card's rows are (#435), and separately from them: this
    # lane's `record` call leaves `binding` and `table` empty, so `receipt.values` is always None
    # here and these rows never passed through it. `_table` and `_files` hand the model the same
    # list they wrote; `live_read_query` reads `answer.rows` again, so a `NaN` the file no longer
    # holds would still reach the model — as the bare `nan` of a Python repr, on the one lane #435's
    # own repro used to compute its answer. A model handed a token it cannot re-serialise writes it
    # into the next table it composes, and that table fails validation exactly as this one did.
    values = [result.json_safe(list(row)) for row in answer.rows] if verdict.discloses else []
    if verdict.discloses and len(json.dumps(values, default=str)) > result.VALUES_BUDGET_CHARS:
        # The same sentence `calculate` gives over the same budget, and the same repair: ask for
        # less. A result this wide is a grouped answer with too many groups, and the model can say
        # so or narrow it.
        verdict = replace(verdict, discloses=False, reason=(
            "That result is too large to read here, so only the card has it. Group by fewer things "
            "or add a tighter filter."))
        values = []

    if verdict.discloses:
        lines.append(f"Result: {values}")
    else:
        lines.append(verdict.reason)
        lines.append("Answer about the shape of the result and never quote a value you were not "
                     "given.")

    log.info("live query: %s — %s, %d columns, discloses=%s -> %s",
             receipt.columns and receipt.columns[0] or "(none)", shape, len(receipt.columns),
             verdict.discloses, receipt.path)

    if turn.record_data_use:
        operation = "du_" + uuid4().hex
        event = {
            "operation_id": operation,
            "source": str(args.get("source") or ""),
            # The HASH, never the statement. The event is persisted into the Thread's history, which
            # is committed, and a statement carries literals — see `_statement`'s docstring. This is
            # the same thing `calculate` does with the CSV bytes it read, for the same reason.
            "source_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            # Whether this read was finding the way or answering the question (ADR-0063). The
            # VERDICT and never the statement, decided here because here is the only place the
            # statement is still in hand: by the time `new_artifact_paths` discovers the file this
            # read wrote, the SQL is gone. The publish side joins the two back together on
            # `artifact` below.
            #
            # Two parts, because one can be mechanical and the other cannot. The catalogue rule is
            # testable from the statement alone. The rest is the caller's to declare, because
            # `SELECT COUNT(*) FROM GONG_CALLS` is a step when the question is which customers use
            # monitoring and is the answer when the question is how many calls there are — the same
            # statement, and only the caller knows which it is. An absent flag is `'answer'`.
            "role": "working" if (catalogue_read(sql) or declared_step(args)) else "answer",
            "artifact": receipt.path,
            "columns": list(receipt.columns),
            "selected_fields": [receipt.columns[i] for i in verdict.derived
                                if i < len(receipt.columns)] if verdict.discloses else [],
            "result_rows": receipt.rows,
            "coverage": {"total": receipt.rows, "processed": receipt.rows, "excluded": 0,
                         "failed": 0, "unfinished": 1 if receipt.truncated else 0},
            "purpose": str(args.get("purpose") or "Run a query against a bound data source"),
            "requests": [], "delivery": "unknown",
        }
        reply = {"data_use": operation, "columns": list(receipt.columns),
                 "result_rows": receipt.rows, "local_reference": receipt.path,
                 "coverage": event["coverage"], "selected_fields": event["selected_fields"],
                 "selected": {"rows": values} if verdict.discloses else {},
                 "kept_rows": receipt.kept}
        turn.record_data_use(event, reply)

    return "\n".join(lines)


def perform(name: str, args: dict, turn: Turn) -> str:
    if name == "live_read_table":
        return _table(args, turn)
    if name == "live_read_files":
        return _files(args, turn)
    if name == "live_read_query":
        return _statement(args, turn)
    raise ValueError(f"No tool named {name}")
