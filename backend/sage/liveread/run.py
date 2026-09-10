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
    # Keyed by kind, because names live in separate namespaces: a Dataset called DWH must not
    # authorise a read of a Data Source called DWH.
    bound: dict[str, tuple[str, ...]] = field(default_factory=dict)
    chips: dict[str, tuple[str, ...]] = field(default_factory=dict)
    shared: tuple[tuple[str, str], ...] = ()
    binding_for: dict[str, str] = field(default_factory=dict)
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
    """What the assistant is told. The rows are on the person's card, not in this sentence."""
    shape = f"{receipt.rows} row{'' if receipt.rows == 1 else 's'}"
    if receipt.truncated:
        shape = f"the first {shape} (there are more)"
    lines = [
        f"Read {what}: {shape}, written to {receipt.path} and now on screen as a table.",
        f"Columns: {', '.join(receipt.columns) or '(none)'}.",
    ]
    if receipt.values is None:
        # Said plainly, because an assistant that thinks it has the rows will quote rows it invented.
        lines.append(
            "You have NOT been shown the values — the person can see them on the card. Answer about "
            "what the table holds and never quote a value."
        )
    else:
        shown = len(receipt.values)
        of = "" if shown == receipt.rows else f" — {shown} of the {receipt.rows} on the card"
        lines.append(f"Rows (the creator shared this table{of}): {receipt.values}")
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


def _table(args: dict, turn: Turn) -> str:
    name = str(args.get("source") or "")
    refused = grant.reachable("datasource", name,
                              bound=turn.bound.get("datasource", ()), chips=turn.chips.get("datasource", ()))
    if refused:
        return _no_card(refused.says)

    source = turn.source_for(name) if turn.source_for else None
    if source is None:
        return _no_card(brand.text(
            "{assistantName} could not find {name} among the {dataSourcePlural} it can open here.",
            name=name or "that",
        ))

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
    rows = turn.sample_rows(
        source,
        named_db or str(args.get("database") or "") or known[0],
        named_schema or str(args.get("schema") or "") or known[1],
        table,
        limit,
    )
    # A statement that came back full is a statement that hit its own LIMIT, and there is almost
    # certainly more behind it. This is the opposite of the listing rule in ADR-0029, where a walk
    # that exactly fills the cap is NOT truncated — a walk knows it enumerated everything, and a
    # LIMIT knows only that it stopped.
    full = len(rows.rows) >= limit
    receipt = result.record(
        turn.examples_dir,
        _slug(table or name),
        str(args.get("title") or table or name),
        list(rows.columns),
        [list(r) for r in rows.rows],
        cap=limit,
        truncated=full,
        binding=turn.binding_for.get(name, ""),
        table=table,
        shared=turn.shared,
    )
    return _receipt_text(receipt, f"{table or name}")


def _files(args: dict, turn: Turn) -> str:
    name = str(args.get("dataset") or "")
    refused = grant.reachable("dataset", name,
                              bound=turn.bound.get("dataset", ()), chips=turn.chips.get("dataset", ()))
    if refused:
        return _no_card(refused.says)

    rel = str(args.get("path") or "")
    if not rel:
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
        )
        return _receipt_text(receipt, f"the files in {name}")

    root = turn.dataset_root(name) if turn.dataset_root else None
    if root is None or not Path(root).is_dir():
        return _no_card(brand.text(
            "{assistantName} can say what {name} holds, but cannot read a file out of it here — "
            "its files are not mounted in this workspace. Ask about the listing instead.",
            name=name or "that {dataset}",
        ))

    # One file below the Dataset. Resolved inside the mount, so a path climbing out of it reads as
    # a file that is not there rather than as a file somewhere else.
    target = (Path(root) / rel).resolve()
    if not str(target).startswith(str(Path(root).resolve())) or not target.is_file():
        return _no_card(brand.text(
            "There is no file at {path} in {name}. List the {dataset} first and name one it holds.",
            path=rel, name=name,
        ))

    head = target.read_bytes()[:HEAD_BYTES].decode("utf-8", "replace")
    rows = list(csv.reader(io.StringIO(head)))
    if len(rows) < 2:
        return _no_card(
            f"{rel} is not laid out as rows and columns, so there is no table to show. "
            "Say what kind of file it is and stop."
        )
    columns, body = rows[0], rows[1:]
    receipt = result.record(
        turn.examples_dir, _slug(name, Path(rel).stem), f"{Path(rel).name}",
        columns, [r for r in body if r],
        truncated=len(head.encode()) >= HEAD_BYTES,
    )
    return _receipt_text(receipt, rel)


def perform(name: str, args: dict, turn: Turn) -> str:
    if name == "live_read_table":
        return _table(args, turn)
    if name == "live_read_files":
        return _files(args, turn)
    raise ValueError(f"No tool named {name}")
