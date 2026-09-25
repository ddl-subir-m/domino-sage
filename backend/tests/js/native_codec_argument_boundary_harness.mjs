// Drive the installed provider codec with one synthetic Responses stream and record the tool
// events it emits (#560). The stream comes in on stdin as JSON: {"frames": [...], "chunk": N}.
// Each frame is one SSE event object; the wire is built here and cut into N-byte chunks, so a
// fragment can end inside an SSE frame or inside a UTF-8 sequence. Output: the codec's
// tool-input-start/delta/end and tool-call events with their ids and text, plus any error.
import { pathToFileURL } from 'node:url';

const [providerPath] = process.argv.slice(2);
const { createSageGateway } = await import(pathToFileURL(providerPath).href);
const input = JSON.parse(await new Promise((resolve) => {
  let data = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (piece) => { data += piece; });
  process.stdin.on('end', () => resolve(data));
}));

const wire = Buffer.from(input.frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join(''));
const size = input.chunk || wire.length;
const body = new ReadableStream({
  start(controller) {
    for (let at = 0; at < wire.length; at += size) controller.enqueue(wire.subarray(at, at + size));
    controller.close();
  },
});

const model = createSageGateway({
  baseURL: 'http://localhost:1234/v1',
  fetch: async (url) => {
    if (String(url).endsWith('/resolve')) {
      return Response.json({model: 'gpt-5.4', protocol: 'responses', effort: null});
    }
    return new Response(body, {headers: {'content-type': 'text/event-stream'}});
  },
}).languageModel('sage-model');

const events = [];
let error = null;
try {
  const result = await model.doStream({
    prompt: [{role: 'user', content: [{type: 'text', text: 'hello'}]}],
    tools: [{type: 'function', name: 'probe', description: 'Record',
      inputSchema: {type: 'object', properties: {label: {type: 'string'}, text: {type: 'string'}}}}],
  });
  for await (const event of result.stream) {
    if (event.type === 'tool-input-start') events.push({type: event.type, id: event.id, tool: event.toolName});
    else if (event.type === 'tool-input-delta') events.push({type: event.type, id: event.id, delta: event.delta});
    else if (event.type === 'tool-input-end') events.push({type: event.type, id: event.id});
    else if (event.type === 'tool-call') events.push({type: event.type, id: event.toolCallId, tool: event.toolName,
      input: event.input, itemId: event.providerMetadata?.openai?.itemId ?? null});
    else if (event.type === 'finish') events.push({type: event.type, reason: event.finishReason});
    else if (event.type === 'error') events.push({type: event.type, message: String(event.error?.message ?? event.error)});
  }
} catch (e) { error = String(e?.message ?? e); }
console.log(JSON.stringify({events, error}));
