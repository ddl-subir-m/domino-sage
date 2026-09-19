// A Delegated model call, as an OpenCode CUSTOM TOOL (ADR-0057).
//
// The same shape as `liveread/tools/live_read.ts`, and for the same measured reasons. Read that
// file's header before changing this one; only what differs is written out again here.
//
// NOTHING IS IMPORTED. `import { tool } from "@opencode-ai/plugin"` makes OpenCode fetch that
// package from npm at runtime, and a workspace has no egress when it runs — the import resolves
// nowhere, the module never loads, and the tool is simply absent. A plain object is what OpenCode
// wanted anyway.
//
// THE FILENAME IS THE TOOL NAME. OpenCode names a default-export tool after its file, so
// `delegated_model_call.ts` gives `delegated_model_call` — the bare name `delegated/mcp.py`
// dispatches on and the name every prompt teaches. Renaming this file renames the tool, and
// `test_a_delegated_model_call_is_named_the_same_either_way` pins the three together.
//
// EVERY ARGUMENT COMES OUT REQUIRED. OpenCode marks every key of `args` required and has no reading
// for a plain schema that says otherwise, so the optional ones are declared nullable and say so,
// and `execute` drops the nulls before posting — Python then sees the key absent.
//
// A shim and nothing more. The grant, the per-turn cap, the sensitivity lock and the cost tags all
// stay in Python, behind the route this posts to. No token and no gateway URL is ever handed to the
// agent: what it relays is this turn's own token, which names a Conversation and authorises nothing
// else (ADR-0052).

const PORT = process.env.SAGE_CONTROL_PORT || "8080"
const ROUTE = `http://127.0.0.1:${PORT}/mcp/delegated-model`

// How long to wait for the model. The ceiling is `_CHAT_TOOL_QUIET_TIMEOUT_S` (240s, see
// `orchestrator/service.py`): a tool in flight sends nothing, so anything past that is unreachable —
// the TURN dies in silence and the assistant is never handed a sentence to act on. 210s keeps the
// failure inside the TOOL and leaves 30s for the sentence below to come back and the next step to
// begin, which refreshes that same clock — so a young turn survives its own bound. Not every turn:
// `_CHAT_TURN_MAX_S` (600s) is measured from the start and is never refreshed, so a turn that has
// already spent 400s gathering rows and then waits 210s here is killed by the hard ceiling anyway,
// in the same silence. The bound buys the sentence when there is still turn left to spend it on.
//
// And it bounds the CLIENT, not the work. Aborting the fetch does not cancel the Python handler,
// which runs to completion with nobody reading it — holding one of the delegated slots for its full
// duration, so a second call in the same turn can queue behind a call this tool already gave up on.
//
// Not lower, and 60s in particular: a 60s bound on a gateway call cut a real answer that was still
// coming, and the person read "TypeError: network error" instead of it. A classification pass over
// gathered rows is legitimately slow, and a bound that fires on work-in-progress is worse than none.
//
// The env var is this file's own idiom, the same shape as PORT above: the production value is the
// default, and a test can reach the bound without waiting out four minutes for it.
//
// The guard is a RANGE, not a truthiness check, and the difference is a capability. Anything
// `AbortSignal.timeout` rejects throws a RangeError, and though that throw lands in the catch below
// — measured, not assumed — the tool then returns that same refusal on EVERY call, having never
// reached the route. It is present, readable, and useless. `Number(...) || default` is not enough:
// it catches NaN, `""` and `0`, and passes `-1`, `0.5` and `5e9` straight through to the throw.
//
// The CEILING is the job's number, not the runtime's (#416). No configured value may reach the
// turn's own quiet window above, because a bound that cannot fire while there is still a turn to
// hand the sentence to is not a bound — it only moves the silence. `AbortSignal.timeout`'s own
// edge was the wrong number to key on: it is a DIFFERENT number on each runtime, and which runtime
// runs these tools is not settled. See the same guard in `liveread/tools/live_read.ts` for the
// measurement. A typo in an env var should cost the bound's precision, not the capability.
//
// `<` is a FLOOR under the real constraint, not the constraint itself. The thing that makes a bound
// useful is headroom to REPORT in: 239_999 passes this guard and leaves one millisecond to get the
// sentence back, which is as useless as 240_000 and this guard admits it. The shipped default
// leaves 30s of that headroom (210_000 against 240_000), and that headroom is the actual design.
// It is not expressed here because "enough room to report" is a number the job would have to
// justify, and reaching for one the mechanism merely suggests is how 2^31-1 got here in the first
// place. Whoever tightens this next: that is the argument, and this is where it was left.
const QUIET_WINDOW_MS = 240_000
const CONFIGURED_MS = Number(process.env.SAGE_DELEGATED_CALL_TIMEOUT_MS)
const TIMEOUT_MS =
  Number.isInteger(CONFIGURED_MS) && CONFIGURED_MS > 0 && CONFIGURED_MS < QUIET_WINDOW_MS
    ? CONFIGURED_MS
    : 210_000

const OPTIONAL = " Send null if you do not need it."

// A failure is not an answer. Each of these says the model was NOT asked before it says why,
// because an assistant handed only the "why" goes off, answers another way, and then describes the
// result as though a model had produced it.
//
// Six returns below carry a sentence, over FIVE conditions — the bound owns two of them, because
// the clock can run out at the headers or inside the body. This one is true of four of those five,
// and the bound has its own. (The other two returns hand back the route's own words and owe
// nothing.) Connection failure never reached the route; HTTP non-2xx is a request this process
// refused to read, not one it ran; an unreadable body that is not a timeout is the bodyless 202 or
// a mangled reply, and this route answers JSON or nothing at all. And
// "returned nothing readable" — the arm that is easiest to mistake for a call that happened — is
// reached only when a 2xx body is valid JSON carrying neither `result.content[0].text` nor
// `error.message`. `delegated/mcp.py:handle` puts EVERY executed call's outcome into the first of
// those: the answer on success, and on failure `_failed_text`, behind an `except Exception` that is
// broad on purpose. So a reply with neither is a reply this call never produced. Nothing was asked.
const NOT_ASKED = " Nothing was asked and no answer came back. Do the work another way, and do not "
  + "report an answer no model gave you."

// The bound is the one arm NOT_ASKED is false of, and it is false in the direction that costs money
// (#413). The route took the request; what ran out was this tool's patience, not the work. Aborting
// the fetch does not cancel the Python handler — see the note above TIMEOUT_MS — so the generation
// is still in flight, still holding one of the four delegated slots, and will finish with nobody
// reading it. Told "nothing was asked", the correct next move for an assistant is to ask again, and
// asking again is the one thing that pays twice for the slowest call in the system while the first
// one is still running. So this says the opposite, and keeps the clause NOT_ASKED exists for:
// whatever the turn does instead, it may not report an answer no model gave it.
//
// It does not say "wait" either. There is nothing here to wait on: the fetch is gone, and the
// answer this call is still generating has no reader left to arrive at.
//
// Where it is WRONG, since the next reader will look for it — and it is wrong at ONE of its two
// sites, not both. In the BODY catch it is not an inference at all: `fetch` already resolved, so
// headers arrived, so the request demonstrably left. That arm is exempt by construction.
//
// The HEADERS catch is the one that reaches. It is keyed on the abort, not on whether a connection
// was ever established, and those are not the same question. A connect that stalls rather than
// refuses — a DROP rule in front of the control app, say, instead of the ECONNREFUSED loopback
// normally gives instantly — reaches 210s and aborts with `TimeoutError` too, and then "the request
// was sent" is false and NOT_ASKED was the true sentence. That case is accepted, not overlooked. It
// costs a turn that could have retried immediately being told not to, on a host where loopback is
// firewalled against itself; the case it replaces costs a second generation on every ordinary slow
// call. And the discriminator that would settle it is the one the body catch already gets for free
// — a flag set when `fetch` RESOLVES — which by definition cannot be read from inside the catch
// that fires because `fetch` never did. A flag set just BEFORE `fetch` is the one that is easy to
// reach for, and it would be worthless: it asserts exactly as much as this sentence already does,
// while reading like evidence.
const NOT_ANSWERED = " The request was sent and may still be running, so asking again would start a "
  + "second call rather than recover this one. Do the work another way, and do not report an answer "
  + "no model gave you."

export default {
  description:
    "Ask a language model the person added to this conversation. Use this when the work needs a " +
    "model to read text — classifying, summarising or extracting over rows you have already " +
    "gathered — rather than doing it by hand or telling the person you cannot reach a model. " +
    "Name the Alias exactly as this conversation names it: a model that is not in this " +
    "conversation is refused, never swapped for another one. You get back the model's answer as " +
    "text.",
  args: {
    token: {
      type: "string",
      description: "The turn token from this turn's prompt. Pass it back exactly as given.",
    },
    alias: {
      type: "string",
      description: "The language model, named as this conversation names it.",
    },
    prompt: { type: "string", description: "What to ask the model." },
    system: {
      type: ["string", "null"],
      description: "An instruction sent ahead of the prompt." + OPTIONAL,
    },
    max_tokens: {
      type: ["integer", "null"],
      description: "Answer budget. Null uses the default, and the server caps it either way.",
    },
  },
  async execute(args) {
    const sent = {}
    for (const key of Object.keys(args || {})) {
      if (args[key] !== null && args[key] !== undefined) sent[key] = args[key]
    }
    let res
    try {
      res = await fetch(ROUTE, {
        method: "POST",
        signal: AbortSignal.timeout(TIMEOUT_MS),
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          jsonrpc: "2.0",
          id: 1,
          method: "tools/call",
          params: { name: "delegated_model_call", arguments: sent },
        }),
      })
    } catch (e) {
      // Two conditions, not one. See the same catch in `liveread/tools/live_read.ts` for why the
      // bound needs its own sentence: "could not be reached" is false of a gateway that took the
      // request and was still working on it, and that is the condition this bound creates.
      //
      // BOTH halves differ here, and that is where this file stops matching live_read.ts (#413).
      // There the trailing sentence is about the SCREEN — nothing was drawn either way — so one
      // constant serves both arms. Here it is about whether the request was SENT, which is the
      // thing the two arms disagree about, so each takes its own.
      return e?.name === "TimeoutError"
        ? `The model did not answer within ${TIMEOUT_MS / 1000}s.` + NOT_ANSWERED
        : `The model could not be reached (${e}).` + NOT_ASKED
    }
    if (!res.ok) {
      return `The model call answered HTTP ${res.status}.` + NOT_ASKED
    }
    let body
    try {
      body = await res.json()
    } catch (e) {
      // The bound covers the BODY too — see the same catch in `liveread/tools/live_read.ts`. An
      // answer that was still streaming when the clock ran out is a timeout, not a malformed reply.
      // And it is the same timeout as the one above, so it owes the same truth: a body that stopped
      // arriving is a request that was sent. Both catches pick between the two constants (#413).
      return e?.name === "TimeoutError"
        ? `The model did not answer within ${TIMEOUT_MS / 1000}s.` + NOT_ANSWERED
        : `The model call replied with something unreadable (${e}).` + NOT_ASKED
    }
    const text = body?.result?.content?.[0]?.text
    if (typeof text === "string") return text
    if (body?.error?.message) return String(body.error.message)
    return "The model call returned nothing readable." + NOT_ASKED
  },
}
