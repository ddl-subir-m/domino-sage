// What a template's `runQuery` hands an app for one query answer, run from the template's own file.
//
// Input on stdin: `{ "template": "fastapi-antd" | "react-vite", "body": <the server's JSON answer> }`.
// Output: the value `runQuery` resolved to.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';

const TEMPLATE = new URL('../../../template/', import.meta.url).pathname;
const { template, body } = JSON.parse(fs.readFileSync(0, 'utf8'));
const fetch = async () => ({ ok: true, status: 200, json: async () => body });

let result;
if (template === 'fastapi-antd') {
  const sandbox = { fetch, JSON, Array, Object, String, Number, Boolean, Error, encodeURIComponent };
  sandbox.window = sandbox;
  sandbox.sage = { url: (p) => p };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(TEMPLATE + 'fastapi-antd/static/sage/appQuery.js', 'utf8'), sandbox);
  result = await sandbox.sage.runQuery('q');
} else {
  // The module imports `appBase` from a file the dev server resolves; it is "" in the preview.
  const source = fs.readFileSync(TEMPLATE + 'react-vite/src/appQuery.ts', 'utf8')
    .replace(/^import \{ appBase \} from "\.\/appBase";$/m, 'const appBase = "";');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'app-query-'));
  const file = path.join(dir, 'appQuery.ts');
  fs.writeFileSync(file, source);
  globalThis.fetch = fetch;
  result = await (await import(file)).runQuery('q');
  fs.rmSync(dir, { recursive: true, force: true });
}
process.stdout.write(JSON.stringify(result));
