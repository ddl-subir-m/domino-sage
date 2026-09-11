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
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..orchestrator import brand
from . import grant, result

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
    # Listing and reading are two different reaches. Every Dataset can be LISTED, mounted or not —
    # an unmounted one is listed through the data library, which is how a Dataset shared from
    # another project is reached at all. Only a mounted one can have a file read out of it here.
    list_files: Callable[[str], Any] | None = None
    dataset_root: Callable[[str], Path | None] | None = None


def _slug(*parts: str) -> str:
    raw = "-".join(str(p) for p in parts if p)
    keep = [c.lower() if c.isalnum() else "-" for c in raw]
    out = "".join(keep).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:60] or "live-read"


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
    refused = grant.reachable("datasource", name,
                              bound=turn.bound.get("datasource", ()), chips=turn.chips.get("datasource", ()))
    if refused:
        return Read(refused=refused.says)

    source = turn.source_for(name) if turn.source_for else None
    if source is None:
        return Read(refused=brand.text(
            "{assistantName} could not find {name} among the {dataSourcePlural} it can open here.",
            name=name or "that",
        ))

    rows = turn.sample_rows(source, database, schema, table, limit)
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
    name = str(args.get("dataset") or "")
    rel = str(args.get("path") or "")
    if not rel:
        # The listing arm checks the grant itself. The file arm gets it from `_file_rows`, which is
        # where **Read again** gets it too — one check on that road rather than two written alike.
        refused = grant.reachable("dataset", name,
                                  bound=turn.bound.get("dataset", ()), chips=turn.chips.get("dataset", ()))
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


def _file_rows(turn: Turn, name: str, rel: str) -> Read:
    """The head of one file inside a mounted Dataset, or the sentence saying why not.

    The other half of the pair `_table_rows` is half of: the agent's read and **Read again** take
    the same road here too, including the grant, so a Dataset that stopped being reachable refuses
    the button the same way it refuses the agent (#256).
    """
    refused = grant.reachable("dataset", name,
                              bound=turn.bound.get("dataset", ()), chips=turn.chips.get("dataset", ()))
    if refused:
        return Read(refused=refused.says)

    root = turn.dataset_root(name) if turn.dataset_root else None
    if root is None or not Path(root).is_dir():
        return Read(refused=brand.text(
            "{assistantName} can say what {name} holds, but cannot read a file out of it here — "
            "its files are not mounted in this workspace. Ask about the listing instead.",
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


def perform(name: str, args: dict, turn: Turn) -> str:
    if name == "live_read_table":
        return _table(args, turn)
    if name == "live_read_files":
        return _files(args, turn)
    raise ValueError(f"No tool named {name}")
