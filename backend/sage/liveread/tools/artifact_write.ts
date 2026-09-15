// Installed with the Live read custom tools. Python checks the active turn and path before I/O.
const PORT = process.env.SAGE_CONTROL_PORT || "8080"

export default {
  description: "Write one requested Chat table or PNG chart under examples/<thread_id>/. Render standard SVG to PNG without shell tools. Cannot edit apps or other threads.",
  args: {
    thread_id: { type: "string", description: "The current thread_id from this turn's prompt." },
    path: { type: "string", description: "Artifact path under examples/<thread_id>/." },
    content: { type: "string", description: "Table JSON, standard SVG chart markup (inline shapes/text only), or base64 PNG bytes." },
    encoding: { type: "string", enum: ["utf8", "svg", "base64"], description: "utf8 for .table.json; svg renders a .png chart; base64 saves existing PNG bytes." },
  },
  async execute(args) {
    let response
    try {
      response = await fetch(`http://127.0.0.1:${PORT}/api/chat/artifact`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(args),
      })
    } catch (error) {
      return `Artifact write failed: ${error}. No artifact was confirmed.`
    }
    let body
    try {
      body = await response.json()
    } catch (error) {
      return `Artifact write failed: unreadable HTTP ${response.status} response. No artifact was confirmed.`
    }
    if (!response.ok && !body.error) return `Artifact write failed: HTTP ${response.status}. No artifact was confirmed.`
    return body.error ? `Write rejected: ${body.error}` : `Artifact written: ${body.path}`
  },
}
