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
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: { name, arguments: sent },
      }),
    })
  } catch (e) {
    return `The live read could not be reached (${e}).` + FELL_THROUGH
  }
  if (!res.ok) {
    return `The live read answered HTTP ${res.status}.` + FELL_THROUGH
  }
  let body
  try {
    body = await res.json()
  } catch (e) {
    return `The live read replied with something unreadable (${e}).` + FELL_THROUGH
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
    "person sees. Say what the table holds; do not claim to be quoting values you were not given.",
  args: {
    token,
    source: { type: "string", description: "The Data Source name." },
    table: { type: "string", description: "The table name." },
    // Null is the right answer almost always, and now it is also the SAFE one: the read fills
    // both from the position the table was picked at. It did not, once, and an unqualified name
    // reached the warehouse as `..TABLE`.
    database: { type: ["string", "null"], description: "The database. Send null to read it where the table was picked." },
    schema: { type: ["string", "null"], description: "The schema. Send null to read it where the table was picked." },
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
    "listing as all of them.",
  args: {
    token,
    dataset: { type: "string", description: "The Dataset name." },
    path: {
      type: ["string", "null"],
      description: "One file below it." + OPTIONAL + " Then the Dataset is listed instead.",
    },
  },
  async execute(args) {
    return call("live_read_files", args)
  },
}
