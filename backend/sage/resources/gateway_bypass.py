"""App source that calls Domino's LLM Gateway itself instead of through `askModel` (#94).

`src/appLlm.ts` already refuses an Alias the app never declared — `pick` returns null and the call
throws a sentence written for the viewer — so the declaration does gate a model call made THROUGH
the helper. What survives is the call that goes around it: an app that declares at least one Alias
has a live gateway URL in `src/appLlm.config.ts`, and nothing stops a `fetch` at it.

The record is the least of what that loses. A raw call also drops the `X-LLM-Tag-sage-*` cost tags,
which are the only thing saying this app's spend came from Sage at all; the viewer-readable error
messages; session-expiry detection; and streaming.

The shape that nothing else reports is the mirror: `endpoint()` routes to the preview proxy
`/api/llm` while the app is being built, so an agent that copies what it sees working writes a call
that works in the preview and breaks the moment the app is published.

Pure functions, like `publish_egress` next door: the caller reads the disk and hands the answers in,
so what the creator reads is testable without a workspace and the scan can make no gateway call of
its own. `askModel` is strictly better in every respect, so nothing here gates anything — the
findings feed a bounded fix nudge, on `_detect_leaks`'s precedent.
"""
from __future__ import annotations

import re

# The dev-server path `src/appLlm.ts` routes to while the app is previewed. It is Sage's proxy and
# it is not there once the app ships, so this one in app source is the variant that passes every
# check the creator can run and fails only for the people they published it for.
DEV_PROXY_PATH = "/api/llm"
# The gateway's own path, for a call written against a host the app worked out for itself rather
# than against the base Sage pinned.
COMPLETIONS_PATH = "/v1/chat/completions"

# The model an OpenAI-shaped request body asks for. The word boundary keeps a neighbouring
# `submodel:` out.
_MODEL_FIELD = re.compile(r"""["']?\bmodel["']?\s*:\s*["']([^"']+)["']""")
# How far from the flagged URL a `model:` field still belongs to the call at it. Read in a window
# rather than out of the whole file, because what this name earns is a sentence telling a person to
# go and bind an Alias: a `model: "linear-fit"` in a chart config three hundred lines away would
# send them on an errand with nothing at the end of it. Both directions, since a request body is as
# often assembled above the `fetch` as written inside it.
_CALL_WINDOW = 600


def raw_gateway_calls(
    sources: list[tuple[str, str | None]],
    declared: list[str],
    base: str | None,
    owned: frozenset[str],
) -> list[tuple[str, list[str]]]:
    """(source file, [Alias names it asks for that this app does not declare]) per flagged file.

    `_detect_leaks`'s return shape, so the build loop's block beside it reads the same way.

    `sources` is `_scan_app_sources`'s answer — files with no text are non-code files and cannot
    hold a call. `declared` is the Alias names from `.sage/bindings.json`, which the caller has
    already read; nothing here asks a gateway what exists. `base` is the URL pinned into
    `src/appLlm.config.ts`, and is None for an app with no model — which has no gateway URL in its
    source to fetch, so there is nothing of that shape to find.

    `owned` is `HelperNames.owned` for the app in hand. Its LLM helper is the legitimate caller and
    holds all three shapes by definition; flagging it would be the scan reporting what it protects.

    A substring match, so what it really finds is a gateway URL in a file rather than a proven call
    — a source that merely PRINTS the base is flagged too. That is `_detect_leaks`'s bargain, taken
    on purpose: it flags any file carrying an attachment's name without reading it either. Nothing
    downstream gates, the nudge is bounded, and a gateway URL sitting in an app's source with no
    call behind it is not a thing worth being right about at the cost of missing the calls.
    """
    marks = [m for m in (base, DEV_PROXY_PATH, COMPLETIONS_PATH) if m]
    out: list[tuple[str, list[str]]] = []
    for rel, text in sources:
        if text is None or rel in owned:
            continue
        hits = [m for m in marks if m in text]
        if not hits:
            continue
        out.append((rel, sorted(_models_near(text, hits) - set(declared))))
    return out


def _models_near(text: str, marks: list[str]) -> set[str]:
    """The models named close enough to a flagged URL to be that call's own (see `_CALL_WINDOW`)."""
    found: set[str] = set()
    for mark in marks:
        at = text.find(mark)
        while at != -1:
            window = text[max(0, at - _CALL_WINDOW):at + len(mark) + _CALL_WINDOW]
            found.update(_MODEL_FIELD.findall(window))
            at = text.find(mark, at + 1)
    return found


def unbound_alias_notice(calls: list[tuple[str, list[str]]]) -> str | None:
    """The sentence to show the creator, or None when the findings need no person.

    Only fires for an Alias the app does not declare, because that is the only half of this the
    agent cannot finish alone: binding one is a person's act (ADR-0010), and rewriting the call to
    `askModel` — which is the whole fix for a declared Alias — makes it throw for an undeclared one.
    So the app goes from quietly working off the record to honestly broken until somebody binds it,
    and this is what says so.

    Deliberately NOT a prompt. Nothing here records a Binding, offers to, or waits for an answer:
    the agent's nudge runs either way and the turn completes.
    """
    names = sorted({a for _, aliases in calls for a in aliases})
    if not names:
        return None
    files = sorted({f for f, aliases in calls if aliases})
    several = len(names) > 1
    return (
        f"{_join(files)} asks Domino's LLM Gateway for {_join(names)}, which "
        f"{'are models' if several else 'is a model'} this app is not set up to use. The call is "
        f"being rewritten to go through askModel, which refuses a model the app does not have — so "
        f"if this app should have {'them' if several else 'it'}, open the Resources panel and "
        f"choose Use on the LLM {'Aliases' if several else 'Alias'}."
    )


def _join(names: list[str]) -> str:
    """`publish_egress._phrase`'s comma-and-`and` list, without its leading noun — these are file
    paths and model names, which name themselves."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"
