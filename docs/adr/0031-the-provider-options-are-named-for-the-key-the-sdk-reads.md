---
status: accepted
---

# The provider options are named for the key the SDK reads

`opencode.json` sets `provider.sage-gateway.options.name` to the string `"google"`. It is not a
display name, it is not our provider id, and it is not decoration: it is the namespace key that
makes Gemini's tool-call signatures survive a round trip. Left at anything else, **every agentic
Gemini build dies on its first tool result.**

```json
"provider": {
  "sage-gateway": {
    "npm": "@ai-sdk/openai-compatible",
    "name": "Sage Enforcement Shim (-> Domino AI Gateway)",
    "options": { "baseURL": "...", "apiKey": "...", "name": "google" }
  }
}
```

Two keys called `name` sit three lines apart, and they mean unrelated things. The outer one is the
label a person reads in OpenCode. The inner one is a wire detail. JSON takes no comment, so the
account lives here and in
`backend/tests/test_a_gemini_tool_call_keeps_its_thought_signature.py`.

## What breaks without it

Gemini attaches a `thought_signature` to every tool call it makes and rejects the next request that
does not hand it back:

```
400 "Function call is missing a thought_signature in functionCall parts. This is required for
tools to work correctly... function call `default_api:glob`, position 2."
```

The signature is cryptographically validated, so it has to be replayed verbatim — a bogus value
answers `"Invalid thought signature"` and an empty string gives the message above. It arrives on
the wire nested in the response, which is worth seeing, because it means nothing is missing from
what the gateway sends us:

```json
"message": {
  "role": "assistant", "content": "OK",
  "extra_content": { "google": { "thought_signature": "AY89a18fG459WTO/NYdw5..." } }
}
```

Plain chat, streaming, vision and the *first* tool call all worked without this setting. Only the
first tool RESULT failed — which is every build, on its first read, and nothing shorter.

## Why the fix is a namespace and not code

OpenCode 1.18.4 bundles `@ai-sdk/openai-compatible` into its binary. Read out of the compiled
bundle, both halves of the round trip already exist — they just disagree about where the value
lives:

```js
// response side — writes under the provider's configured options name
providerMetadata[providerOptionsName] = { thoughtSignature }

// request side — reads under "google", hardcoded
providerOptions?.google?.thoughtSignature
```

With `options.name` at our provider id it writes to `sage-gateway` and reads from `google`, so the
signature is stored, found by nobody, and silently dropped. Naming the options bag `"google"` makes
the two halves meet. OpenCode then keeps the signature in its own session state and persists it to
`opencode.db`, so session resume and parallel tool calls work with nothing added on our side.

The namespace is inert for a model that sends no signature, which is why one global setting is safe
for the whole catalog rather than something that needs to be per-model.

## Why not the alternatives

**Cache and reinject signatures in the shim** was the obvious fix and is much worse. It would put
SSE parsing on the hot path of `OpenAICompatibleClient.route()` for every model to serve one, and
then owe cache scoping, session resume and compaction their own answers — all of which OpenCode
already solves for free by keeping the value in session state.

**A patched or forked `@ai-sdk/openai-compatible`** cannot reach the copy that runs: it is bundled
into the OpenCode binary, not resolved from `node_modules`.

**A second provider entry just for Gemini**, named `google`, would line the keys up for Gemini and
split the config: two providers pointing at one gateway, two model lists, two places for the
baseURL rewrite in `_install_opencode_config` to miss.

## The cost, stated plainly

The value is load-bearing and reads like a typo. Two things follow from that.

First, it must survive the paths that rewrite this config. `apply_agent_voice` rewrites provider
names for an OEM pack and `_install_opencode_config` rewrites `options.baseURL` to the live shim
port — a change to either that rebuilt `options` from the fields it cared about would drop this key
and take every Gemini build down with it, with nothing in the diff to say why. Both are pinned by
test.

Second, **an OpenCode upgrade must re-verify this.** Upstream making the read symmetric would turn
this into dead weight; upstream changing the key would turn it into the same silent breakage it
fixes. The check is cheap and needs no Domino workspace — point a scratch config's baseURL at the
gateway, then, with `OPENCODE_CONFIG` set so the global `~/.config/opencode` is never written:

```
opencode run --model sage-gateway/gemini-3.7-flash "Read NOTE.txt and tell me the secret word."
```

Verified this way on 2026-09-03 against `cloud-dogfood`: without the setting, the run dies on the
glob result with the 400 above; with it, a four-leg build read a file, survived a read of a file
that did not exist, wrote `out.txt` and read it back. `gpt-5.4` ran the same prompt unchanged.

## The model entry beside it

`provider.sage-gateway.models` keys this alias as the bare `gemini-3.7-flash`, though the gateway
offers it as `domino/gemini-3.7-flash`. `chat_compact.compact_model` hands OpenCode
`bare_model_id(...)` as the modelID when it asks a Chat session to summarize itself, so a
slash-prefixed key would not resolve there. Outbound spelling is not at stake: the shim overwrites
`model` with the router's decision on every request, and the gateway answers 200 to either
spelling.

Its `limit.context` is the conservative 128k default, not a measurement. `/api/aliases` reports
`inference_params: {}` for this alias — the gateway states no window, so there is nothing to read
and we do not invent one. Guessing high is the expensive mistake, since claiming more room than the
model has overflows the prompt mid-turn, while guessing low only compacts a conversation earlier
than it had to. `chat_compact.CONTEXT_LIMITS` mirrors the same number, and a test compares the two
maps as sets so neither can drift.
