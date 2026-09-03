"""Why `provider.sage-gateway.options.name` is the string "google" and not our own provider id.

Gemini attaches `thought_signature` to every tool call it makes and rejects the next request that
does not echo it back — `400 "Function call is missing a thought_signature in functionCall parts"`,
on the FIRST tool result, so every agentic build died on its first read. The signature is
cryptographically validated, so it has to be replayed verbatim; a bogus or empty one fails too.

OpenCode bundles `@ai-sdk/openai-compatible`, and both halves of that round-trip exist but key
asymmetrically (read out of the compiled OpenCode 1.18.4 binary, 2026-09-03): the response is
stored under `providerMetadata[<options.name>]`, while the request reads
`providerOptions.google.thoughtSignature` — `google` hardcoded. Left at the provider id it writes
to `sage-gateway` and reads from `google`, so the signature is silently dropped on the way back.

Naming the options bag "google" makes the two halves meet. OpenCode then keeps the signature in
its own session state and persists it to `opencode.db`, so resume and parallel tool calls work
without the shim caching anything — the alternative, reinjecting signatures in the shim, would put
SSE parsing on the hot path of every model to fix one. Verified live on 2026-09-03: a 7-leg Gemini
build (three reads, a failed tool call, a write) ran clean, and `gpt-5.4`/`sonnet` were unaffected,
which is the point — the namespace is inert for a model that sends no signature.

Re-check this on any OpenCode upgrade: upstream making the read symmetric would make it dead
weight. It is not the provider's display name, and voicing must not treat it as one.
"""
from __future__ import annotations

import json
from pathlib import Path

from sage.orchestrator.brand import apply_agent_voice

CONFIG = json.loads((Path(__file__).resolve().parents[2] / "opencode.json").read_text())


def test_the_provider_options_are_named_for_the_key_the_sdk_reads():
    assert CONFIG["provider"]["sage-gateway"]["options"]["name"] == "google"


def test_the_options_name_is_not_the_providers_display_name():
    """Two different keys called `name` sit three lines apart in a file that cannot hold a comment.
    The display name is the one a person reads in OpenCode; this one is a wire detail."""
    provider = CONFIG["provider"]["sage-gateway"]
    assert provider["name"] != provider["options"]["name"]
    assert "Sage" in provider["name"]


def test_an_oem_pack_cannot_rename_the_options_bag():
    """`apply_agent_voice` rewrites agent prompts for a partner's assistant name. If it ever grew
    to rewrite provider fields as well, it would rename this one too and every Gemini build would
    start failing on its first tool call again — with nothing in the diff to say why."""
    voiced = apply_agent_voice(json.loads(json.dumps(CONFIG)), "Acme")
    assert voiced["provider"]["sage-gateway"]["options"]["name"] == "google"
