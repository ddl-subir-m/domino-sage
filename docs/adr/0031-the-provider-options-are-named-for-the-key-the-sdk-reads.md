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

One global setting is safe for the whole catalog rather than something that needs to be per-model:
`gpt-5.4` and `sonnet` send no signature, so nothing is stored under either key for them and the
rename changes nothing they do.

It is worth being exact about what the rename touches, though, because it is **not** signature-
specific. `providerOptionsName` is the namespace for this provider's whole `providerOptions` /
`providerMetadata` bag, and the SDK derives it from the same string:

```js
get providerOptionsName(){ return this.config.provider.split(".")[0].trim() }
// config.provider is `${options.name}.chat`, so this is now "google"
```

So the setting does not add a key beside the others — it moves which key the SDK reads for
everything provider-scoped. Nothing else in `opencode.json` uses a `providerOptions` bag today
(`provider.sage-gateway.options` is the only `options` block in the file, and the shim sets
`reasoning_effort` on the request itself rather than through OpenCode), so there is no live effect
beyond the fix. But a per-model or per-agent `options` block added later is where this would bite,
and it would bite silently. Verify such a block reaches the wire before trusting it.

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

An unlisted alias is worse than nameless: OpenCode resolves only what this map lists, and fails the
whole request with `UnknownError: Unexpected server error` for anything else — it does not fall back
to the id as given. Verified by changing one thing: `opus` unlisted failed that way instantly, and
`opus` listed read a file and answered.

That is survivable only because of how Sage routes. No turn hands OpenCode an alias at all —
`_ensure_session` deliberately creates sessions with no session-level model, OpenCode stays on its
configured default, and the shim overwrites `model` per request. Summarize is the single call that
names an alias, which is why an unlisted model shows up as a session that never compacts rather
than as a turn that fails. The gateway offers aliases this file does not list, and the picker offers
every accessible one, so `chat_compact.summarize_model_id` falls back to a listed id for exactly
that case; `should_compact` keeps weighing the real alias, since substituting a 200k default there
would let a 32k session sail past its own window.

Its `limit.context` is 1048576 — measured, not advertised. `/api/aliases` reports
`inference_params: {}` for every alias on this gateway, so there is no window to read anywhere in
the control plane. The number comes from the gateway's own refusal: send a prompt over the limit and
the error names it ("The input token count exceeds the maximum number of tokens allowed 1048576",
and for the Claude aliases "prompt is too long: 1050024 tokens > 1000000 maximum"). An over-limit
request is rejected before it bills, so the measurement is free to repeat — which matters, because
these are the gateway's configured ceilings and can be changed under us.

The same pass found every pre-existing entry under-claiming by 2–8x, so they were corrected too:
`sonnet` and `gpt-5.4` were both recorded as 200000 against measured 1000000 and 922000, and
`bedrock-qwen3-coder` as 128000 against 262144. Under-claiming is not the safe direction it appears
to be. This map decides when a Chat conversation is summarised, so a window smaller than the truth
discards context the model could still hold, and pays for a summarize call to do it. Compacting
earlier than the window is a cost policy; it belongs in `TOKEN_RATIO`, not in a field that reads as
the model's limit. `chat_compact.CONTEXT_LIMITS` mirrors these values and a test compares the two
maps as sets, so neither can drift.

Three aliases the gateway offers are deliberately absent. `opus` and `etan-opus-4.6` were added once
measured (both back onto `claude-opus-4-6`, both 1000000). `local-domino-llm` and `haiku` keep their
documented windows because neither could be asked — the sovereign endpoint answers 502 to
everything, `haiku` is not in this gateway's `/v1/models`. And `domino-gcp/claude-sonnet-5` stays
out entirely: it 404s upstream from GCP, so any number would be invention.
`chat_compact.summarize_model_id` is what keeps that safe.
