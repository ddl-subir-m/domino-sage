"""What the bound tables hold, recorded and told to the agent (#15).

A creator picks a Data Source and a Scope (#11) and then asks for an app. The agent writing that app
has to name real columns in real tables or the query fails on the first viewer — so it is given the
schema of what was bound, read once when the Scope was chosen.

Names and types only. Never rows: sample data is production data in a model's context, and whether
that is acceptable is the creator's decision to make explicitly (#16), not one inferred from the fact
that it would help.

Two places, one shape. `.sage/schema.json` is the record, committed to the app's repo like every
other manifest, and the AGENTS.md region is what the agent actually reads. Small schemas are written
into the region in full, because a read the agent has to remember to make is one it can skip; large
ones are named there and left in the file, because a two-hundred-table schema in front of the model
on every turn costs more context than it is worth.

Pure functions, like `pinned_model`: everything here is a transformation of an already-read manifest,
so the rendering is testable apart from the I/O and the store.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..orchestrator import brand
from .app_helpers import TEMPLATE, HelperNames
from .bindings import Binding
from .provider import Column, SampleRows

SCHEMA_PATH = ".sage/schema.json"
# Gitignored, unlike every other manifest here (#16). `.sage/` is committed and rides into the
# published app's container, which is right for names and types and wrong for rows: sample data in
# that file would put production data in the creator's git history and inside the deployed app. So
# samples live where a clone cannot pick them up — and AGENTS.md, which IS committed, can only point
# at them, never quote them.
SAMPLES_PATH = ".sage/samples.json"

# Above this many columns the region names the tables and points at the file instead of listing
# everything. Chosen against the shape of the thing being described: a single bound table is tens of
# columns and belongs in front of the agent, while a bound schema is however many tables the
# warehouse has and does not. AGENTS.md is read on every turn, so this is a recurring cost.
INLINE_COLUMN_LIMIT = 80
# And even then the table list itself is bounded, for the schema that holds hundreds.
TABLE_NAME_LIMIT = 60


@dataclass(frozen=True)
class BoundSource:
    """One Data Source this app reads, as the agent is told about it (#33).

    Several, since a creator binds a warehouse and an app database because the app has a screen for
    each — and `serve.py` was built for that: it loads every Binding and each query names the one it
    reads. `stranded` is per source because whether a Scope travels as configuration is decided by
    the connector, so two sources in one app can need differently-written SQL.
    """

    binding: Binding
    columns: list[Column]
    stranded: list[tuple[str, str]] | None = None


def render_schema(sources: list[tuple[Binding, list[Column]]]) -> str:
    """`.sage/schema.json` — what each bound Data Source holds, in Binding order.

    No timestamp, deliberately. This file is committed to the creator's own app repo, and a "read at"
    field would make every re-bind a diff in a file whose content had not changed.

    Keyed by Binding id rather than by source name: an id is what a query's `binding` field carries,
    and it is what survives a Data Source being renamed in Domino.
    """
    body = {"sources": [
        {
            "id": binding.id,
            "source": binding.name,
            "scope": binding.scope,
            "connector_type": binding.connector_type,
            "tables": [{"name": name, "columns": [{"name": c.name, "type": c.type} for c in cols]}
                       for name, cols in _by_table(columns).items()],
        }
        for binding, columns in sources
    ]}
    return json.dumps(body, indent=2) + "\n"


def _by_table(columns: list[Column]) -> dict[str, list[Column]]:
    out: dict[str, list[Column]] = {}
    for column in columns:
        out.setdefault(column.table, []).append(column)
    return out


# Where the columns of a pre-#33 schema land. That file named one source and no id, and the Binding
# it described was the first one recorded — so the caller, which knows the Bindings, moves them onto
# that id. Keyed apart rather than guessed at here, because this function has no Binding list.
LEGACY_SOURCE = ""


def parse_schema(raw: object) -> dict[str, list[Column]]:
    """The recorded schema back as columns per Binding id, in file order.

    Anything unreadable is no schema at all, and the region then says the columns are not known
    rather than inventing them. The pre-#33 shape — one source, no id — reads back under
    `LEGACY_SOURCE`, since an app written by an older Sage must not lose its columns on upgrade.
    """
    if not isinstance(raw, dict):
        return {}
    if "sources" not in raw:
        columns = _columns_of(raw)
        return {LEGACY_SOURCE: columns} if columns else {}
    out: dict[str, list[Column]] = {}
    for entry in raw.get("sources") or []:
        if isinstance(entry, dict) and str(entry.get("id") or ""):
            out[str(entry["id"])] = _columns_of(entry)
    return out


def recorded_scope(raw: object, binding_id: str) -> str | None:
    """The Scope the recorded schema was read at for one Binding, or None when it holds no entry.

    What tells a schema that is merely old from one that is missing: a Binding whose Scope has moved
    has an entry describing the wrong part of the store, and re-reading it costs a query, so the
    caller has to be able to tell the two apart without one.
    """
    if not isinstance(raw, dict) or "sources" not in raw:
        return None
    for entry in raw.get("sources") or []:
        if isinstance(entry, dict) and str(entry.get("id") or "") == binding_id:
            return str(entry.get("scope") or "")
    return None


def _columns_of(entry: dict) -> list[Column]:
    out: list[Column] = []
    for table in entry.get("tables") or []:
        if not isinstance(table, dict):
            continue
        name = str(table.get("name") or "")
        for column in table.get("columns") or []:
            if isinstance(column, dict) and str(column.get("name") or ""):
                out.append(Column(name, str(column["name"]), str(column.get("type") or "")))
    return out


@dataclass(frozen=True)
class SharedSample:
    """One table's shared rows, and which Data Source they came out of (#16, #33).

    The Binding id rather than the store's name, for the reason the schema record keys on it: two
    stores in one app can hold a table of the same name, and `events` shared from the warehouse is
    not `events` shared from the app database. Without it the two collide in one file and the picker
    cannot tell which of its boxes to tick.
    """

    binding: str
    rows: SampleRows


def render_samples(shared: list[SharedSample]) -> str:
    """`.sage/samples.json` — the rows a creator chose to share.

    Named tables and their rows only. Leftover `sensitive` keys in an older file are ignored on
    read and not rewritten.
    """
    return json.dumps({
        "tables": [{"binding": s.binding, "name": s.rows.table,
                    "columns": s.rows.columns, "rows": s.rows.rows} for s in shared],
    }, indent=2) + "\n"


def parse_samples(raw: object) -> list[SharedSample]:
    """The shared tables. Unreadable is no samples, which is the safe reading: the agent is shown
    nothing it was not certainly given.

    An entry written before #33 names no Binding. It reads back with an empty one, and the caller —
    which knows the Bindings — resolves that to the first Data Source, the only one that could have
    produced it. This file is gitignored, so that only ever happens to a workspace whose orchestrator
    was upgraded under it, never to a clone.
    """
    if not isinstance(raw, dict):
        return []
    out: list[SharedSample] = []
    for table in raw.get("tables") or []:
        if isinstance(table, dict) and str(table.get("name") or ""):
            rows = SampleRows(str(table["name"]), list(table.get("columns") or []),
                              [list(r) for r in table.get("rows") or [] if isinstance(r, list)])
            out.append(SharedSample(str(table.get("binding") or ""), rows))
    return out


def agents_block(sources: list[BoundSource], problems: list[str] | None,
                 max_rows: int, *, samples=(), names: HelperNames = TEMPLATE) -> str:
    """What the agent is told about the app's data, for the managed AGENTS.md region.

    Empty when no Data Source is bound. Describing the machinery for a store that is not there would
    cost context on every turn and invite an app built around data it cannot reach.

    Prescriptive about the four things an agent gets wrong when left to itself. It writes SQL into a
    component and a fetch to go with it, when the only path is a named query in a manifest. It
    qualifies — or fails to qualify — table names without knowing which the connector needs. It sees
    the preview 404 and "fixes" a query that was correct. And it reaches for the store directly to
    see what is in a table, which is the one thing the creator has not agreed to.

    Several sources get a section each, headed by the id a query has to carry, because the mistake
    that replaces "which columns" once there are two stores is "which store" — and a query naming
    the wrong Binding is refused at startup with a sentence about a Data Source the creator did pick.
    One source reads exactly as it did before: a heading per store, when there is one store, is a
    structure that says nothing and is re-read every turn.
    """
    if not sources:
        return ""
    lines = ["## The app's data", ""]
    if len(sources) == 1:
        one = sources[0]
        lines += [_scope_sentence(one.binding), ""]
        lines += _tables_section(one.columns)
    else:
        lines += [
            brand.text("This app reads {count} {dataSourcePlural}. Every query names the one it "
                       "reads, so the first thing to settle for any question is WHICH of these "
                       "holds the answer:",
                       count=len(sources)), "",
        ]
        for source in sources:
            lines += [
                f"### {source.binding.display_name} — `\"binding\": \"{source.binding.id}\"`", "",
                _scope_sentence(source.binding), "",
            ]
            lines += _tables_section(source.columns)
            lines += [_scope_rule(source.binding, source.stranded,
                                  source.columns[0].table if source.columns else "usage"), ""]
    # The example names one of this app's own tables. A generic `usage` reads as a placeholder the
    # agent has to translate, and the translation is exactly the step this block exists to remove.
    lines += _how_to_ask(sources, max_rows, names)
    lines += _samples_section(samples)
    lines += _problems_section(problems)
    return "\n".join(lines)


def _scope_sentence(binding: Binding) -> str:
    kind = binding.connector_type[:-6] if binding.connector_type.endswith("Config") else ""
    where = f"**{binding.scope}** in " if binding.scope else ""
    named = (brand.text("the {kind} {dataSource}", kind=kind) if kind
             else brand.text("the {dataSource}"))
    return f"This app reads {where}{named} **{binding.display_name}**."


def _tables_section(columns: list[Column]) -> list[str]:
    """The tables and their columns, in full or by name, or a line saying they are not known."""
    tables: dict[str, list[Column]] = {}
    for column in columns:
        tables.setdefault(column.table, []).append(column)
    if not tables:
        return [
            brand.text(
                "{assistantName} could not read what the tables in this Scope hold, so their "
                "column names are not available here. Ask the user what a table holds rather than "
                "guessing column names — a query naming a column that does not exist fails for the "
                "first person who opens the app."),
            "",
        ]
    if len(columns) <= INLINE_COLUMN_LIMIT:
        out = ["These are its tables and columns — use these names exactly:", ""]
        for name, cols in tables.items():
            out.append(f"`{name}`")
            out += [f"  - `{c.name}` {c.type}".rstrip() for c in cols]
            out.append("")
        return out
    listed = list(tables)[:TABLE_NAME_LIMIT]
    more = len(tables) - len(listed)
    out = [
        f"It has {len(tables)} tables:", "",
        ", ".join(f"`{n}`" for n in listed) + (f", and {more} more" if more else ""), "",
        (f"**Their columns are in `{SCHEMA_PATH}`.** Read that file for the tables you are about to "
         "use, and take the column names from it rather than guessing — there are too many to list "
         "here."), "",
    ]
    return out


def _how_to_ask(sources: list[BoundSource], max_rows: int, names: HelperNames) -> list[str]:
    first = sources[0]
    table = first.columns[0].table if first.columns else "usage"
    # Which store a query reads is per query, so with several it is a rule of its own rather than a
    # constant to copy. The ids are repeated here beside the example even though each section above
    # is headed by one: this is where the agent is looking while it writes the entry.
    if len(sources) == 1:
        which = [brand.text('- `binding` must be `"{id}"`. That is this app\'s {dataSource}.',
                            id=first.binding.id),
                 _scope_rule(first.binding, first.stranded, table)]
    else:
        which = [brand.text("- `binding` says WHICH of this app's {dataSourcePlural} the query "
                            "reads: {ids}. Getting it wrong is not a slow query, it is a query the "
                            "app refuses to run.",
                            ids=", ".join(f'`"{s.binding.id}"` for {s.binding.display_name}'
                                          for s in sources))]
    return [
        "### How this app asks for data", "",
        ("The app never sends SQL. It calls a query by NAME, and the statement lives in "
         "`.sage/queries.json`, which you write:"), "",
        "```json",
        "[",
        "  {",
        '    "name": "usage_by_account",',
        f'    "binding": "{first.binding.id}",',
        (f'    "sql": "SELECT ... FROM {_qualified(table, first.stranded)} WHERE ... >= :since",'),
        '    "params": [{ "name": "since", "type": "date" }]',
        "  }",
        "]",
        "```", "",
        *which,
        ("- Placeholders are `:name`. Every placeholder must be declared in `params`, and every "
         "declared parameter must appear in the statement — a mismatch makes the query unusable."),
        ("- A parameter's `type` is one of `string`, `int`, `float`, `bool`, `date`. A date value is "
         'written `YYYY-MM-DD`. Add `"enum": [...]` when the values are a fixed set, which is worth '
         "doing whenever they are."),
        f"- One query answers at most {max_rows} rows. Aggregate in SQL rather than in the browser.",
        ("- **`.sage/queries.json` is yours to write** — the one file under `.sage/` that is. Keep it "
         "valid JSON; a catalog that will not parse leaves the app with no queries at all."), "",
        "Call it from the app:", "",
        "```tsx",
        f'import {{ runQuery }} from "./{names.query}";   // from a subfolder: "../{names.query}"',
        "",
        'const { columns, rows } = await runQuery("usage_by_account", { since: "2026-01-01" });',
        "```", "",
        ("- **`runQuery` throws an `Error` whose `message` is written for the viewer.** Catch it and "
         "show that message as it is; do not replace it with your own wording."),
        brand.text(
            "- **Queries answer in the preview too**, against the same {dataSource}, the same "
            "Scope and the same statements the published app will use. So a query that fails while "
            "you are building is a real failure and worth fixing now — do not design a screen "
            "around it, and do not treat an empty result as the normal state. Preview answers are "
            "cached for a few seconds, so a change made in the store may take a moment to show."),
        ("- **If the data cannot be reached, the WHOLE SCREEN says so — not just the panel that "
         "asked.** This is a different state from an empty list, and it is the one that goes wrong. "
         "When `runQuery` fails, every control fed by a query is inert at the same moment: a filter "
         "whose options come from a query has no options, and a button that re-runs one does "
         "nothing. Render that, do not decorate it. Disable those controls and say why beside them "
         "— a select holding only \"All\" next to an enabled primary button tells the viewer the app "
         "works and the store is empty, and neither is true. Drop the headings and feature badges "
         "that name filters, metrics or date ranges the screen cannot currently show; they are "
         "claims, not decoration. Do not leave a filled primary button that does nothing when "
         "pressed — if the only useful action is to try again, make that the primary action. The "
         "test to apply: a viewer reads **not yet**, rather than **working, but empty**."),
        brand.text(
            "- **Do not read the {dataSource} yourself.** No scripts, no SQL anywhere except "
            "`.sage/queries.json`, and never fetch rows to see what a table holds. What is written "
            "above is what you have; if it is not enough, ask the user."),
        brand.text("- **Do not edit or re-create `{helper}`.** {assistantName} owns it, and which "
                   "{dataSourcePlural} this app reads is chosen in {assistantName}, not in code.",
                   helper=names.query_path), "",
    ]


def _qualified(table: str, stranded: list[tuple[str, str]] | None) -> str:
    """The table as the statement has to write it: bare where the Scope travels, prefixed with the
    innermost level that does not where it cannot."""
    return f"{stranded[-1][1]}.{table}" if stranded else table


def _scope_rule(binding: Binding, stranded: list[tuple[str, str]] | None, table: str) -> str:
    """The one line that decides whether the generated SQL has to qualify its table names.

    Answered from `serve.py`'s own table (#14) rather than restated here, so the sentence the agent
    is given and the check the published app makes cannot disagree. `None` means Sage could not ask,
    and then the safe instruction is the strict one: a qualified statement runs on every connector,
    where an unqualified one runs only where the Scope travels.
    """
    if stranded is None:
        return brand.text("- Write the table name qualified — `FROM {qualified}` — unless the user "
                          "says otherwise. {assistantName} could not confirm what this connector "
                          "will accept as configuration, and a qualified name works either way.",
                          qualified=f"{binding.schema or 'schema'}.{table}")
    if not stranded:
        return (f"- **Write table names unqualified** — `FROM {table}`, not "
                f"`FROM {binding.schema or 'schema'}.{table}`. This app sends its Scope to the store "
                "as configuration, so the statement does not repeat it, and a qualified name would "
                "be a second place for the same fact to go wrong.")
    levels = ", ".join(value for _, value in stranded)
    return (f"- **The statement has to name {levels} itself** — "
            f"`FROM {_qualified(table, stranded)}`. This connector will not take that part of the "
            "Scope as configuration, so a query that leaves it out is refused before it runs.")


def _samples_section(tables) -> list[str]:
    """The rows a creator chose to share, named but not quoted (#16).

    `tables` is (store, table names) per Data Source they were shared from, in Binding order.

    Named, because the agent has to know they exist to go and read them. Not quoted, because this
    region is written into AGENTS.md and AGENTS.md is committed — the whole reason the samples file is
    gitignored is that rows must not travel with the repo.

    Absent entirely when nothing was shared, which is the default and stays fully supported: the
    columns above are enough to write a working query, and #15 shipped exactly that.
    """
    groups = [(store, list(names)) for store, names in tables if names]
    if not groups:
        return []
    # Which store each shared table is in, once there is more than one. A table name alone stops
    # identifying anything the moment two stores are bound — `events` in the warehouse is not
    # `events` in the app database — and the agent reading these rows is about to write a query that
    # has to name a Binding.
    if len(groups) == 1:
        named = _and_list(groups[0][1])
    else:
        named = _and_list_of([f"{_and_list(names)} in **{store}**" for store, names in groups])
    return [
        "### Sample rows", "",
        (f"The user has chosen to show you a few real rows from {named}. They are in "
         f"`{SAMPLES_PATH}` — read that file when you need to see the shape of the data: what a code "
         "column actually contains, how a date is written, whether a column is usually empty."), "",
        ("- **A handful of rows, not a distribution.** Do not draw conclusions about totals, ranges "
         "or how many rows a table has from them, and do not write them into the app as expected "
         "values."),
        ("- **Never copy them anywhere.** Not into `src/`, not into a fixture, not into a test. The "
         "app reads the store at request time, so a copied row is both a snapshot that is wrong "
         "tomorrow and real data in a repo."),
        "",
    ]


def _and_list(names: list[str]) -> str:
    return _and_list_of([f"`{n}`" for n in names])


def _and_list_of(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _problems_section(problems: list[str] | None) -> list[str]:
    """What the app will refuse to answer, in the app's own words.

    Only ever the sentences `serve.py` itself produces. The agent is being asked to fix the thing the
    published app is going to complain about, and a paraphrase would send it after something adjacent.
    """
    if not problems:
        return []
    return [
        "### Queries this app will refuse", "",
        brand.text("{assistantName} checked `.sage/queries.json` against this app's {dataSource}. "
                   "Fix these:"), "",
        *[f"- {p}" for p in problems], "",
    ]
