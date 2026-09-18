// Live read, as an OpenCode CUSTOM TOOL rather than an MCP one (ADR-0041).
//
// This is the ONE copy of Live read the model is offered, and it is a custom tool rather than an
// MCP one. Not because MCP is broken — it is not. Turns used to run on OpenCode's v2 API, whose
// prompt path sends the model no custom tools AND no MCP tools; moving them to v1 (`7b9209e`, see
// `driver/opencode.py`) delivered both at once. Measured in production: `all 13:` carrying
// `live_read_table` AND `sage-live-read_live_read_table`, and the model called one.
//
// Two tools with the same description and the same Python route behind them only give the model
// something to guess about, so Sage declares no MCP server and keeps this. A custom tool needs no
// server, no handshake and no port: the fetch below goes to a route this process already serves.
//
// NOTHING IS IMPORTED HERE, AND THAT IS THE POINT. This file used to open with
// `import { tool } from "@opencode-ai/plugin"`, and that one line cost a day. OpenCode installs
// that package from the npm registry AT RUNTIME, into `~/.config/opencode`, the first time it
// loads this directory. A workspace fetches at build time and has no such egress when it runs, so
// the install failed, the import resolved nowhere, the module never loaded, and the tools were
// simply absent — the same silence as the MCP drop they were meant to escape. Reproduced on the
// bench 2026-09-09 by pointing npm at a dead registry: with the import the whole tool list errors,
// without it the tools register. The bench had passed until then only because OpenCode had left a
// warm `node_modules` in that directory back in July.
//
// The import bought nothing. `tool()` is the identity function and `tool.schema` is a re-export of
// zod, so a plain object is the same object OpenCode would have received anyway. What it wants is
// `args` as a record of per-argument JSON Schema, which is exactly `TOOLS[n].inputSchema.properties`
// in `liveread/mcp.py` — measured: OpenCode wraps that record into the right `parameters`.
//
// EVERY ARGUMENT COMES OUT REQUIRED. OpenCode marks every key of `args` required and has no reading
// for a plain schema that says otherwise (zod's `.optional()` was the only way, and zod is the
// dependency we just removed). So the optional ones are declared nullable and say so, and `call`
// drops the nulls before posting — Python then sees the key absent, exactly as it did over MCP.
//
// THE FILENAME IS PART OF THE CONTRACT. OpenCode names a multi-export tool `<file>_<export>`, so
// `live_read.ts` exporting `table` and `files` gives `live_read_table` and `live_read_files` —
// the same bare names `liveread/mcp.py` already dispatches on, and the names the prompts teach.
// Renaming this file renames the tools. `test_the_live_read_tools_are_named_the_same_either_way`
// pins them together.
//
// A shim and nothing more: every decision — the token, the row cap, what reaches the card and what
// reaches the model — stays in Python, behind the route this posts to. Rows never pass through
// here; the reply is the same summary text the MCP path returned.

// The orchestrator serves this, and it moves port with the workspace (:8888 on Domino, :8080
// locally). OpenCode inherits SAGE_CONTROL_PORT from the process that spawned it — see
// `driver/server.py`, which hands it the whole environment.
const PORT = process.env.SAGE_CONTROL_PORT || "8080"
const ROUTE = `http://127.0.0.1:${PORT}/mcp/live-read`

// How long to wait for the read. The ceiling is `_CHAT_TOOL_QUIET_TIMEOUT_S` (240s, see
// `orchestrator/service.py`): a tool in flight sends nothing, so anything past that is unreachable —
// the TURN dies in silence and the assistant is never handed a sentence to act on. 210s keeps the
// failure inside the TOOL and leaves 30s for the sentence below to come back and the next step to
// begin, which refreshes that same clock — so a young turn survives its own bound. Not every turn:
// `_CHAT_TURN_MAX_S` (600s) is measured from the start and is never refreshed, so a turn that has
// already spent 400s gathering rows and then waits 210s here is killed by the hard ceiling anyway,
// in the same silence. The bound buys the sentence when there is still turn left to spend it on.
//
// And it bounds the CLIENT, not the work. Aborting the fetch does not cancel the Python handler:
// `/mcp/live-read` runs `live_read_call` directly on the control app's event loop (unlike
// `/mcp/delegated-model`, which hands off to a threadpool), so an abandoned read goes on holding
// that loop after this tool has already reported failure, and a second read queues behind it.
//
// The same number as `delegated/tools/delegated_model_call.ts`, and NOT because one number was
// easier. These two look like different shapes — a model call and a data read — and the read looks
// like the one that could afford to be strict. It is the opposite. `live_read_files` with
// `operation: "analyze_text"` is not a read: behind this one fetch `liveread/text_analysis.py` runs
// the rows through the LLM Gateway in batches (50 records each by default, up to two attempts
// apiece, at most four in flight), so it is the LONGEST call of the two, not the shortest. A strict
// bound here would cut a working analysis pass — the same mistake as the 60s gateway bound that
// showed the person "TypeError: network error" while their answer was still coming.
//
// The env var is this file's own idiom, the same shape as PORT above: the production value is the
// default, and a test can reach the bound without waiting out four minutes for it.
//
// The guard is a RANGE, not a truthiness check, and the difference is a capability. Anything
// `AbortSignal.timeout` rejects throws a RangeError, and though that throw lands in the catch below
// — measured, not assumed — the tools then return that same refusal on EVERY call, having never
// reached the route. They are present, readable, and useless. `Number(...) || default` is not
// enough: it catches NaN, `""` and `0`, and passes `-1`, `0.5` and `5e9` straight through to the
// throw. The upper bound is 2^31-1 rather than the 2^32-1 node accepts, because above 2^31-1 node
// does not refuse — it warns and silently sets the duration to 1ms, which is a bound that fires
// instantly on every call. A typo in an env var should cost the bound's precision, not the
// capability.
const CONFIGURED_MS = Number(process.env.SAGE_LIVE_READ_TIMEOUT_MS)
const TIMEOUT_MS =
  Number.isInteger(CONFIGURED_MS) && CONFIGURED_MS > 0 && CONFIGURED_MS <= 2_147_483_647
    ? CONFIGURED_MS
    : 210_000

const OPTIONAL = " Send null if you do not need it."

// A failure is not an answer, and a fallback the person cannot see is not one either. Every one of
// these sends the model to Python, which is right — but once it has the rows in hand it describes
// them as though a card were on screen, and nothing wrote one. Live, a turn whose read failed
// replied "here are the first 5 rows" over an empty Thread. So each failure says the screen is
// still empty as well as what to do about it.
const FELL_THROUGH =
  " Nothing was put on the person's screen. Query the data with Python and write the table file " +
  "yourself, and do not tell the person you cannot see their data."


// Every failure comes back as TEXT the assistant reads, never as a thrown error. A refusal is not a
// crash, and a turn that cannot make a live read must still be able to answer with Python — which
// is what the prompt tells it to do. Throwing here would end the turn on a stack trace instead.
async function call(name, args) {
  // The schema cannot say "optional", so the model is told to send null. Python is not asked to
  // learn a second spelling of absent.
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
        params: { name, arguments: sent },
      }),
    })
  } catch (e) {
    // Two conditions, not one, and the difference is the whole point of the bound. "Could not be
    // reached" was true while the only way out of this fetch was a connection failure; a bound adds
    // a second way, and in THAT one the route was reached — it accepted the connection and did not
    // answer in time. Telling the model it could not be reached would be telling it the opposite of
    // what happened, and the sensible reply to unreachable is retry-or-abandon when the truth is a
    // route that is alive and slow. Keyed on `name`, not the message: measured 2026-09-18, node and
    // bun both set `TimeoutError` here, and they word the message differently ("The operation was
    // aborted due to timeout" against "The operation timed out."), so the message is not a gate.
    return (e?.name === "TimeoutError"
      ? `The live read did not answer within ${TIMEOUT_MS / 1000}s.`
      : `The live read could not be reached (${e}).`) + FELL_THROUGH
  }
  if (!res.ok) {
    return `The live read answered HTTP ${res.status}.` + FELL_THROUGH
  }
  let body
  try {
    body = await res.json()
  } catch (e) {
    // The bound covers the BODY too, not just the headers, so an abort can land here instead: the
    // route answered, the rows were still arriving, and the clock ran out mid-stream. Same sentence
    // as the other catch, because from the person's side it is the same event — the read did not
    // come back in time. Calling it "unreadable" would blame the payload for the clock.
    return (e?.name === "TimeoutError"
      ? `The live read did not answer within ${TIMEOUT_MS / 1000}s.`
      : `The live read replied with something unreadable (${e}).`) + FELL_THROUGH
  }
  const text = body?.result?.content?.[0]?.text
  if (typeof text === "string") return text
  if (body?.error?.message) return String(body.error.message)
  return "The live read returned nothing readable." + FELL_THROUGH
}

const token = {
  type: "string",
  description: "The read token from this turn's prompt. Pass it back exactly as given.",
}

export const table = {
  description:
    "Read a few real rows out of one bound table and show them to the person as a table card. " +
    "Use this whenever they ask what the data looks like, or to see a sample row. You get back the " +
    "columns, a row count and a path — not the rows themselves, which go straight to the card the " +
    "person sees. Say what the table holds; do not claim to be quoting values you were not given. " +
    "In fresh projects, operation sum calculates a bound table locally and returns selected totals " +
    "in the same call without needing a Sample rows approval step.",
  args: {
    token,
    source: { type: "string", description: "The Data Source name." },
    // Dotted or bare: the context line above names every table `DWH.MARTS.SALES` and hands the
    // model a SELECT over that same string, so a bare name is the form it has to be reminded of,
    // not the form it reaches for. Python takes the dots apart.
    table: { type: "string", description: "The table. A dotted database.schema.table is fine." },
    // Null is the right answer almost always, and now it is also the SAFE one: the read fills
    // both from the position the table was picked at. It did not, once, and an unqualified name
    // reached the warehouse as `..TABLE`.
    database: { type: ["string", "null"], description: "The database. Send null to read it where the table was picked." },
    schema: { type: ["string", "null"], description: "The schema. Send null to read it where the table was picked." },
    operation: { type: ["string", "null"], enum: ["sum", null],
      description: "Fresh projects: calculate a table locally and return selected totals." + OPTIONAL },
    group_by: { type: ["string", "null"], description: "The group column for sum." + OPTIONAL },
    sum_column: { type: ["string", "null"], description: "The numeric column for sum." + OPTIONAL },
    selected_fields: { type: ["array", "null"], items: { type: "string" },
      description: "Result columns and/or total. Null returns structure only." },
    row_limit: { type: ["integer", "null"], description: "Explicit user row limit; null for all rows." },
    result_name: { type: ["string", "null"], description: "One filename without a directory." + OPTIONAL },
    purpose: { type: ["string", "null"], description: "Purpose of this calculation." + OPTIONAL },
    limit: { type: ["integer", "null"], description: "Rows to read. Default 5, capped." + OPTIONAL },
    title: { type: ["string", "null"], description: "A short title for the card." + OPTIONAL },
  },
  async execute(args) {
    return call("live_read_table", args)
  },
}

export const files = {
  description:
    "List the files in a bound Dataset, or read the head of one of them. Use this to say what a " +
    "Dataset holds. A listing that stopped short of the end says so — never report a capped " +
    "listing as all of them. In fresh projects, operation sum calculates a CSV from its authorized path, " +
    "and operation analyze_text sends only the selected text column with stable record ids through the LLM Gateway, " +
    "validates exact id coverage, writes a result table and returns coverage. Use dataset=upload for uploads. " +
    "Use this for CSV totals and complaint analysis; do not read unrelated raw rows into model context. " +
    "Respect explicit user limits.",
  args: {
    token,
    dataset: { type: "string", description: "The Dataset name." },
    operation: { type: ["string", "null"], enum: ["sum", "analyze_text", null],
      description: "Fresh projects: calculate CSV totals or analyze CSV text." + OPTIONAL },
    group_by: { type: ["string", "null"], description: "The group column for sum." + OPTIONAL },
    sum_column: { type: ["string", "null"], description: "The numeric column for sum." + OPTIONAL },
    text_column: { type: ["string", "null"], description: "The CSV column containing text for analyze_text." + OPTIONAL },
    id_column: { type: ["string", "null"], description: "Optional source id column for analyze_text." + OPTIONAL },
    labels: { type: ["array", "null"], items: { type: "string" },
      description: "Allowed labels for analyze_text classification, or null for summaries." },
    output_field: { type: ["string", "null"], description: "The result field name, such as label or summary." + OPTIONAL },
    batch_size: { type: ["integer", "null"], description: "Records per gateway batch. Null uses the default." },
    max_concurrency: { type: ["integer", "null"], description: "Parallel gateway batches. Null uses one at a time." },
    selected_fields: { type: ["array", "null"], items: { type: "string" },
      description: "Result columns and/or total. Null returns structure only." },
    row_limit: { type: ["integer", "null"], description: "Explicit user row limit; null for all rows." },
    result_name: { type: ["string", "null"], description: "One filename without a directory." + OPTIONAL },
    purpose: { type: ["string", "null"], description: "Purpose of this calculation." + OPTIONAL },
    path: {
      type: ["string", "null"],
      description: "One file below it." + OPTIONAL + " Then the Dataset is listed instead.",
    },
  },
  async execute(args) {
    return call("live_read_files", args)
  },
}
