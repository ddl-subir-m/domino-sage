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

from .bindings import Binding
from .provider import Column

SCHEMA_PATH = ".sage/schema.json"

# Above this many columns the region names the tables and points at the file instead of listing
# everything. Chosen against the shape of the thing being described: a single bound table is tens of
# columns and belongs in front of the agent, while a bound schema is however many tables the
# warehouse has and does not. AGENTS.md is read on every turn, so this is a recurring cost.
INLINE_COLUMN_LIMIT = 80
# And even then the table list itself is bounded, for the schema that holds hundreds.
TABLE_NAME_LIMIT = 60


def render_schema(binding: Binding, columns: list[Column]) -> str:
    """`.sage/schema.json` — the record of what was read, in table order.

    No timestamp, deliberately. This file is committed to the creator's own app repo, and a "read at"
    field would make every re-bind a diff in a file whose content had not changed.
    """
    tables: dict[str, list[dict]] = {}
    for column in columns:
        tables.setdefault(column.table, []).append({"name": column.name, "type": column.type})
    body = {
        "source": binding.name,
        "scope": binding.scope,
        "connector_type": binding.connector_type,
        "tables": [{"name": name, "columns": cols} for name, cols in tables.items()],
    }
    return json.dumps(body, indent=2) + "\n"


def parse_schema(raw: object) -> list[Column]:
    """The recorded schema back as columns, in file order. Anything unreadable is no schema, which
    renders as a region that says the columns are not known rather than one that invents them."""
    if not isinstance(raw, dict):
        return []
    out: list[Column] = []
    for table in raw.get("tables") or []:
        if not isinstance(table, dict):
            continue
        name = str(table.get("name") or "")
        for column in table.get("columns") or []:
            if isinstance(column, dict) and str(column.get("name") or ""):
                out.append(Column(name, str(column["name"]), str(column.get("type") or "")))
    return out


def agents_block(binding: Binding | None, columns: list[Column],
                 stranded: list[tuple[str, str]] | None, problems: list[str] | None,
                 max_rows: int) -> str:
    """What the agent is told about the app's data, for the managed AGENTS.md region.

    Empty when no Data Source is bound. Describing the machinery for a store that is not there would
    cost context on every turn and invite an app built around data it cannot reach.

    Prescriptive about the four things an agent gets wrong when left to itself. It writes SQL into a
    component and a fetch to go with it, when the only path is a named query in a manifest. It
    qualifies — or fails to qualify — table names without knowing which the connector needs. It sees
    the preview 404 and "fixes" a query that was correct. And it reaches for the store directly to
    see what is in a table, which is the one thing the creator has not agreed to.
    """
    if binding is None:
        return ""
    lines = [
        "## The app's data", "",
        _scope_sentence(binding), "",
    ]
    lines += _tables_section(columns)
    # The example names one of this app's own tables. A generic `usage` reads as a placeholder the
    # agent has to translate, and the translation is exactly the step this block exists to remove.
    lines += _how_to_ask(binding, stranded, max_rows, columns[0].table if columns else "usage")
    lines += _problems_section(problems)
    return "\n".join(lines)


def _scope_sentence(binding: Binding) -> str:
    kind = binding.connector_type[:-6] if binding.connector_type.endswith("Config") else ""
    where = f"**{binding.scope}** in " if binding.scope else ""
    named = f"the {kind} Data Source" if kind else "the Data Source"
    return f"This app reads {where}{named} **{binding.display_name}**."


def _tables_section(columns: list[Column]) -> list[str]:
    """The tables and their columns, in full or by name, or a line saying they are not known."""
    tables: dict[str, list[Column]] = {}
    for column in columns:
        tables.setdefault(column.table, []).append(column)
    if not tables:
        return [
            ("Sage could not read what the tables in this Scope hold, so their column names are not "
             "available here. Ask the user what a table holds rather than guessing column names — a "
             "query naming a column that does not exist fails for the first person who opens the "
             "app."),
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


def _how_to_ask(binding: Binding, stranded: list[tuple[str, str]] | None, max_rows: int,
                table: str) -> list[str]:
    return [
        "### How this app asks for data", "",
        ("The app never sends SQL. It calls a query by NAME, and the statement lives in "
         "`.sage/queries.json`, which you write:"), "",
        "```json",
        "[",
        "  {",
        '    "name": "usage_by_account",',
        f'    "binding": "{binding.id}",',
        (f'    "sql": "SELECT ... FROM {_qualified(table, stranded)} WHERE ... >= :since",'),
        '    "params": [{ "name": "since", "type": "date" }]',
        "  }",
        "]",
        "```", "",
        f'- `binding` must be `"{binding.id}"`. That is this app\'s Data Source.',
        ("- Placeholders are `:name`. Every placeholder must be declared in `params`, and every "
         "declared parameter must appear in the statement — a mismatch makes the query unusable."),
        ("- A parameter's `type` is one of `string`, `int`, `float`, `bool`, `date`. A date value is "
         'written `YYYY-MM-DD`. Add `"enum": [...]` when the values are a fixed set, which is worth '
         "doing whenever they are."),
        _scope_rule(binding, stranded, table),
        f"- One query answers at most {max_rows} rows. Aggregate in SQL rather than in the browser.",
        ("- **`.sage/queries.json` is yours to write** — the one file under `.sage/` that is. Keep it "
         "valid JSON; a catalog that will not parse leaves the app with no queries at all."), "",
        "Call it from the app:", "",
        "```tsx",
        'import { runQuery } from "./sageQuery";   // from a subfolder: "../sageQuery"',
        "",
        'const { columns, rows } = await runQuery("usage_by_account", { since: "2026-01-01" });',
        "```", "",
        ("- **`runQuery` throws an `Error` whose `message` is written for the viewer.** Catch it and "
         "show that message as it is; do not replace it with your own wording."),
        ("- **Queries answer in the published app, not in the preview.** The preview serves the app "
         "with a dev server that has no query API, so `runQuery` failing there is expected and is "
         "NOT a bug to chase. Build the screen so it shows the error message and moves on."),
        ("- **Do not read the Data Source yourself.** No scripts, no SQL anywhere except "
         "`.sage/queries.json`, and never fetch rows to see what a table holds. What is written "
         "above is what you have; if it is not enough, ask the user."),
        ("- **Do not edit or re-create `src/sageQuery.ts`.** Sage owns it, and which Data Source this "
         "app reads is chosen in Sage, not in code."), "",
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
        return (f"- Write the table name qualified — `FROM {binding.schema or 'schema'}.{table}` — "
                "unless the user says otherwise. Sage could not confirm what this connector will "
                "accept as configuration, and a qualified name works either way.")
    if not stranded:
        return (f"- **Write table names unqualified** — `FROM {table}`, not "
                f"`FROM {binding.schema or 'schema'}.{table}`. This app sends its Scope to the store "
                "as configuration, so the statement does not repeat it, and a qualified name would "
                "be a second place for the same fact to go wrong.")
    levels = ", ".join(value for _, value in stranded)
    return (f"- **The statement has to name {levels} itself** — "
            f"`FROM {_qualified(table, stranded)}`. This connector will not take that part of the "
            "Scope as configuration, so a query that leaves it out is refused before it runs.")


def _problems_section(problems: list[str] | None) -> list[str]:
    """What the app will refuse to answer, in the app's own words.

    Only ever the sentences `serve.py` itself produces. The agent is being asked to fix the thing the
    published app is going to complain about, and a paraphrase would send it after something adjacent.
    """
    if not problems:
        return []
    return [
        "### Queries this app will refuse", "",
        "Sage checked `.sage/queries.json` against this app's Data Source. Fix these:", "",
        *[f"- {p}" for p in problems], "",
    ]
