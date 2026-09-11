"""What a `.table.json` keeps when the Project does not keep its rows (ADR-0045). Pure: no I/O.

`workspace.threads.withhold_table_rows` does the writing, at the end of a Chat or Build turn. This
half is separate for the reason `shim.chat_paths` is: the rule is worth reading on its own, and a
rule with a filesystem in it is not.

The count and the column names are recovered the way `store.js` recovers them to paint the card,
and the ladder there is the specification for the one here. A turn writes this file in whichever
shape its pandas reached for — records under `data`, a `to_json` transpose with no array in it at
all — so a count read off the documented shape alone would have reported "0 rows" over the very
files this exists to empty, and the card would have lost the only sentence it has left.
"""
from __future__ import annotations

from typing import Any

SUFFIX = ".table.json"

# Every key the receipt may carry, and nothing else. A whitelist rather than "drop `rows`", because
# the shapes below put values under `data`, under `records`, and under the frame's own column names
# at the top level — a blacklist would have to name each one, and would miss the next.
#
# The last four are optional and belong to a writer that already knew them: ADR-0045's table names
# `cap`, `truncated` and the statement among what a Live read Artifact commits, and `source` is what
# a viewer reads that table again through (#256), so a whitelist that dropped them made this pass
# shrink a file that was already correct. Each is type-guarded on the way through (`_carried`),
# which is what keeps an optional key from becoming a way to smuggle values back in under a name the
# rule allows.
_KEYS = ("title", "columns", "rows", "rowCount", "readAt", "keptRows",
         "cap", "truncated", "statement", "source")


def shape_only(body: Any, *, read_at: str) -> dict:
    """The receipt this table becomes: its title, its columns, how many rows were read, and when.

    No `title` where the file had none. The card already falls back to the manifest's
    filename-derived title, and putting a second name on the same Artifact here would make the two
    disagree on the first restart.

    No `rowCount` where none could be read. Zero is a claim — "the frame was empty" — and a shape
    nothing recovered from has not earned it; the card reads the absence and offers the file
    instead, which is the floor it already had for a table it could not draw.
    """
    columns, count = columns_and_count(body)
    if _is_receipt(body) and not count:
        # Already emptied, by an earlier turn or by the writer itself. Its count and its date belong
        # to the read, and this file no longer holds either — recomputing them here would report "0
        # rows read" at the moment of the rewrite, which is the sentence this module exists to
        # prevent, arrived at from the other side. Over a file a writer already emptied, this whole
        # function is a byte no-op, which is the property that lets one contract have two authors.
        count = _int(body.get("rowCount"))
        read_at = body["readAt"] if isinstance(body.get("readAt"), str) else read_at
    elif not columns and not count:
        count = None
    title = body.get("title") if isinstance(body, dict) else None
    kept = {"columns": columns, "rows": [], "readAt": read_at, "keptRows": False}
    if count is not None:
        kept["rowCount"] = count
    if isinstance(title, str) and title.strip():
        kept = {"title": title, **kept}
    kept.update(_carried(body))
    return {k: kept[k] for k in _KEYS if k in kept}


def _carried(body: Any) -> dict:
    """The optional keys a writer may already have filled in, kept only in the type they are owed.

    `cap` and `truncated` are the halves of ADR-0029's rule that a short read says so, and
    `statement` is the path to the `.sql` sibling — a path, not a query, which is why it is checked
    for one. None of the three can hold a data row in the type it is allowed to arrive in, and that
    is the whole reason each is checked rather than copied: an optional key is otherwise a name the
    rule allows, which is what a value would come back under.
    """
    if not isinstance(body, dict):
        return {}
    out: dict = {}
    if _int(body.get("cap")) is not None:
        out["cap"] = body["cap"]
    if isinstance(body.get("truncated"), bool):
        out["truncated"] = body["truncated"]
    statement = body.get("statement")
    if isinstance(statement, str) and statement.endswith(".sql"):
        out["statement"] = statement
    source = _source(body.get("source"))
    if source:
        out["source"] = source
    return out


# What a `source` may name, and nothing else. A nested object is the one shape on this whitelist
# that could hold a whole row under a key nobody checks, so it gets a whitelist of its own rather
# than riding in whole on the strength of its name.
_SOURCE_KEYS = {"kind": str, "binding": str, "table": str, "database": str, "schema": str,
                "path": str, "limit": int}


def _source(raw: Any) -> dict:
    """Which read produced this table, so a different viewer can run it again (#256).

    An identifier, never a value: a Binding id and a table name, or a Binding id and a path inside
    a Dataset. That is why it is allowed past a rule whose whole job is keeping values out of a
    committed file, and why each field is type-checked on the way through like every other optional
    key here.

    `kind`, `binding` and a target are all required, because a card reads them together: `kind` says
    which read to run, the Binding is what a viewer runs it through, and the table or path is what
    it reads. Half a record is worse than none — it puts a **Read again** button on a card whose
    press can only come back with a failure (#258).
    """
    if not isinstance(raw, dict):
        return {}
    out = {k: raw[k] for k, want in _SOURCE_KEYS.items()
           if k in raw and isinstance(raw[k], want) and not isinstance(raw[k], bool)}
    if not out.get("kind") or not out.get("binding") or not (out.get("table") or out.get("path")):
        return {}
    return out


def _int(value: Any) -> int | None:
    """A whole number, and `True` is not one. `isinstance(True, int)` holds in Python, so a body
    carrying `"rowCount": true` would otherwise write a boolean into a numeric field, and the card
    reads that as no count at all — a table offered as a file that no longer has any rows in it."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_receipt(body: Any) -> bool:
    """Whether this file has already been through here. `keptRows` is written by nothing else.

    It records what THAT FILE holds and not what the Project currently answers. A Project that turns
    Kept rows on later still has older Artifacts saying `false`, and that is correct — repairing
    them on the next save would be writing rows nobody asked anyone to write.
    """
    return isinstance(body, dict) and body.get("keptRows") is False


def unreadable(read_at: str) -> dict:
    """The receipt for a `.table.json` that is not JSON.

    Sage cannot read it, so it cannot promise it holds no values, and it cannot name a count it
    never read. The ADR's rule for an unknown destination is the rule for unknown contents: keep
    what can be named — here, nothing — rather than push bytes nobody has looked at.
    """
    return {"columns": [], "rows": [], "readAt": read_at, "keptRows": False}


def columns_and_count(body: Any) -> tuple[list[str], int]:
    """The table's column names and how many rows it held, over every shape the card reads."""
    bare = body if isinstance(body, list) else None
    wrapper = body if bare is None and isinstance(body, dict) else {}

    source = bare
    if source is None:
        for key in ("rows", "data", "records"):
            value = wrapper.get(key)
            if isinstance(value, list):
                source = value
                break
    source = source or []

    head = source[0] if source else None
    schema = wrapper.get("schema")
    fields = []
    if isinstance(schema, dict) and isinstance(schema.get("fields"), list):
        # `orient="table"` names its columns in a JSON Table Schema and nowhere else, and its
        # `index` field is one pandas synthesised rather than one of the frame's own.
        # Through `_column_name` like every other source of a name: a MultiIndex column writes its
        # `name` as a list, and a list left in the receipt is a column head nothing can print.
        fields = [n for n in (_column_name(f.get("name")) for f in schema["fields"]
                              if isinstance(f, dict)) if n and n != "index"]

    named = _column_list(wrapper.get("columns"))
    columns = (named if any(named) else
               fields if fields else
               [str(k) for k in head] if isinstance(head, dict) else [])

    if not source:
        # No array anywhere, which is every `to_json` orient that keeps no list: the rows are the
        # dict's own values. The wrapper is tried first because that is the plain dump, then the
        # keys a wrapper would have used.
        #
        # A RECEIPT's own keys come off the wrapper first, and only a receipt's. `source` is a small
        # object of strings and numbers, which is exactly the shape this guess is looking for, so a
        # receipt that carries one read back as a one-row table with a column called `source`
        # (#256) — a card headed with the names of its own metadata.
        #
        # Stripped only where `keptRows` says this file came through here, for the reason
        # `_is_receipt` uses it: nothing else writes that key. A frame really can have a column
        # called `title` or `source`, and dropping those out of somebody's data to protect a file
        # that is not a receipt is the blank box this whole ladder exists to prevent.
        for candidate in (_without_receipt_keys(wrapper),
                          wrapper.get("data"), wrapper.get("rows"), wrapper.get("records")):
            dump = _pandas_oriented(candidate)
            if dump:
                return dump
    return columns, len(source)


def _without_receipt_keys(wrapper: dict) -> dict:
    """The wrapper with the receipt's own keys removed, where it is a receipt at all."""
    return ({k: v for k, v in wrapper.items() if k not in _KEYS}
            if _is_receipt(wrapper) else wrapper)


def _column_name(value: Any) -> str:
    """One column's name, whatever the turn wrote in its place — a string, a JSON Table Schema
    `{name, type}`, or a pandas Index dumped as `{0: "date"}`."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return " ".join(n for n in (_column_name(v) for v in value) if n)
    if isinstance(value, dict):
        for key in ("name", "title", "field", "key"):
            if key in value:
                return _column_name(value[key])
    return ""


def _column_list(raw: Any) -> list[str]:
    if isinstance(raw, list) and raw:
        return [_column_name(v) for v in raw]
    if isinstance(raw, dict) and raw:
        return [_column_name(v) for v in raw.values()]
    return []


def _series_of_scalars(value: Any) -> bool:
    return (isinstance(value, dict) and bool(value)
            and all(v is None or not isinstance(v, (dict, list)) for v in value.values()))


def _index_keys(keys: list[str]) -> bool:
    return bool(keys) and all(str(k).lstrip("-").isdigit() for k in keys)


def _pandas_oriented(obj: Any) -> tuple[list[str], int] | None:
    """`df.to_json()` with no orient (columns) or `orient="index"`, which keep no array at all.

    The two are transposes of each other: columns names the frame's columns at the top level with
    the row index inside, index names the row index at the top level with the column names inside.
    Numeric-looking keys pick which is which, and where they cannot, this answers nothing at all.

    That last part is where this stops mirroring the card, deliberately. When NEITHER key set looks
    like an index — `df.set_index("email").to_dict("index")`, or a correlation matrix — the two
    readings are transposes and one of them makes the top-level keys row labels, which are values.
    `store.js` guesses columns-orient and paints it, and it can afford to: it is drawing a file that
    still has its rows, and a wrong guess is a header nobody can read. Here the guess would be
    WRITTEN, and a wrong one puts every email in the index into the `columns` of a file whose whole
    claim is that it holds no data. The count is no better — it would be the number of columns read
    as a number of rows. So the ambiguous shape recovers nothing, the receipt keeps no count, and
    the card falls through to offering the file, which is the floor it already had.
    """
    if not isinstance(obj, dict):
        return None
    entries = [(k, v) for k, v in obj.items() if _series_of_scalars(v)]
    if not entries:
        return None
    if len([v for v in obj.values() if isinstance(v, dict)]) != len(entries):
        return None

    keys = [k for k, _ in entries]
    values = [v for _, v in entries]
    inner = list(values[0])
    if _index_keys(keys) and not _index_keys(inner):
        return [str(c) for c in inner], len(values)

    index_keys: list[str] = []
    seen = set()
    for value in values:
        for key in value:
            if key not in seen:
                seen.add(key)
                index_keys.append(key)
    if not _index_keys(index_keys):
        return None
    return [str(k) for k in keys], len(index_keys)
