// Where a template's preview reporters send Sage's control calls, run from the template's own files.
//
// Input on stdin: `{ "stack": "fastapi-antd" | "react-vite", "base": <the preview's base>,
// "abort"?: true }`, or `{ "stack": "vite-config", "prefix": <SAGE_BASE_PREFIX>, "app": <SAGE_PREVIEW_APP> }`.
// Output: `{ "calls": [<pathnames fetched>] }` for a reporter — after the page loads, reports one
// runtime error, has one data fetch refused (or aborted, with `abort`) and asks whether a build is
// running — or
// `{ "api": <the API the build-aware overlay asks> }` for `vite.config.ts`.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';

const TEMPLATE = new URL('../../../template/', import.meta.url).pathname;
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = [];

function browser(base) {
  const origin = 'http://sage.test';
  const location = { origin, href: origin + base + '?sageValidation=v1', search: '?sageValidation=v1' };
  const fetch = async (url) => {
    const pathname = new URL(String(url), location.href).pathname;
    calls.push(pathname);
    if (pathname.includes('/api/queries/')) {
      throw input.abort ? new DOMException('aborted', 'AbortError') : new Error('refused');
    }
    return { ok: true, json: async () => ({ running: false }) };
  };
  const document = { readyState: 'complete', visibilityState: 'visible', addEventListener() {} };
  return { location, fetch, document, addEventListener() {} };
}

async function exercise(report, buildIsRunning, base) {
  report('boom', 'at App');
  await globalThis.__page.fetch(base.replace(/\/?$/, '/') + 'api/queries/q').catch(() => {});
  await buildIsRunning();
  await new Promise((r) => setTimeout(r, 0));
}

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'preview-api-'));
}

if (input.stack === 'fastapi-antd') {
  const sandbox = { ...browser(input.base), URL, URLSearchParams, Headers, Request, JSON, Promise, String };
  sandbox.window = sandbox;
  sandbox.sage = { base: input.base, preview: true };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(TEMPLATE + 'fastapi-antd/static/sage/reportRuntimeError.js', 'utf8'), sandbox);
  globalThis.__page = sandbox;
  await exercise(sandbox.sage.reportRuntimeError, sandbox.sage.buildIsRunning, input.base);
  process.stdout.write(JSON.stringify({ calls }));
} else if (input.stack === 'react-vite') {
  const source = fs.readFileSync(TEMPLATE + 'react-vite/src/reportRuntimeError.ts', 'utf8')
    .replaceAll('import.meta.env.BASE_URL', JSON.stringify(input.base))
    .replaceAll('import.meta.env.DEV', 'true');
  const dir = tmpdir();
  const file = path.join(dir, 'reportRuntimeError.ts');
  fs.writeFileSync(file, source);
  Object.assign(globalThis, browser(input.base));
  globalThis.window = globalThis;
  globalThis.__page = globalThis;
  try {
    const mod = await import(file);
    await exercise(mod.reportRuntimeError, mod.buildIsRunning, input.base);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
  process.stdout.write(JSON.stringify({ calls }));
} else {
  // The config imports Vite and its React plugin, which only the template's own install carries.
  const source = fs.readFileSync(TEMPLATE + 'react-vite/vite.config.ts', 'utf8')
    .replace(/^import react from "@vitejs\/plugin-react";$/m, 'const react = () => ({});')
    .replace(/^import \{ defineConfig \} from "vite";$/m, 'const defineConfig = (f) => f;');
  const dir = tmpdir();
  fs.copyFileSync(TEMPLATE + 'react-vite/package.json', path.join(dir, 'package.json'));
  const file = path.join(dir, 'vite.config.ts');
  fs.writeFileSync(file, source);
  process.env.SAGE_BASE_PREFIX = input.prefix;
  process.env.SAGE_PREVIEW_APP = input.app;
  try {
    const config = (await import(file)).default({ command: 'serve' });
    const overlay = config.plugins.find((p) => p.name === 'sage-build-aware-overlay');
    const script = overlay.transformIndexHtml()[0].children;
    process.stdout.write(JSON.stringify({ api: JSON.parse(script.match(/var API = (".*?");/)[1]) }));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}
