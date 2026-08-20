"""The one Model API a Built App calls, pinned into the app's own source (#9).

The Model API counterpart to `pinned_model`, and deliberately its mirror: FIRST Binding of the kind
wins, the config is a generated TS module beside a Sage-owned helper, and both are committed to the
app's repo because a published app has no Sage around it and no environment of its own to read.

Where it differs is the token, and the difference is worth stating plainly rather than discovering
in a diff. An LLM Alias needs no credential — the viewer's own Domino session authenticates that
call, so nothing secret is written. A Model API has no such path: probed exhaustively, the only
credential a Model API accepts is its own access token as `Basic base64(token:token)`, no cookie or
session will do, and the token cannot be minted (see DOMINO-PRIMITIVES.md). So the token is written
into the app's source, committed to the app's repo, and compiled into the bundle every viewer
downloads. That is Domino's own documented pattern for calling a Model API from a page, and it is a
decision with a cost: **anyone who can open the published app can read the token and reuse it**.
Whoever pastes it is told so, in the form and again in the config file's own header.

Pure functions, like `pinned_model`, so the rendering is testable without a workspace.
"""
from __future__ import annotations

import json

from .bindings import KIND_MODEL_API, Binding
from .model_api_credentials import Credential

# Sage-owned, committed into the app's repo, and written as a pair: the helper imports the config.
CONFIG_PATH = "src/sageModelApi.config.ts"
HELPER_PATH = "src/sageModelApi.ts"


def pinned_model_api(bindings: list[Binding]) -> Binding | None:
    """The Model API the app calls, or None if it has none."""
    return next((b for b in bindings if b.kind == KIND_MODEL_API), None)


def render_config(api: Binding | None, credential: Credential | None) -> str:
    """The whole text of `src/sageModelApi.config.ts`.

    A Binding without a credential renders as no Model API at all. That pairing is enforced at bind
    time, so reaching this branch means the store was emptied under a bound app — and an app whose
    config names a URL with a null token would fail at the call with nothing to say, whereas one
    that reports having no Model API sends the creator back to Sage where the fix is.
    """
    usable = api if (api is not None and credential is not None) else None
    body = ",\n".join(
        f"  {key}: {json.dumps(value)}"
        for key, value in (
            ("name", usable.display_name if usable else None),
            ("url", credential.url if usable else None),
            ("token", credential.token if usable else None),
        )
    )
    return (
        "// Written by Sage — do not edit. Sage rewrites this file whenever the app's Resources change.\n"
        "//\n"
        "// `token` is this Model API's access token. It is compiled into the app's bundle, so ANYONE\n"
        "// WHO OPENS THE PUBLISHED APP CAN READ IT and call the model with it until it is regenerated\n"
        "// from the Model API's Settings page in Domino. That is how Domino's own sample calls a Model\n"
        "// API from a page: the model has no other credential, and a Domino session will not open one.\n"
        "//\n"
        "// null means no Model API has been chosen yet. See ./sageModelApi.ts.\n"
        f"export const sageModelApiConfig = {{\n{body},\n}};\n"
    )


def agents_block(api: Binding | None) -> str:
    """What the agent is told about the app's Model API, for the managed AGENTS.md region.

    Empty when nothing is pinned, for the reason the Alias block is: describing machinery that is not
    there costs context on every turn and invites a call that cannot run.

    Prescriptive about the two things that go wrong. Left alone, an agent writes its own `fetch` with
    the URL and a placeholder key — and the real token is right there in the config, so the
    placeholder version fails in a way that looks like a credential problem rather than a code one.
    And it invents an input shape, which no Model API publishes: nothing in Domino exposes what the
    deployed function takes, so the shape has to come from the creator or from the model's own error.
    """
    if api is None:
        return ""
    return "\n".join([
        "## The app's Model API", "",
        (f"This app calls the Model API **{api.display_name}**. `src/sageModelApi.ts` already knows its "
         "URL and holds its access token — call it, and never write a URL, token or `fetch` yourself:"), "",
        "```tsx",
        'import { callModelApi, ModelApiError } from "./sageModelApi";  // from a subfolder: "../sageModelApi"',
        "",
        "const result = await callModelApi({ score: 0.9 });  // whatever this model's function takes",
        "```", "",
        ("- **The argument is the model's own input, and Sage does not know its shape.** Domino "
         "publishes no signature for a Model API, so ask the person building the app what the model "
         "takes rather than guessing. `callModelApi` wraps whatever you pass as Domino's `{\"data\": …}` "
         "envelope and returns the `result` out of the response."),
        ("- **`callModelApi` throws a `ModelApiError` whose `message` is written for the viewer.** Show "
         "that message as it is. When the model itself rejected the request, `error.detail` carries the "
         "model's own words — render it in a monospace block, unedited, because it is the only thing "
         "that says which argument was wrong."),
        ("- **Show the failure, never swallow it.** A prediction that quietly renders nothing looks "
         "like an app with no data. Catch the error and put the message on the screen."),
        ("- **Do not edit or re-create `src/sageModelApi.ts` or `src/sageModelApi.config.ts`.** Sage "
         "owns both, rewrites them, and the Model API this app uses is chosen in Sage, not in code."), "",
    ])
