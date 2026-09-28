// Feed an SSE wire through the production codec and report the reasoning and text it surfaces.
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const [providerPath, wirePath, protocol] = process.argv.slice(2);
const { createSageGateway } = await import(pathToFileURL(providerPath).href);
const wire = readFileSync(wirePath, 'utf8');
const alias = protocol === 'chat' ? 'domino/gemini-3.7-flash' : 'mimo-v2.6-pro';

const model = createSageGateway({
  baseURL: 'http://localhost:1234/v1',
  fetch: async (url) => {
    if (String(url).endsWith('/resolve')) {
      return Response.json({model: alias, protocol, effort: null});
    }
    return new Response(wire, {headers: {'content-type': 'text/event-stream'}});
  },
}).languageModel(alias);

const events = [];
const result = await model.doStream({prompt: [{role: 'user', content: [{type: 'text', text: 'hello'}]}],
  maxOutputTokens: 128});
for await (const event of result.stream) {
  if (event.type.startsWith('reasoning') || event.type === 'text-delta' || event.type === 'error') {
    events.push({type: event.type, id: event.id ?? null, delta: event.delta ?? null,
      error: event.type === 'error' ? String(event.error?.message || event.error) : null});
  }
}
console.log(JSON.stringify(events));
