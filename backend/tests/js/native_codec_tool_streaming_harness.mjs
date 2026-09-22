// Record the tool definitions the production codec actually puts on the wire.
//
// Sage asks Anthropic to stream a tool's arguments eagerly, and it does so by accident: nothing in
// Sage sets `eager_input_streaming`, the installed `@ai-sdk/anthropic` defaults it on, and
// `provider.mjs` imports that copy. The harness exists so the accident cannot be undone silently.
import { pathToFileURL } from 'node:url';

const [providerPath] = process.argv.slice(2);
const { createSageGateway } = await import(pathToFileURL(providerPath).href);

let sent = null;
const model = createSageGateway({
  baseURL: 'http://localhost:1234/v1',
  fetch: async (url, init) => {
    if (String(url).endsWith('/resolve')) {
      return Response.json({model: 'Opus-4.8', protocol: 'messages', effort: null});
    }
    sent = JSON.parse(init.body);
    return new Response('data: {"type":"message_stop"}\n\n',
      {headers: {'content-type': 'text/event-stream'}});
  },
}).languageModel('sonnet');

const tools = [
  {type: 'function', name: 'write', description: 'Write a file',
   inputSchema: {type: 'object', required: ['filePath', 'content'],
     properties: {filePath: {type: 'string'}, content: {type: 'string'}}}},
  {type: 'function', name: 'bash', description: 'Run a command',
   inputSchema: {type: 'object', required: ['command'], properties: {command: {type: 'string'}}}},
];

try {
  const result = await model.doStream({
    prompt: [{role: 'user', content: [{type: 'text', text: 'hello'}]}],
    tools, maxOutputTokens: 128,
  });
  for await (const _ of result.stream) { /* drain; the recorded request is the subject */ }
} catch { /* the stub reply is deliberately minimal — `sent` is captured before it is returned */ }

console.log(JSON.stringify({
  stream: sent?.stream ?? null,
  tools: (sent?.tools ?? []).map((t) => ({
    name: t.name,
    eager: Object.hasOwn(t, 'eager_input_streaming') ? t.eager_input_streaming : null,
  })),
}));
