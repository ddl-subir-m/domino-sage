"""Performing one Delegated model call (ADR-0057).

What `mcp.handle` calls when a tool call arrives. Everything this needs about the world is injected
on `Turn`, so the rule can be tested with no gateway, no workspace and no network — the shape
`liveread/run.py` already has, and for the same reason: resolving an Alias and reading the
sensitivity gate are things only the orchestrator knows how to do.

Every path returns text the ASSISTANT reads, including every refusal. A refusal is a sentence the
person is owed; returned as a protocol failure it would leave the assistant to invent why it could
not answer, which is the transcript #370 opens with — an agent that read `appLlm.ts`, concluded it
needed a browser, and told the person so.

THREE REFUSALS, AND NONE OF THEM SUBSTITUTES. A person who asked for `opus` and got an answer from
something else with no sentence saying so is the defect #293 and #317 are both open about, so each
refusal below names what it refused and what the caller may have instead.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ..orchestrator import brand

log = logging.getLogger("sage.delegated")

# The filename carries the tool name (OpenCode names a default-export tool after its file), so this
# has to agree with `tools/delegated_model_call.ts` and with every prompt that teaches it.
# `test_a_delegated_model_call_is_named_the_same_either_way` pins the three together.
TOOL_NAME = "delegated_model_call"

# The most a Delegated model call may ask for in one answer. Not a bound on spend — the per-turn
# call cap is that — but on how much of one answer lands back in the turn's context. A pass over
# many cases is many small answers, and the model asking for one enormous one is the shape that
# makes the turn's own context the problem rather than the work.
MAX_TOKENS_CEILING = 4096
DEFAULT_MAX_TOKENS = 1024


@dataclass(frozen=True)
class Turn:
    """What one Delegated model call may see about the turn that asked for it.

    `aliases` is the grant and nothing else is: the Aliases this Conversation has been given, as
    (the name a request carries, the name the person reads). Bound, so the bind is the consent —
    never the Working set, which ADR-0020 fixed as orientation and never context.

    `refusal` non-empty means the sensitivity gate already refused this turn, or could not be read.
    Either way the call does not happen: a Delegated model call FAILS CLOSED, because failing open
    moves a person's rows to an unapproved model on the strength of a read that failed (ADR-0057).
    """

    thread_id: str
    max_calls: int
    aliases: tuple[tuple[str, str], ...] = ()
    # Aliases this Conversation names that Sage could not resolve to a callable name, by the label
    # the person reads. A different fact from "not in this conversation", and it needs its own
    # sentence: this one IS in the conversation, and telling someone it is not sends them to add a
    # thing that is already there.
    unresolved: tuple[str, ...] = ()
    # The sensitivity lock's approved set, or None where no Dataset puts this turn under one.
    approved: frozenset[str] | None = None
    refusal: str = ""
    # Take one of this turn's calls: which call this is, or None once the cap is reached. ONE
    # operation and not a count to read and a counter to raise, because OpenCode can put several
    # tool calls in one step — two of them reading "24 so far" and both going ahead is a cap that
    # holds on average. It is what queues the step line too, so the number the person reads is the
    # number that was enforced rather than a second one derived from the same events.
    reserve: Callable[[str], int | None] | None = None
    # (alias name, messages, max_tokens) -> the answer text. Raises to report a failure the
    # assistant should read as one.
    ask: Callable[[str, list[dict], int], str] | None = None
    # Kept for the log line and for the receipt; never the prompt and never the answer.
    label_for: dict[str, str] = field(default_factory=dict)


def _refused(says: str) -> str:
    """A call that did not happen. The sentence is returned unchanged, and said out loud on the way
    past.

    Logged for the reason `liveread.run._no_card` logs: a refusal and a call nobody made reach the
    person as the same thing — an answer with no model behind it — and until this line they left the
    same evidence, which is none. Never the prompt and never the answer: what is refused is named by
    its Alias, and the Alias is a name the person picked.
    """
    log.info("delegated model call: refused — %s", says)
    return says


def _alias_list(turn: Turn) -> str:
    return ", ".join(label for _, label in turn.aliases)


def perform(name: str, args: dict, turn: Turn) -> str:
    """Answer one `delegated_model_call`. Returns the text the assistant reads."""
    if name != TOOL_NAME:
        return _refused(f"There is no tool named {name}.")

    # FAIL CLOSED, and first, before anything about the arguments. A turn whose gate could not be
    # read is a turn that may be carrying declared rows, and the door the call arrived through does
    # not change that (ADR-0057).
    if turn.refusal:
        return _refused(turn.refusal)

    asked = str(args.get("alias") or "").strip()
    if not asked:
        return _refused(brand.text(
            "Name the language model to call. This conversation has: {names}.",
            names=_alias_list(turn) or "none",
        ))
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return _refused("Send the text to ask the model as `prompt`.")

    resolved = _resolve(asked, turn)
    if resolved is None:
        if not turn.aliases and not turn.unresolved:
            # Different fact and a different act: nothing was refused, there is simply nothing bound
            # yet. Naming an empty set as though it were a choice reads as a bug.
            return _refused(brand.text(
                "No language model is in this conversation, so {assistantName} has none to call. "
                "Add one with Use in this conversation, then ask again.",
            ))
        if any(asked.casefold() == label.casefold() for label in turn.unresolved):
            return _refused(brand.text(
                "{assistantName} couldn't read the list of language models this {turn}, so it "
                "could not call {asked}. Try again.",
                asked=asked,
            ))
        return _refused(brand.text(
            "{asked} isn't a language model in this conversation. These are: {names}. "
            "Add the one you want with Use in this conversation, then ask again.",
            asked=asked, names=_alias_list(turn) or "none",
        ))

    if turn.approved is not None and resolved not in turn.approved:
        # The sensitivity lock, named model by name (ADR-0043). The set is in the sentence because
        # the alternative — refusing and saying only that — sends the agent to guess, and a guess
        # that lands is a substitution nobody agreed to.
        # Both halves in the words on screen. `ApprovedModels.names` holds gateway alias names, and
        # a sentence that refuses `Claude Opus 4.6` and then offers `gemini-2-5-pro` is naming the
        # set in a vocabulary the person's chips do not use — which is the guessing this sentence
        # exists to prevent. A name with no label known travels as itself; that is honest, and it
        # is the only thing Sage has for a model this Conversation never named.
        return _refused(brand.text(
            "{label} isn't approved for the data in this conversation, so {assistantName} did not "
            "call it. Approved here: {names}.",
            label=turn.label_for.get(resolved, resolved),
            names=", ".join(sorted(turn.label_for.get(n, n) for n in turn.approved)) or "none",
        ))

    if turn.ask is None or turn.reserve is None:
        return _refused(brand.text(
            "{assistantName} cannot reach the {llmGateway} on this turn.",
        ))

    # LAST of the refusals, so a call that was going to be refused anyway does not spend one of the
    # turn's. Loud rather than silent, and it names the number: an agent told nothing cannot tell a
    # cap from a model with nothing to say, and uncapped a delegated loop spends the whole turn
    # ceiling and produces nothing.
    if turn.reserve(turn.label_for.get(resolved, resolved)) is None:
        return _refused(brand.text(
            "This {turn} has already called a language model {n} times, which is the limit for "
            "one {turn}. Finish with what you have and say what is still unanswered.",
            n=str(turn.max_calls),
        ))

    messages: list[dict] = []
    system = str(args.get("system") or "").strip()
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # Coerced rather than type-checked. Nothing enforces a JSON schema on a model's tool arguments,
    # so `"2048"` and `2048.0` are live shapes for a field the tool declares as an integer — and
    # dropping either to the default would shorten an answer the caller asked to be longer, with no
    # word saying so. Anything that is not a whole number at all falls to the default, which is a
    # ceiling and not a promise.
    budget = _budget(args.get("max_tokens"))

    # Raises on failure rather than being caught here: `mcp.handle` owns what the assistant is told
    # about a broken call, exactly as it does for a Live read, so there is one sentence for it.
    answer = turn.ask(resolved, messages, budget)
    if not answer.strip():
        # A successful call whose content is empty, which is the case `scope.py` documents at
        # length: a route with extended thinking on spends the budget on reasoning tokens and
        # returns a perfectly successful response with `""` in it. Handed straight back, the
        # assistant has "the model's answer" and it is nothing — with a call already spent and no
        # sentence telling it what happened, which is how #29 absorbed four of these into a build.
        return _refused(brand.text(
            "{label} answered nothing. The call was made, so it still counts against this "
            "{turn}'s limit. Ask again with a larger `max_tokens`, or do the work another way — "
            "do not report an answer it did not give.",
            label=turn.label_for.get(resolved, resolved),
        ))
    return answer


def _budget(requested: object) -> int:
    """The answer budget for one call, from whatever the model sent for it.

    Via `float` and not `int`, because `int("2048.0")` raises and `2048.0` is one of the two shapes
    this exists to accept — a JSON number with a decimal point. `bool` is excluded before anything
    else: `True` is an `int` in Python and `max_tokens: 1` is not what `max_tokens: true` meant.
    """
    if isinstance(requested, bool) or requested is None:
        return DEFAULT_MAX_TOKENS
    try:
        asked = float(str(requested).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS
    if not asked.is_integer() or asked < 1:
        return DEFAULT_MAX_TOKENS
    return min(int(asked), MAX_TOKENS_CEILING)


def _resolve(asked: str, turn: Turn) -> str | None:
    """The Alias name a request carries, for whatever the model called it — or None.

    Matched on the callable name and on the label the person reads, because those are the two names
    on screen and nothing tells the model which of them the tool wants. Case-insensitively, for the
    reason `liveread.grant.reachable` folds case: the agent re-types a name it read in prose.
    """
    want = asked.casefold()
    for call_name, label in turn.aliases:
        if want in (call_name.casefold(), label.casefold()):
            return call_name
    return None
