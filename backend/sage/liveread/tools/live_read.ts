// Live read, as an OpenCode CUSTOM TOOL rather than an MCP one (ADR-0041).
//
// The MCP transport does not deliver. Measured on the pinned 1.18.4 in a live workspace: OpenCode
// held `sage-live-read` connected for the very instance the turn ran in — confirmed by
// `GET /mcp?directory=` two seconds before the model call — and sent the model ten built-in tools
// and none of ours. Config clean, port right, no project config, no `tools` filter, agent resolved.
// That is opencode #33027, and nothing on our side of the boundary can fix it.
//
// Custom tools go down a different path, and that path works. Verified end to end on the same
// 1.18.4: a tool in `~/.config/opencode/tools/` appears in the model's own tool list AND executes,
// from any session directory, with no `.opencode/` in the workspace. The telling detail is that
// `/experimental/tool` lists built-ins PLUS custom tools and never MCP tools — and a Chat turn gets
// exactly that registry. So this lands in the list that arrives.
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

import { tool } from "@opencode-ai/plugin"

// The orchestrator serves this, and it moves port with the workspace (:8888 on Domino, :8080
// locally). OpenCode inherits SAGE_CONTROL_PORT from the process that spawned it — see
// `driver/server.py`, which hands it the whole environment.
const PORT = process.env.SAGE_CONTROL_PORT || "8080"
const ROUTE = `http://127.0.0.1:${PORT}/mcp/live-read`

// Every failure comes back as TEXT the assistant reads, never as a thrown error. A refusal is not a
// crash, and a turn that cannot make a live read must still be able to answer with Python — which
// is what the prompt tells it to do. Throwing here would end the turn on a stack trace instead.
async function call(name, args) {
  let res
  try {
    res = await fetch(ROUTE, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: { name, arguments: args },
      }),
    })
  } catch (e) {
    return `The live read could not be reached (${e}). Query the data with Python instead, and do not tell the person you cannot see their data.`
  }
  if (!res.ok) {
    return `The live read answered HTTP ${res.status}. Query the data with Python instead, and do not tell the person you cannot see their data.`
  }
  let body
  try {
    body = await res.json()
  } catch (e) {
    return `The live read replied with something unreadable (${e}). Query the data with Python instead.`
  }
  const text = body?.result?.content?.[0]?.text
  if (typeof text === "string") return text
  if (body?.error?.message) return String(body.error.message)
  return "The live read returned nothing readable. Query the data with Python instead."
}

const token = tool.schema
  .string()
  .describe("The read token from this turn's prompt. Pass it back exactly as given.")

export const table = tool({
  description:
    "Read a few real rows out of one bound table and show them to the person as a table card. " +
    "Use this whenever they ask what the data looks like, or to see a sample row. You get back the " +
    "columns, a row count and a path — not the rows themselves, which go straight to the card the " +
    "person sees. Say what the table holds; do not claim to be quoting values you were not given.",
  args: {
    token,
    source: tool.schema.string().describe("The Data Source name."),
    table: tool.schema.string().describe("The table name."),
    database: tool.schema.string().optional(),
    schema: tool.schema.string().optional(),
    limit: tool.schema.number().optional().describe("Rows to read. Default 5, capped."),
    title: tool.schema.string().optional().describe("A short title for the card."),
  },
  async execute(args) {
    return call("live_read_table", args)
  },
})

export const files = tool({
  description:
    "List the files in a bound Dataset, or read the head of one of them. Use this to say what a " +
    "Dataset holds. A listing that stopped short of the end says so — never report a capped " +
    "listing as all of them.",
  args: {
    token,
    dataset: tool.schema.string().describe("The Dataset name."),
    path: tool.schema
      .string()
      .optional()
      .describe("One file below it. Omit to list the Dataset instead."),
  },
  async execute(args) {
    return call("live_read_files", args)
  },
})
