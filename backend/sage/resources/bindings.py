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


# What each kind is called in a sentence, and what the app's own code does with the first record of
# that kind. The FIRST is the one the app is wired to for all three kinds — `pinned_model` writes it
# into `src/sageLlm.config.ts`, `pinned_model_api` into its own config, and `bound_schema` describes
# only the first Data Source. A creator can bind more than one of a kind, and mentioning one of the
# others is a real request, but the agent has to be told the app is not wired to it: AGENTS.md
# describes the wired one alone, so nothing else would say so.
_KIND_TEXT = {
    KIND_LLM_ALIAS: ("LLM Alias", "the model this app calls"),
    KIND_MODEL_API: ("Model API", "the Model API this app calls"),
    KIND_DATA_SOURCE: ("Data Source", "the Data Source this app queries"),
}


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
    wired: dict[str, Binding] = {}
    for b in recorded:
        wired.setdefault(b.kind, b)
    lines = []
    for mention in mentions:
        b = mention.binding
        kind, role = _KIND_TEXT.get(b.kind, (b.kind, ""))
        # The display name is what the creator picked from; the name is what they typed after the @.
        # Both, when they differ, so neither reading of the mention is left guessing.
        name = b.display_name if b.display_name == b.name else f"{b.display_name} (`{b.name}`)"
        scope = f", scoped to `{b.scope}`" if b.scope else ""
        app = wired.get(b.kind)
        if not role or app is None:
            said = "recorded as used by this app."
        elif app.key == b.key:
            said = role[0].upper() + role[1:] + "."
        else:
            said = (f"recorded, but NOT {role} — that is **{app.display_name}**. Which one the app "
                    f"uses is chosen in Sage, not in code, so do not rewire the app to this one; if "
                    f"that is what the user wants, tell them to change it in the Resources rail.")
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
