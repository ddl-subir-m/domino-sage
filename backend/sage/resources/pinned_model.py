"""The one LLM Alias a Built App calls, pinned into the app's own source (#7).

A Binding is a record (#6). This is what turns one of those records into something the published app
can actually call: the Alias name is written into `src/sageLlm.config.ts`, committed to the app's
repo, and the template's `src/sageLlm.ts` calls the gateway with it straight from the viewer's
browser.

Pinned, and pinned in the app's OWN repo, for two reasons. The app answers the same way for everyone
who opens it, rather than resolving a model per viewer. And the published app has no Sage around it
and no environment of its own to read — `app.sh` runs `vite build` in a container that has never
heard of `GATEWAY_BASE_URL`, so anything the app needs at runtime has to be in the repo before it
ships.

FIRST in manifest order wins, not last. Bindings are a set of recorded dependencies and a creator
may hold more than one; the app calls one of them. Anchoring on the first means adding a second
Binding cannot silently change what an already-built app does — the rail badges the pinned row, and
removing it is what moves the pin.

Pure functions on purpose, like `preflight`: every decision here is a string transformation over an
already-read manifest, so the writer's I/O and the rendering are testable apart.
"""
from __future__ import annotations

import json

from .bindings import KIND_LLM_ALIAS, Binding

# Sage-owned, committed into the app's repo, and both are needed for the app to build: the helper
# imports the config. Written as a pair, never one without the other.
CONFIG_PATH = "src/sageLlm.config.ts"
HELPER_PATH = "src/sageLlm.ts"


def pinned_alias(bindings: list[Binding]) -> Binding | None:
    """The LLM Alias the app calls, or None if it has none."""
    return next((b for b in bindings if b.kind == KIND_LLM_ALIAS), None)


def bound_aliases(bindings: list[Binding]) -> list[Binding]:
    """Every LLM Alias this app may call, in the order they were chosen (#34).

    All of them, not the first alone: a creator binds several because the app has several jobs, and
    which one a call is for is decided in the prompt (`@sonnet` to summarise, `@gpt5.4` to cluster).
    The first stays special only as the DEFAULT — what a call naming no model gets — so app code
    written before a second Alias arrived goes on meaning what it meant.
    """
    return [b for b in bindings if b.kind == KIND_LLM_ALIAS]


def render_config(aliases: list[Binding], base: str | None, project: str | None) -> str:
    """The whole text of `src/sageLlm.config.ts`.

    A generated TS module rather than JSON, because `resolveJsonModule` is off in the template's
    tsconfig and turning it on to carry four fields would change how every app compiles.

    Nothing is annotated with a type: an unannotated `alias: null` widens where the helper assigns it
    to its own `Config`, whereas a type declared here would have to be kept in step with the helper
    from inside a Python string.

    `json.dumps` per value, so an Alias whose display name carries a quote or a backslash cannot end
    the literal early — these strings come from a gateway's registration records, not from us.

    `alias`/`displayName` carry the FIRST entry as well as `models` carrying all of them. Two shapes
    for one fact, on purpose: an app seeded before #34 has a helper that reads the scalars, and it
    would stop compiling the moment Sage rewrote this file without them. It goes on calling the
    default, which is what it did before.

    That helper is now replaced rather than left standing — `ensure_llm_helper` has refreshed since
    `e83f3d0`, and #40 put the rest of Sage's own sources on the same footing — so the copy these
    scalars protect is one attach away from being gone. They stay because the refresh is not a
    guarantee: `_ensure_helper` returns False when the template file cannot be read, and two fields
    are a cheap thing to be wrong about in the safe direction.
    """
    default = aliases[0] if aliases else None
    entries = ",\n".join(
        f"    {{ alias: {json.dumps(a.name)}, displayName: {json.dumps(a.display_name)} }}"
        for a in aliases
    )
    body = ",\n".join(
        f"  {key}: {json.dumps(value)}"
        for key, value in (
            ("alias", default.name if default else None),
            ("displayName", default.display_name if default else None),
            ("base", base if default else None),
            ("project", project if default else None),
        )
    )
    models = f"\n  models: [\n{entries},\n  ],\n" if aliases else "\n  models: [],\n"
    return (
        "// Written by Sage — do not edit. Sage rewrites this file whenever the app's Resources change.\n"
        "//\n"
        "// `models` is every LLM Alias this app may call — pass one by name to `askModel`. `alias` is\n"
        "// the first of them, the model a call that names none gets. null means no model has been\n"
        "// chosen yet. See ./sageLlm.ts.\n"
        f"export const sageLlmConfig = {{\n{body},{models}}};\n"
    )


def agents_block(aliases: list[Binding], sources: list[Binding]) -> str:
    """What the agent is told about the app's models, for the managed AGENTS.md region.

    Empty when nothing is bound: an agent told about a model that is not there would write a call
    that cannot run, and describing the machinery costs context on every turn for nothing.

    `sources` is the app's Data Source Bindings, and it only ever decides whether the closing
    paragraph is written. That paragraph fires on the LOCAL join — at least one store and at least
    one Alias — and is worded as a conditional, because this file is a function of the disk and the
    disk cannot say whether an Alias is Domino-hosted. See `_egress_note`.

    Prescriptive about the two things an agent gets wrong when left to itself. It invents a `fetch`
    with a hardcoded model name, URL and — worst — a placeholder API key, none of which can work from
    a browser. And it skips the availability check, which is invisible to whoever built the app
    (their own access is why they could pick the Alias at all) and breaks for the colleague they send
    it to.

    One Alias reads exactly as it did before #34. The selector is only introduced when there is
    something to select: an app with one model gains nothing from a paragraph about picking between
    models, and every turn pays for that paragraph.
    """
    if not aliases:
        return ""
    default = aliases[0]
    several = len(aliases) > 1
    head = ["## The app's language models" if several else "## The app's language model", ""]
    if several:
        # The exact string to pass, per Alias. An agent that has to derive the name from a display
        # name gets it wrong for every Alias registered under a different one, and the call fails at
        # request time with a message about a model the creator never named.
        head += [
            ("This app can call any of the LLM Aliases below. `src/sageLlm.ts` already knows how to "
             "reach them — call it, and never write a model name, gateway URL or API key of your own:"),
            "",
        ]
        for i, a in enumerate(aliases):
            label = f"`alias: {json.dumps(a.name)}`"
            note = " — the default, used by any call that names no model" if i == 0 else ""
            head.append(f"- **{a.display_name}** — {label}{note}")
        head.append("")
    else:
        head += [
            (f"This app calls the LLM Alias **{default.display_name}**. `src/sageLlm.ts` already knows "
             "which model that is and how to reach it — call it, and never write a model name, gateway "
             "URL or API key yourself:"),
            "",
        ]
    code = [
        "```tsx",
        'import { askModel, checkModel } from "./sageLlm";  // from a subfolder: "../sageLlm"',
        "",
        'const answer = await askModel([{ role: "user", content: question }]);',
    ]
    if several:
        code += [
            "",
            "// Another of this app's models, for a call that is that model's job:",
            f'const clustered = await askModel(messages, {{ alias: {json.dumps(aliases[1].name)} }});',
        ]
    code += [
        "",
        "// Streams instead, when you want the answer to appear as it is written:",
        "await askModel(messages, { onToken: (t) => setAnswer((a) => a + t) });",
        "```", "",
    ]
    rules = []
    if several:
        # An invented name throws rather than quietly answering from the default, so this rule is
        # about a failed screen rather than a wrong one — but the failure reaches the viewer.
        rules.append(
            "- **Use only the Alias names listed above.** `askModel` refuses any other name rather "
            "than falling back to the default, because an answer that silently came from a different "
            "model is a wrong answer nobody can see.")
    rules += [
        ("- **Check on load and show the result.** "
         + (f'`const status = await checkModel({json.dumps(default.name)});`' if several
            else "`const status = await checkModel();`")
         + " — when `status.ok` is false, render `status.message` instead of the model UI. Whether "
           "this model is available depends on who opens the app, not on the app, so it works for the "
           "person who built it and can still fail for the person they share it with. Telling them on "
           "load beats a button that fails when they press it."
         + (" Each Alias is a separate answer: check the ones a screen actually uses." if several else "")),
        ("- **`askModel` throws an `Error` whose `message` is written for the viewer.** Catch it and "
         "show that message as it is; do not replace it with your own wording."),
        ("- The call goes from the viewer's browser to Domino's LLM Gateway under the viewer's own "
         "Domino identity. There is no key to add, no server to write, and no CORS to configure."),
        # Said for the same reason the query block says it (#7). A model call used to fail in the
        # preview whatever the app did — cross-origin — so an agent that saw it fail could reasonably
        # build a screen around the model being unreachable. Now that it answers, a failure is a real
        # one, and designing around it would hide a bug instead of reporting it.
        ("- **The model answers in the preview too**, so a call that fails while you are building is "
         "a real failure worth fixing now. Do not design a screen around the model being "
         "unavailable, and do not treat an empty answer as the normal state."),
        ("- **Do not edit or re-create `src/sageLlm.ts` or `src/sageLlm.config.ts`.** Sage owns both, "
         "rewrites them, and which models this app uses is chosen in Sage, not in code."), "",
    ]
    return "\n".join(head + code + rules + _egress_note(sources))


def _egress_note(sources: list[Binding]) -> list[str]:
    """Where this app's data goes when it calls a model — the consequence, not a rule (#35).

    A CONSEQUENCE because ADR-0012 left no rule to state: no store-and-Alias combination is refused,
    the administrator who registered that Alias made it callable on purpose, and the creator is told
    at publish and decides. An agent handed a prohibition it can see the app violating on every turn
    learns to route around the block, which is the failure the ADR rejected a blanket refusal over.

    CONDITIONAL because it is honest at this precision and no more. `_write_app_model` runs on every
    Binding change and is deliberately a function of the disk, so it is handed no listing and cannot
    tell a vendor-backed Alias from a Domino-hosted one. The fix is NOT a `sovereign` field on the
    Binding record: an Alias's hosting is a live fact and `.sage/bindings.json` is committed to the
    creator's repo, so it would go stale in the one place nobody re-reads. (`connector_type` is the
    counter-precedent and does not carry — a connector's shape does not change under you.)

    Fires on the join alone. An app with a model and no store has nothing to send it, and an app
    with a store and no model never leaves the platform, so neither pays for this paragraph — the
    Alias half of the join is `agents_block`'s own early return, which is why only the store half is
    asked here.
    """
    if not sources:
        return []
    return [
        "### Where this app's data goes",
        "",
        ("This app reads a Data Source and calls a model, so rows from that store go wherever the "
         "model runs. An LLM Alias hosted on Domino answers inside the platform; one that is not — "
         "which most are — answers outside it, and whatever a prompt carries goes with it. Once the "
         "app is published that happens for every viewer, unattended, rather than under the "
         "creator's eye. The creator is told this before publishing and decides."),
        "",
        ("A screen that sends whole rows sends more than one that sends the columns it shows. Both "
         "are allowed, and they are not the same amount of data leaving Domino."),
        "",
    ]
