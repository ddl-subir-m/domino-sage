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

from ..orchestrator import brand
from .app_helpers import TEMPLATE, HelperNames
from .bindings import KIND_MODEL_API, Binding
from .model_api_credentials import Credential

# Sage-owned, committed into the app's repo, and written as a pair: the helper imports the config.
# Their names are the app's rather than this module's (#119) — see `app_helpers`, and `pinned_model`,
# which this mirrors here as it does everywhere else.


def pinned_model_api(bindings: list[Binding]) -> Binding | None:
    """The Model API the app calls, or None if it has none."""
    return next((b for b in bindings if b.kind == KIND_MODEL_API), None)


def bound_model_apis(bindings: list[Binding]) -> list[Binding]:
    """Every Model API this app may call, in the order they were chosen (#34).

    All of them, because an app scores different rows with different models — "score table xyz with
    @model-api-1 and table abc with @model-api-2" is one request naming two. The first stays special
    only as the DEFAULT, so a call already written that names none goes on reaching the same model.
    """
    return [b for b in bindings if b.kind == KIND_MODEL_API]


def render_config(apis: list[Binding], credentials: dict[str, Credential],
                  names: HelperNames = TEMPLATE) -> str:
    """The whole text of the app's Model API config (`names.model_api_config_path`).

    A Binding without a credential is dropped rather than rendered with a null token. That pairing is
    enforced at bind time, so reaching this branch means the store was emptied under a bound app — and
    an app whose config names a URL with no token would fail at the call with nothing to say, whereas
    one that does not list the model at all sends the creator back to Sage where the fix is.

    Every entry carries its own token and every one ships in the bundle. That is the same trade #9
    made for one model, taken once per model — the creator was told as much before each paste.

    `name`/`url`/`token` repeat the FIRST entry beside `models`, so an app seeded before #34 keeps a
    helper that reads the scalars and goes on calling the model it always called.
    """
    usable = [(a, credentials[a.id]) for a in apis if credentials.get(a.id) is not None]
    first = usable[0] if usable else None
    entries = ",\n".join(
        f"    {{ name: {json.dumps(a.display_name)}, url: {json.dumps(c.url)}, "
        f"token: {json.dumps(c.token)} }}"
        for a, c in usable
    )
    body = ",\n".join(
        f"  {key}: {json.dumps(value)}"
        for key, value in (
            ("name", first[0].display_name if first else None),
            ("url", first[1].url if first else None),
            ("token", first[1].token if first else None),
        )
    )
    models = f"\n  models: [\n{entries},\n  ],\n" if usable else "\n  models: [],\n"
    header = brand.text(
        "// Written by {assistantName} — do not edit. {assistantName} rewrites this file whenever "
        "the app's {resourcePlural} change.\n"
        "//\n"
        "// `models` is every {modelApi} this app may call — pass one by name to `callModelApi`. "
        "Each carries\n"
        "// its own `token`, and every one of them is compiled into the app's bundle, so ANYONE "
        "WHO OPENS THE\n"
        "// PUBLISHED APP CAN READ THEM and call those models until each is regenerated from its "
        "{modelApi}'s\n"
        "// Settings page in {platformName}. That is how {platformName}'s own sample calls a "
        "{modelApi} from a page: the model\n"
        "// has no other credential, and a {platformName} session will not open one.\n"
        "//\n"
        "// `name`/`url`/`token` repeat the first entry. null means no {modelApi} has been chosen "
        "yet.\n"
        "// See ./{helper}.ts.\n",
        helper=names.model_api,
    )
    return f"{header}export const {names.model_api}Config = {{\n{body},{models}}};\n"


def agents_block(apis: list[Binding], credentials: dict[str, Credential],
                 names: HelperNames = TEMPLATE) -> str:
    """What the agent is told about the app's Model APIs, for the managed AGENTS.md region.

    Empty when nothing is callable, for the reason the Alias block is: describing machinery that is
    not there costs context on every turn and invites a call that cannot run. A Binding whose token
    has gone is not described either — the config drops it, so a call naming it would be refused.

    Prescriptive about the two things that go wrong. Left alone, an agent writes its own `fetch` with
    the URL and a placeholder key — and the real token is right there in the config, so the
    placeholder version fails in a way that looks like a credential problem rather than a code one.
    And it invents an input shape, which no Model API publishes: nothing in Domino exposes what the
    deployed function takes, so the shape has to come from the creator or from the model's own error.
    """
    usable = [a for a in apis if credentials.get(a.id) is not None]
    if not usable:
        return ""
    several = len(usable) > 1
    head = [brand.text("## The app's {modelApiPlural}") if several
            else brand.text("## The app's {modelApi}"), ""]
    if several:
        head += [
            brand.text("This app can call any of the {modelApiPlural} below. `{helper}` already "
                       "knows each one's URL and holds its access token — call it, and never write "
                       "a URL, token or `fetch` of your own:",
                       helper=names.model_api_path), "",
        ]
        for i, a in enumerate(usable):
            note = " — the default, used by any call that names no model" if i == 0 else ""
            head.append(f"- **{a.display_name}** — `model: {json.dumps(a.display_name)}`{note}")
        head.append("")
    else:
        head += [
            brand.text("This app calls the {modelApi} **{name}**. `{helper}` already knows its URL "
                       "and holds its access token — call it, and never write a URL, token or "
                       "`fetch` yourself:",
                       name=usable[0].display_name, helper=names.model_api_path), "",
        ]
    code = [
        "```tsx",
        (f'import {{ callModelApi, ModelApiError }} from "./{names.model_api}";'
        f'  // from a subfolder: "../{names.model_api}"'),
        "",
        "const result = await callModelApi({ score: 0.9 });  // whatever this model's function takes",
    ]
    if several:
        code += [
            "",
            "// Another of this app's models, for the rows that are that model's job:",
            f'const other = await callModelApi(row, {{ model: {json.dumps(usable[1].display_name)} }});',
        ]
    code += ["```", ""]
    rules = []
    if several:
        rules.append(
            "- **Use only the names listed above, and be deliberate about which.** Two models take "
            "different inputs and mean different things, so a prediction from the wrong one is a "
            "wrong answer rendered as a right one. `callModelApi` refuses a name this app does not "
            "use rather than falling back to the default.")
    rules += [
        (brand.text(
            "- **The argument is the model's own input, and {assistantName} does not know its "
            "shape.** {platformName} publishes no signature for a {modelApi}, so ask the person "
            "building the app what the model takes rather than guessing. `callModelApi` wraps "
            "whatever you pass as {platformName}'s `{\"data\": …}` envelope and returns the "
            "`result` out of the response.")
         + (" Each model has its own shape; do not assume one takes what another does."
            if several else "")),
        ("- **`callModelApi` throws a `ModelApiError` whose `message` is written for the viewer.** Show "
         "that message as it is. When the model itself rejected the request, `error.detail` carries the "
         "model's own words — render it in a monospace block, unedited, because it is the only thing "
         "that says which argument was wrong."),
        ("- **Show the failure, never swallow it.** A prediction that quietly renders nothing looks "
         "like an app with no data. Catch the error and put the message on the screen."),
        brand.text("- **Do not edit or re-create `{helper}` or `{config}`.** {assistantName} owns "
                   "both, rewrites them, and which {modelApiPlural} this app uses is chosen in "
                   "{assistantName}, not in code.",
                   helper=names.model_api_path, config=names.model_api_config_path), "",
    ]
    return "\n".join(head + code + rules)
