// A template's `askModel` / `askJson` against a stubbed gateway, run from the template's own file (#681).
//
// Input on stdin: `{ "template": "fastapi-antd" | "react-vite", "cases": [{ "name", "call":
// "askModel" | "askJson", "args": [...], "reply": { "status": 200, "body": {...} } }] }`.
// Output: `{ <name>: { "result" | "thrown": {...}, "requests": [<JSON body sent>] } }`.
import fs from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import vm from 'node:vm';

const TEMPLATE = new URL('../../../template/', import.meta.url).pathname;
const { template, cases } = JSON.parse(fs.readFileSync(0, 'utf8'));
const config = { alias: 'first', displayName: 'First', base: 'https://gateway.example/v1', project: 'p',
                 models: [{ alias: 'first', displayName: 'First' }, { alias: 'second', displayName: 'Second' }] };

let reply = null;
let requests = [];
const fetch = async (_url, init) => {
  requests.push(JSON.parse(init.body));
  const headers = { 'x-request-id': 'req-1' };
  return { ok: reply.status < 400, status: reply.status,
           headers: { get: (k) => headers[k.toLowerCase()] ?? null }, json: async () => reply.body };
};

let helpers;
if (template === 'fastapi-antd') {
  const sandbox = { fetch, JSON, Array, Object, String, Number, Boolean, Error, SyntaxError, Math, Promise,
                    appLlmConfig: config };
  sandbox.window = sandbox;
  sandbox.sage = { url: (p) => p, preview: false };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(TEMPLATE + 'fastapi-antd/static/sage/appLlm.js', 'utf8'), sandbox);
  helpers = sandbox.sage;
} else {
  globalThis.fetch = fetch;
  const source = fs.readFileSync(TEMPLATE + 'react-vite/src/appLlm.ts', 'utf8')
    .replace('import { appLlmConfig } from "./appLlm.config";', `const appLlmConfig = ${JSON.stringify(config)};`)
    .replaceAll('import.meta.env.DEV', 'false')
    .replaceAll('import.meta.env.BASE_URL', '"/"');
  helpers = await import('data:text/javascript;base64,' +
                        Buffer.from(stripTypeScriptTypes(source)).toString('base64'));
}

const out = {};
for (const test of cases) {
  reply = test.reply || { status: 200, body: { choices: [{ message: { content: '{}' }, finish_reason: 'stop' }] } };
  requests = [];
  const row = {};
  try {
    row.result = JSON.parse(JSON.stringify(await helpers[test.call](...test.args)));
  } catch (error) {
    row.thrown = { message: error.message, kind: error.kind ?? null };
  }
  row.requests = requests;
  out[test.name] = row;
}
process.stdout.write(JSON.stringify(out));
