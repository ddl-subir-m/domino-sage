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

const OPTIONAL = " Send null if you do not need it."

// A failure is not an answer. Each of these says the model was NOT asked before it says why,
// because an assistant handed only the "why" goes off, answers another way, and then describes the
// result as though a model had produced it.
const NOT_ASKED = " Nothing was asked and no answer came back. Do the work another way, and do not "
  + "report an answer no model gave you."

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
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          jsonrpc: "2.0",
          id: 1,
          method: "tools/call",
          params: { name: "delegated_model_call", arguments: sent },
        }),
      })
    } catch (e) {
      return `The model could not be reached (${e}).` + NOT_ASKED
    }
    if (!res.ok) {
      return `The model call answered HTTP ${res.status}.` + NOT_ASKED
    }
    let body
    try {
      body = await res.json()
    } catch (e) {
      return `The model call replied with something unreadable (${e}).` + NOT_ASKED
    }
    const text = body?.result?.content?.[0]?.text
    if (typeof text === "string") return text
    if (body?.error?.message) return String(body.error.message)
    return "The model call returned nothing readable." + NOT_ASKED
  },
}
