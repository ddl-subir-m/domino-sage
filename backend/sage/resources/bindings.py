"""Bindings — the recorded link between a Built App and a Resource it uses (#6).

A Binding is a record, not a wiring. It says "this app depends on this Resource", so a creator can
see what the app gained and an auditor can read the list. Nothing in routing, in the agent's prompt
or in the published app reads it yet.

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

# The only kind #6 can create. Other Resource kinds join this vocabulary as they arrive.
KIND_LLM_ALIAS = "llm_alias"


@dataclass(frozen=True)
class Binding:
    kind: str
    id: str
    name: str
    display_name: str

    @property
    def key(self) -> tuple[str, str]:
        """What makes two Bindings the same one. An id is only unique within its kind."""
        return (self.kind, self.id)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id, "name": self.name, "display_name": self.display_name}


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
        out.append(Binding(kind, rid, name, str(e.get("display_name") or name)))
    return out
