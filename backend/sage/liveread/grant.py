"""What a Live read may reach, and whether its rows may reach the model (ADR-0041).

Pure: no I/O, no provider, no filesystem. The orchestrator reads the records; this decides on them.
Kept apart from `result` because the two answer different questions — may this happen, and what did
it leave behind — and only this one has to be right about permission.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..orchestrator import brand

# The five kinds a Live read reads. A Model API and an LLM Alias are CALLED rather than read: a call
# costs money and need not repeat, and reading is what this is for. They are named here so that
# "unknown kind" and "deliberately not this one" are different answers to the person (ADR-0041).
READABLE = frozenset({"datasource", "dataset", "attachment", "upload", "artifact"})
CALLED = frozenset({"modelapi", "llmalias"})


@dataclass(frozen=True)
class Refusal:
    """Why a Live read did not happen. `says` is read by a person; `tag` is read by a rule.

    Every refusal names the missing thing and the act that fixes it. None of them names the
    mechanism: "blocked", "read-only", "no tool" and "the project text" are all the wrong answer to
    someone who asked to see a row, and the last of those is the transcript in ADR-0041.
    """

    tag: str
    says: str


def reachable(kind: str, name: str, *, bound: Iterable[str] = (), chips: Iterable[str] = ()) -> Refusal | None:
    """`None` when a Live read of `name` may go ahead, else why not.

    In range is what the Conversation or the current Built App already names — a Binding, or a
    Session context chip. Never the Working set, which ADR-0020 fixed as orientation and never
    context: a list that permits nothing would name things the assistant cannot reach.
    """
    k = (kind or "").replace("_", "").replace("-", "").casefold()
    if k in CALLED:
        return Refusal("not-readable", brand.text(
            "{assistantName} reads stores and files. A {modelApi} answers a request instead, so it "
            "is something the app you are building calls, not something to look inside."
        ))
    if k not in READABLE:
        return Refusal("not-readable", brand.text(
            "{assistantName} has nothing by that name to read here."
        ))
    named = {str(n).casefold() for n in bound} | {str(n).casefold() for n in chips}
    if (name or "").casefold() not in named:
        # Names the thing and the act, never the mechanism. The act is the glossary's own label, so
        # the sentence points at a control the person can actually see (ADR-0015).
        return Refusal("not-in-range", brand.text(
            "{assistantName} cannot reach {name} from this conversation. Use it in this "
            "conversation, and ask again.",
            name=name or "that",
        ))
    return None


def values_allowed(binding: str, table: str, *, shared: Iterable[tuple[str, str]] = ()) -> bool:
    """True where the creator already put THIS table's rows in front of the assistant.

    The record is `.sage/samples.json`, and no second door is opened beside it (ADR-0021). It is
    read tolerantly on the binding, exactly as `bound_schema.parse_samples` is: an entry written
    before #33 names no Binding, and a caller resolving one is the only thing that could have
    produced it. The table name is the part that must match.

    Fails closed. An unreadable or absent record is no shared tables, which is the safe reading —
    the assistant is shown nothing it was not certainly given.
    """
    want = (table or "").casefold()
    if not want:
        return False
    for b, t in shared:
        if (t or "").casefold() != want:
            continue
        if not b or not binding or str(b) == str(binding):
            return True
    return False
