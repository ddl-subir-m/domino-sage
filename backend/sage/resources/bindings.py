"""Bindings — the recorded link between a Built App and a Resource it uses (#6).

A Binding is a record first: it says "this app depends on this Resource", so a creator can see what
the app gained and an auditor can read the list. Since #7 the first LLM Alias in the list is also the
model the published app calls — `resources.pinned_model` pins it into the app's own source — while
Sage's own routing and the model slots it uses are untouched by any of this.

Why its own manifest, and not another entry kind in `.sage/attachments.json`: that file's consumer
is the template's `scripts/rehydrate-data.mjs`, which `continue`s past any entry without
`path`/`dataset`/`dataset_rel_path` and says nothing. A Binding stored there would be dropped in
silence by a script whose whole job is to notice files that failed to arrive.

The record keeps the alias `name` and the `display_name` the row showed when the choice was made.
That is deliberate: the Bindings group has to render when the gateway is unreachable, which is
exactly when knowing what the app depends on matters most. It also means the label can go stale —
the browsable list below it is the live view, the manifest is the record of what was chosen.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..orchestrator import brand

# The kinds Sage records. Other Resource kinds join this vocabulary as they arrive.
KIND_LLM_ALIAS = "llm_alias"
# A Model API Binding (#9) is a record only, for now: the first LLM Alias is pinned into the app's
# source, but nothing yet writes the code that calls a Model API, because how a browser reaches one
# is unverified — a Model API is served from the main Domino host, not from the `apps.` host the
# published page and the LLM Gateway share, so #7's same-origin recipe does not carry over untested.
KIND_MODEL_API = "model_api"
# A Data Source Binding (#11) records a scope as well as a Resource. A Data Source on its own names a
# store, not the part of it an app reads, and the live warehouse is the whole company's — so "this app
# uses Snowflake-Data-Warehouse" is not yet a choice anyone can act on. The cascade that produces the
# scope runs in the build session, where querying already works; nothing here makes the Built App
# query, which is a later slice.
KIND_DATA_SOURCE = "data_source"
# A Dataset Binding (#141) records that the app depends on one Domino Dataset. Not the same record as
# an Attachment, and it does not replace one: an Attachment is a FILE copied into the app's own tree
# and rebuilt at deploy time, while this says which Dataset the app is recorded as reading. The
# glossary's rule — "a file never becomes a Binding" — holds, because a Dataset is not a file.
#
# Inert in every consumer that reads Bindings by kind: nothing is pinned into the app's source, no
# scope travels with it, and `stale_bindings` is handed no listing for it, so it can never be called
# gone by a listing that was never fetched.
KIND_DATASET = "dataset"


@dataclass(frozen=True)
class Binding:
    kind: str
    id: str
    name: str
    display_name: str
    # Where inside the Resource the choice landed (#11). Only a Data Source has these, and only down
    # to the level the creator actually reached: stopping at a schema is a real choice — a narrower
    # scope than the source and a wider one than a single table — so `table` unset means "the schema",
    # not "unfinished". All three stay `None` for every other kind.
    database: str | None = None
    schema: str | None = None
    table: str | None = None
    # Domino's own `dataSourceType` ("SnowflakeConfig"), recorded because the Built App has to decide
    # something offline that only this answers: whether the chosen Scope can travel as a configuration
    # override on the query (#14). The SDK's config classes differ per connector — three of them carry
    # a schema, most carry none — so a published app with no Sage around it and no network at boot can
    # still say which of its queries are honest. Recorded rather than asked at query time for the same
    # reason `display_name` is: the record has to read when the network does not.
    connector_type: str = ""

    @property
    def key(self) -> tuple[str, str]:
        """What makes two Bindings the same one. An id is only unique within its kind.

        Deliberately excludes the scope. Choosing a different schema in the same Data Source is a
        creator correcting one choice, not adding a second dependency, so it has to replace the
        record in place — which is what lets the choice change without the Resource being unpicked
        and picked again.
        """
        return (self.kind, self.id)

    @property
    def scope(self) -> str:
        """The chosen scope as one dotted label, or "" for a kind that has none.

        Joined from whichever levels are set, so a store with no database level reads `public.events`
        rather than `..events`.
        """
        return ".".join(p for p in (self.database, self.schema, self.table) if p)

    def to_dict(self) -> dict:
        """The manifest entry and the HTTP row, one shape.

        The scope keys are omitted when unset rather than written as nulls. This manifest is
        committed to the creator's own app repo, so three nulls added to every LLM Alias entry that
        already exists would show up as a dirty file in an app nobody had touched.
        """
        out = {"kind": self.kind, "id": self.id, "name": self.name, "display_name": self.display_name}
        for key, value in (("database", self.database), ("schema", self.schema),
                           ("table", self.table), ("connector_type", self.connector_type)):
            if value:
                out[key] = value
        return out


@dataclass(frozen=True)
class Mention:
    """One @mention in a prompt: the Binding it names, and the tables inside it it pointed at (#31).

    A table rides the Binding rather than standing on its own because that is what it is — not a
    Resource Sage can be bound to, but a place inside one. Keeping them apart is what lets the note
    say the true thing: a mentioned table narrows the REQUEST, while the Binding's scope is still
    what the published app queries.
    """

    binding: Binding
    tables: tuple[str, ...] = ()


def scope_label(scope: dict | None) -> str:
    """The dotted Scope out of the loose dict a browser row carries, or "" when it names none.

    `Binding.scope` does the same join over a recorded Binding. This is the same rule against the
    other shape — the `{database, schema, table}` dict a Session-context row and a bind payload both
    carry — and it lives here so the two cannot drift apart. Two callers that could not import each
    other were the reason it was worth moving: `service` renders a Chat context line with it, and
    `handoff` puts it in the digest a planner reads.
    """
    if not isinstance(scope, dict):
        return ""
    return ".".join(
        str(p) for p in (scope.get("database"), scope.get("schema"), scope.get("table")) if p
    )


def parse_bindings(raw: object) -> list[Binding]:
    """Bindings from a manifest body, in file order.

    An entry with no kind or no id names nothing and is dropped. Everything else is kept, including
    a kind this Sage does not render — a newer Sage's record is not this one's to delete.
    """
    if not isinstance(raw, list):
        return []
    out: list[Binding] = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        kind, rid = str(e.get("kind") or ""), str(e.get("id") or "")
        if not kind or not rid:
            continue
        name = str(e.get("name") or rid)
        out.append(Binding(kind, rid, name, str(e.get("display_name") or name),
                           _scope_part(e, "database"), _scope_part(e, "schema"),
                           _scope_part(e, "table"), str(e.get("connector_type") or "")))
    return out


def _scope_part(entry: dict, key: str) -> str | None:
    """One level of a recorded scope, or None when the entry does not name it.

    A blank string reads back as None rather than "": the manifest omits an unset level, so a `""`
    in the file was written by something else, and an empty scope level is not a scope level.
    """
    value = entry.get(key)
    return value if isinstance(value, str) and value else None


# What each kind is called in a sentence. What the app can DO with a second one of that kind is not
# a property of the kind, so it lives in `_what_the_app_does_with` below rather than in this table.
#
# A template per kind rather than a resolved label: the pack is read when the sentence is written,
# and this table is built once at import.
_KIND_LABEL = {
    KIND_LLM_ALIAS: "{llmAlias}",
    KIND_MODEL_API: "{modelApi}",
    KIND_DATA_SOURCE: "{dataSource}",
}


def _what_the_app_does_with(b: Binding, first: Binding | None) -> str:
    """The sentence that says what naming THIS Resource means for the app being built.

    The three kinds answer differently, and the difference is the whole of what an agent gets wrong.
    An LLM Alias is picked per call, so the first is only a default (#34). A Data Source is picked
    per query, so the first is not even that — each query carries its Binding's id (#33). A Model API
    is one url and one token in the app's source, so a second one is a record the app cannot act on,
    and an agent not told that writes a call with no config behind it.
    """
    if b.kind == KIND_LLM_ALIAS:
        if first is None or first.key == b.key:
            return "This app's default model — the one a call that names no model reaches."
        return (f'Also callable by name — pass `alias: "{b.name}"` for the calls this request means '
                f"for it. The default stays **{first.display_name}**.")
    if b.kind == KIND_DATA_SOURCE:
        # No default to name: `serve.py` resolves each query against the Binding the query itself
        # carries, so there is no such thing as the Data Source this app reads.
        return f'Queries read it by naming `"binding": "{b.id}"`.'
    if b.kind == KIND_MODEL_API:
        if first is None or first.key == b.key:
            return brand.text("This app's default {modelApi} — the one a call that names no model "
                              "reaches.")
        return (f'Also callable by name — pass `model: "{b.display_name}"` for the predictions this '
                f"request means for it. The default stays **{first.display_name}**.")
    return "recorded as used by this app."


def mention_note(mentions: list[Mention], recorded: list[Binding]) -> str:
    """The block a turn carries when the creator @mentioned Resources in the prompt (#31), or "".

    A mention has to reach the agent as an IDENTITY, not as the text of a name. The creator picked a
    row out of a list, two Resources of different kinds can carry the same name, and a Data Source
    Binding also holds a scope — none of which survives "use BigQuery_Demo" in prose.

    How to USE each kind is deliberately not repeated here: AGENTS.md already carries a managed block
    per wired Resource, and a second copy on every mentioning turn would cost context and drift from
    it. What this block adds is which Resource was meant, plus the one thing AGENTS.md cannot say —
    that a mentioned Resource is not the one the app is wired to, since AGENTS.md only ever describes
    the wired one.
    """
    if not mentions:
        return ""
    first: dict[str, Binding] = {}
    for b in recorded:
        first.setdefault(b.kind, b)
    lines = []
    for mention in mentions:
        b = mention.binding
        kind = brand.text(_KIND_LABEL.get(b.kind, b.kind))
        # The display name is what the creator picked from; the name is what they typed after the @.
        # Both, when they differ, so neither reading of the mention is left guessing.
        name = b.display_name if b.display_name == b.name else f"{b.display_name} (`{b.name}`)"
        scope = f", scoped to `{b.scope}`" if b.scope else ""
        said = _what_the_app_does_with(b, first.get(b.kind))
        # The tables the creator reached for inside the Resource. Their columns are already in the
        # AGENTS.md data block, so this points rather than repeats — the block is re-read every turn,
        # and a second copy of the columns here would cost context to say what is already said.
        if mention.tables:
            named = ", ".join(f"`{t}`" for t in mention.tables)
            said += (f" The request is about {'the table' if len(mention.tables) == 1 else 'the tables'} "
                     f"{named} inside it, whose columns the AGENTS.md data block lists.")
        lines.append(f"- {kind} **{name}**{scope} — {said}")
    return ("The user @mentioned these Resources in the message above. This app is already recorded "
            "as using each one, and AGENTS.md says how to use it — take these as the exact Resources "
            "the request is about, and do not substitute or add another:\n" + "\n".join(lines))
