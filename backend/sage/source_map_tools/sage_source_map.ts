// The code map, as an OpenCode CUSTOM TOOL (#700).
//
// The same shape as `delegated/tools/delegated_model_call.ts`, and for the same measured reasons.
// Read that file's header before changing this one; only what differs is written out here.
//
// THE FILENAME IS THE TOOL NAME: `sage_source_map.ts` gives `sage_source_map`, the name
// `source_map.TOOL_NAME` dispatches on and `shim/enforcement.py` strips from a turn that did not
// switch it on.
//
// A lookup is a read of the app's own files, so every failure says the same thing: read and search
// them instead. Nothing a failed lookup leaves running costs anything, so the timeout needs no
// sentence of its own about whether to ask again.

const PORT = process.env.SAGE_CONTROL_PORT || "8080"
const ROUTE = `http://127.0.0.1:${PORT}/mcp/source-map`

// The bound and its range guard are `delegated_model_call.ts`'s, for the reasons written there.
const QUIET_WINDOW_MS = 240_000
const CONFIGURED_MS = Number(process.env.SAGE_SOURCE_MAP_TIMEOUT_MS)
const TIMEOUT_MS =
  Number.isInteger(CONFIGURED_MS) && CONFIGURED_MS > 0 && CONFIGURED_MS < QUIET_WINDOW_MS
    ? CONFIGURED_MS
    : 210_000

const INSTEAD = " Read and search the app's files instead."

export default {
  description:
    "Find which of this app's files defines a screen, component, route or named query, and what " +
    "links to it. Cheaper than searching. Pass a symbol, paths, or neither for an overview.",
  args: {
    token: {
      type: "string",
      description: "The read token from this turn's prompt. Pass it back exactly as given.",
    },
    symbol: {
      anyOf: [{ type: "string" }, { type: "null" }],
      description: "A name to find, such as MainScreen. Send null if you do not need it.",
    },
    paths: {
      anyOf: [{ type: "array", items: { type: "string" } }, { type: "null" }],
      description: "App-relative source paths to map. Send null if you do not need it.",
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
          params: { name: "sage_source_map", arguments: sent },
        }),
      })
    } catch (e) {
      return e?.name === "TimeoutError"
        ? `The source map did not answer within ${TIMEOUT_MS / 1000}s.` + INSTEAD
        : `The source map could not be reached (${e}).` + INSTEAD
    }
    if (!res.ok) {
      return `The source map answered HTTP ${res.status}.` + INSTEAD
    }
    let body
    try {
      body = await res.json()
    } catch (e) {
      return e?.name === "TimeoutError"
        ? `The source map did not answer within ${TIMEOUT_MS / 1000}s.` + INSTEAD
        : `The source map replied with something unreadable.` + INSTEAD
    }
    const text = body?.result?.content?.[0]?.text
    if (typeof text === "string") return text
    if (body?.error?.message) return String(body.error.message) + INSTEAD
    return "The source map returned nothing readable." + INSTEAD
  },
}
