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


def render_config(alias: Binding | None, base: str | None, project: str | None) -> str:
    """The whole text of `src/sageLlm.config.ts`.

    A generated TS module rather than JSON, because `resolveJsonModule` is off in the template's
    tsconfig and turning it on to carry four fields would change how every app compiles.

    Nothing is annotated with a type: an unannotated `alias: null` widens where the helper assigns it
    to its own `Config`, whereas a type declared here would have to be kept in step with the helper
    from inside a Python string.

    `json.dumps` per value, so an Alias whose display name carries a quote or a backslash cannot end
    the literal early — these strings come from a gateway's registration records, not from us.
    """
    body = ",\n".join(
        f"  {key}: {json.dumps(value)}"
        for key, value in (
            ("alias", alias.name if alias else None),
            ("displayName", alias.display_name if alias else None),
            ("base", base if alias else None),
            ("project", project if alias else None),
        )
    )
    return (
        "// Written by Sage — do not edit. Sage rewrites this file whenever the app's Resources change.\n"
        "//\n"
        "// `alias` is the LLM Alias name this app calls, pinned when it was chosen so the app answers the\n"
        "// same way for everyone who opens it. null means no model has been chosen yet. See ./sageLlm.ts.\n"
        f"export const sageLlmConfig = {{\n{body},\n}};\n"
    )


def agents_block(alias: Binding | None) -> str:
    """What the agent is told about the app's model, for the managed AGENTS.md region.

    Empty when nothing is pinned: an agent told about a model that is not there would write a call
    that cannot run, and describing the machinery costs context on every turn for nothing.

    Prescriptive about the two things an agent gets wrong when left to itself. It invents a `fetch`
    with a hardcoded model name, URL and — worst — a placeholder API key, none of which can work from
    a browser. And it skips the availability check, which is invisible to whoever built the app
    (their own access is why they could pick the Alias at all) and breaks for the colleague they send
    it to.
    """
    if alias is None:
        return ""
    return "\n".join([
        "## The app's language model", "",
        (f"This app calls the LLM Alias **{alias.display_name}**. `src/sageLlm.ts` already knows which "
         "model that is and how to reach it — call it, and never write a model name, gateway URL or API "
         "key yourself:"), "",
        "```tsx",
        'import { askModel, checkModel } from "./sageLlm";  // from a subfolder: "../sageLlm"',
        "",
        "const answer = await askModel([{ role: \"user\", content: question }]);",
        "",
        "// Streams instead, when you want the answer to appear as it is written:",
        "await askModel(messages, { onToken: (t) => setAnswer((a) => a + t) });",
        "```", "",
        ("- **Check on load and show the result.** `const status = await checkModel();` — when "
         "`status.ok` is false, render `status.message` instead of the model UI. Whether this model is "
         "available depends on who opens the app, not on the app, so it works for the person who built "
         "it and can still fail for the person they share it with. Telling them on load beats a button "
         "that fails when they press it."),
        ("- **`askModel` throws an `Error` whose `message` is written for the viewer.** Catch it and "
         "show that message as it is; do not replace it with your own wording."),
        ("- The call goes from the viewer's browser to Domino's LLM Gateway under the viewer's own "
         "Domino identity. There is no key to add, no server to write, and no CORS to configure."),
        ("- **Do not edit or re-create `src/sageLlm.ts` or `src/sageLlm.config.ts`.** Sage owns both, "
         "rewrites them, and the model this app uses is chosen in Sage, not in code."), "",
    ])
