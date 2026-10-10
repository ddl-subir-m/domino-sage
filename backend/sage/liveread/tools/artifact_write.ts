// Installed with the Live read custom tools. Python checks the active turn and path before I/O.
//
// The same shape as `live_read.ts`, and bounded for the same measured reasons. Read that file's
// header before changing this one; only what differs is written out again here.
const PORT = process.env.SAGE_CONTROL_PORT || "8080"

// How long to wait for the write. The ceiling is the JOB's number, not the runtime's.
// `_CHAT_TOOL_QUIET_TIMEOUT_S` (240s, `orchestrator/service.py`) ends the TURN when a tool goes
// quiet, and a tool in flight sends nothing, so a bound at or past that window cannot fire while
// there is still a turn to hand the sentence to. That is not a bound. 210s keeps the failure inside
// the TOOL and leaves 30s for the sentence below to come back.
//
// This tool was the one with no bound at all, and the reason it mattered is a composition rather
// than either half alone: `/mcp/live-read` then ran ON the control app's event loop, so a read
// this tool's neighbour already gave up on went on holding that loop, and the write posted here
// could not even be dispatched to its threadpool until the loop came back. With no ceiling it
// simply waited, and at 240s the turn died with nothing said. The read runs off the loop now
// (#604); the ceiling stays, because a write still must not outlast the turn's quiet window.
//
// The ceiling does not come from `AbortSignal.timeout`'s own range because that range is a
// different number on each runtime, and which runtime runs these tools is not settled here:
// `driver/server.py` launches OpenCode through `npx`, and OpenCode ships bun-compiled binaries.
// Measured 2026-09-18 on node v22.22.3 and bun 1.3.11 — above 2^31-1 node warns and silently sets
// the duration to 1ms (a bound that fires on every call before the route can answer) and throws
// only above 2^32-1, while bun arms every one of those values including the one node refuses.
// 240_000 is the same number on both, and it sits far below either edge, so keying on the runtime
// would buy nothing this does not already cover.
//
// The guard is a RANGE, not a truthiness check, and the difference is a capability: anything
// `AbortSignal.timeout` rejects throws a RangeError, and the tool would then return that same
// refusal on EVERY call, having never reached the route — present, readable and useless.
// `Number(...) || default` catches NaN, `""` and `0`, and passes `-1`, `0.5` and `5e9` straight
// through to the throw. A typo in an env var should cost the bound's precision, not the capability.
//
// `<` is a FLOOR under the real constraint, not the constraint itself. The thing that makes a bound
// useful is headroom to REPORT in: 239_999 passes this guard and leaves one millisecond to get the
// sentence back, which is as useless as 240_000 and this guard admits it. The shipped default
// leaves 30s of that headroom (210_000 against 240_000), and that headroom is the actual design.
// It is not expressed here because "enough room to report" is a number the job would have to
// justify, and reaching for one the mechanism merely suggests is how 2^31-1 got here in the first
// place. Whoever tightens this next: that is the argument, and this is where it was left.
const QUIET_WINDOW_MS = 240_000
const CONFIGURED_MS = Number(process.env.SAGE_ARTIFACT_WRITE_TIMEOUT_MS)
const TIMEOUT_MS =
  Number.isInteger(CONFIGURED_MS) && CONFIGURED_MS > 0 && CONFIGURED_MS < QUIET_WINDOW_MS
    ? CONFIGURED_MS
    : 210_000

// A failure is not an answer. The model has to be told the file is NOT there before it is told why,
// because an assistant handed only the "why" goes on to describe an artifact nobody wrote.
const NOT_CONFIRMED = " No artifact was confirmed."

export default {
  description: "Write one requested Chat table, or a PNG chart drawn from the rows of a result read this turn, under examples/<thread_id>/. Cannot edit apps or other threads.",
  args: {
    thread_id: { type: "string", description: "The current thread_id from this turn's prompt." },
    path: { type: "string", description: "Artifact path under examples/<thread_id>/: <name>.table.json or <name>.png." },
    // Nullable rather than optional, for the reason `live_read.ts` gives: OpenCode marks every key
    // of `args` required. The nulls are dropped before posting, so Python sees the key absent.
    content: { anyOf: [{ type: "string" }, { type: "null" }], description: "Table JSON, for a .table.json path. Send null for a chart." },
    encoding: { anyOf: [{ type: "string", enum: ["utf8"] }, { type: "null" }], description: "utf8 for .table.json. Send null for a chart." },
    table: { anyOf: [{ type: "string" }, { type: "null" }], description: "For a .png chart: the title of the result read this turn to plot. Send null for a table." },
    x: { anyOf: [{ type: "string" }, { type: "null" }], description: "For a .png chart: the column holding the labels. Send null for a table." },
    y: { anyOf: [{ type: "array", items: { type: "string" } }, { type: "null" }], description: "For a .png chart: the column or columns holding the numbers. Send null for a table." },
    by: { anyOf: [{ type: "string" }, { type: "null" }], description: "For a .png chart of one y by two categories: the second category's column. Draws one bar per x, stacked by it. Send null otherwise." },
    money: { anyOf: [{ type: "array", items: { type: "string" } }, { type: "null" }], description: "For a .png chart: the y columns that are amounts of money. Send null if none." },
    percent: { anyOf: [{ type: "array", items: { type: "string" } }, { type: "null" }], description: "For a .png chart: the y columns that are percentages (28.7 means 28.7%). Send null if none." },
    bar_label: { anyOf: [{ type: "string" }, { type: "null" }], description: "For a .png bar chart of one y and no by: the column whose text labels each bar in place of its value. Build that text in the statement, e.g. a column of '81 calls · 28.7% win rate'. Send null to label each bar with its value." },
  },
  async execute(args) {
    const sent = {}
    for (const key of Object.keys(args)) {
      if (args[key] !== null && args[key] !== undefined) sent[key] = args[key]
    }
    let response
    try {
      response = await fetch(`http://127.0.0.1:${PORT}/api/chat/artifact`, {
        method: "POST",
        signal: AbortSignal.timeout(TIMEOUT_MS),
        headers: { "content-type": "application/json" },
        body: JSON.stringify(sent),
      })
    } catch (error) {
      // Two conditions, not one, and the difference is the whole point of the bound. "Could not be
      // reached" was true while the only way out of this fetch was a connection failure; the bound
      // adds a second way, and in THAT one the route WAS reached — it accepted the connection and
      // did not answer in time. Keyed on `name`, not on the message: node and bun both set
      // `TimeoutError` here and word the message differently (see `live_read.ts`), so the message
      // cannot be the gate.
      return (error?.name === "TimeoutError"
        ? `Artifact write failed: the write did not answer within ${TIMEOUT_MS / 1000}s.`
        : `Artifact write failed: the route could not be reached (${error}).`) + NOT_CONFIRMED
    }
    let body
    try {
      body = await response.json()
    } catch (error) {
      // The bound covers the BODY too, so an abort can land here instead: the route answered and
      // the reply was still arriving when the clock ran out. Calling that "unreadable" would blame
      // the payload for the clock and point the model at a route that was answering perfectly well.
      return error?.name === "TimeoutError"
        ? `Artifact write failed: the write did not answer within ${TIMEOUT_MS / 1000}s.` + NOT_CONFIRMED
        : `Artifact write failed: unreadable HTTP ${response.status} response.` + NOT_CONFIRMED
    }
    if (!response.ok && !body.error) return `Artifact write failed: HTTP ${response.status}.` + NOT_CONFIRMED
    return body.error ? `Write rejected: ${body.error}` : `Artifact written: ${body.path}`
  },
}
