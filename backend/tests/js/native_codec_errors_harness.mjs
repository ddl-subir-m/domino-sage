// Exercise the installed native SDK codecs through the production provider boundary.
import { inspect } from 'node:util';
import { pathToFileURL } from 'node:url';
import { APICallError } from '@ai-sdk/provider';
const [providerPath, protocol, kind, safeWire] = process.argv.slice(2);
const { createSageGateway } = await import(pathToFileURL(providerPath).href);
const marker = 'SYNTHETIC-OPAQUE-479';
const safeMessage = 'The model gateway refused content under its guardrail policy (guardrail_blocked).';
const malformed = {
  messages: {type: 'content_block_delta', index: 'invalid', delta: {type: 'thinking_delta', thinking: marker}},
  responses: {type: 17, output_index: 0,
    item: {id: 'rs_test', type: 'reasoning', encrypted_content: marker, summary: 'invalid'}},
  chat: {choices: [{index: 0, delta: {tool_calls: marker}, finish_reason: null}]},
};
const requests = [];
const checkpointMessage = 'Session policy changed. Use Clear recall before continuing.';
const model = createSageGateway({baseURL: 'http://localhost:1234/v1', fetch: async (url) => {
  requests.push(new URL(url).pathname);
  if (String(url).endsWith('/resolve')) {
    return Response.json({model: protocol === 'messages' ? 'Opus-4.8' : 'gpt-5.4', protocol, effort: null});
  }
  if (kind.startsWith('http')) {
    return Response.json({error: {message: kind === 'http' ? checkpointMessage : safeMessage}},
      {status: kind === 'http' ? 400 : 502});
  }
  if (kind === 'stream-failure' || kind === 'abort') {
    const error = kind === 'abort' ? new DOMException(marker, 'AbortError') : new Error(marker);
    return new Response(new ReadableStream({start(controller) {controller.error(error);}}),
      {headers: {'content-type': 'text/event-stream'}});
  }
  const wire = kind === 'safe' ? safeWire : `data: ${JSON.stringify(malformed[protocol])}\n\n`;
  return new Response(wire, {headers: {'content-type': 'text/event-stream'}});
}}).languageModel('gpt-5.4');
let errorLeaked = false;
let raw = false;
const errors = [];
function record(error) {
  errorLeaked ||= inspect(error, {depth: 10}).includes(marker);
  errors.push({message: String(error?.message || error), apiClass: APICallError.isInstance(error),
    status: error?.statusCode, retryable: error?.isRetryable, name: error?.name});
}
try {
  const result = await model.doStream({prompt: [{role: 'user', content: [{type: 'text', text: marker}]}],
    maxOutputTokens: 128, includeRawChunks: true});
  for await (const event of result.stream) {
    raw ||= event.type === 'raw';
    if (event.type === 'error') record(event.error);
  }
} catch (error) { record(error); }
console.log(JSON.stringify({errorLeaked, raw, errors, requests}));
