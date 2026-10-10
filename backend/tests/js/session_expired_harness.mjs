// Boots the real app.js — Root, store.js and api.js together — against a proxy that serves, then
// lets the Domino login in front of it lapse, and reports what the Workbench made of the answers
// (#754).
//
// Input on stdin: `{ "mode": "html-404" | "html-401" | "html-200" | "html-502" | "json-404" }`.
//
//   html-404  What openresty answered on every path under the session in the #714 rerun.
//   html-401  The same proxy refusing with the status that says so.
//   html-200  A sign-in page served as a success, which `request()` used to read as `{}`.
//   html-502  Control: the proxy's warm-up page. ADR-0027 waits that out; it is not a sign-in.
//   json-404  Control: Sage's own 404, which is Sage answering and nothing to do with the login.
//
// After the boot, three reads cover the three ways the old fetch layer misread a proxy page: the
// app list through `request()` (a 2xx became an empty list, a 4xx kept only its status line), the
// preview status through a direct fetch in the store (a parse error became "Preview is
// unavailable"), and /healthz, which sits outside /api and has its own fetch.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { mode } = JSON.parse(fs.readFileSync(0, 'utf8'));

const PROJECT = {
  id: 'p-acme-risk', name: 'Acme Risk Review', untitled: false, workspace: '/mnt/code',
  attached: [], scratch: [],
  model: { mode: 'ask', selected_mode: 'plan', phase: 'idle', picked_model: 'm', chat_model: 'm',
           reasoning_effort: null, catalog: { plan: ['m'], implement: ['m'], ask: ['m'] } },
  cost: null, manage: null,
};

function node(type, props, ...children) {
  const flat = children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false);
  const tag = typeof type === 'function' ? (type.name || 'Component') : String(type);
  return { tag, type, props: props || {}, children: flat };
}

const hooks = [];
let slot = 0;
const pendingEffects = [];
const React = {
  createElement: node,
  Fragment: 'Fragment',
  useState(initial) {
    const i = slot++;
    if (!hooks[i]) hooks[i] = { value: typeof initial === 'function' ? initial() : initial };
    const cell = hooks[i];
    return [cell.value, (next) => {
      cell.value = typeof next === 'function' ? next(cell.value) : next;
    }];
  },
  useEffect(fn, deps) {
    const i = slot++;
    const prev = hooks[i];
    const changed = !prev || !deps || !prev.deps || deps.length !== prev.deps.length
      || deps.some((d, k) => d !== prev.deps[k]);
    hooks[i] = { deps: deps || null };
    if (changed) pendingEffects.push(fn);
  },
};
let rendered = null;
const ReactDOM = { createRoot: () => ({ render: (el) => { rendered = el; } }) };

const json = (body, status = 200) => ({
  ok: status < 300, status, statusText: status === 404 ? 'Not Found' : 'OK',
  headers: { get: (k) => (k.toLowerCase() === 'content-type' ? 'application/json' : null) },
  json: async () => body, text: async () => JSON.stringify(body),
});

// The proxy's own page, with the parse error a browser really raises on it.
const STATUS_TEXT = { 200: 'OK', 401: 'Unauthorized', 404: 'Not Found', 502: 'Bad Gateway' };
const html = (status) => ({
  ok: status < 300, status, statusText: STATUS_TEXT[status],
  headers: { get: (k) => (k.toLowerCase() === 'content-type' ? 'text/html' : null) },
  json: async () => {
    throw new SyntaxError(`Unexpected token '<', "<html> <h"... is not valid JSON`);
  },
  text: async () => `<html> <head><title>${status} ${STATUS_TEXT[status]}</title></head>`
    + '<body><center><h1>openresty</h1></center></body></html>',
});

let lapsed = false;
const lapsedAnswer = () => {
  if (mode === 'json-404') return json({ detail: 'Not Found' }, 404);
  return html(Number(mode.split('-')[1]));
};

const sandbox = {
  console, JSON, Math, Date, process, Set, Map, Promise, Array, Object, String, Number, Boolean,
  RegExp, Error, SyntaxError, TypeError, Proxy, TextEncoder, TextDecoder, URL, URLSearchParams,
  setTimeout: unrefTimeout, clearTimeout, setInterval: unrefTimeout, clearInterval: clearTimeout,
  Blob, ArrayBuffer, Uint8Array,
  React, ReactDOM,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  requestAnimationFrame: (fn) => fn(),
  location: { hash: '#/chat', reload() {} },
  addEventListener() {},
  removeEventListener() {},
  document: {
    addEventListener() {}, removeEventListener() {}, querySelector: () => null,
    getElementById: () => ({}), body: {}, documentElement: { style: { setProperty() {} } },
  },
  antd: {
    ConfigProvider: function ConfigProvider() {}, App: function AntApp() {},
    Result: function Result() {}, Button: function Button() {}, Spin: function Spin() {},
    message: { success() {}, error() {}, info() {}, warning() {} },
    notification: { open() {}, error() {}, warning() {}, info() {} },
    Modal: { confirm() {}, info() {}, error() {} },
    theme: { defaultAlgorithm: null },
  },
  fetch: async (url) => {
    const href = String(url);
    if (lapsed) return lapsedAnswer();
    const path = href.split('?')[0].replace(/^\.\/api/, '');
    if (path === './healthz') return json({ ok: true, open_weight_models: [] });
    if (path === '/project') return json(PROJECT);
    if (path === '/projects') return json({ items: [], provisioning: false });
    if (path === '/me') return json({ id: 'u1', name: 'Dana Reed' });
    if (path === '/threads') return json([]);
    if (path === '/members') return json({ members: [], directory: [] });
    if (path === '/assets') return json({ assets: [] });
    return json({});
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

for (const f of ['theme.js', 'util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                 'components/platform-error.js', 'app.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
sandbox.SW.Shell = function Shell() {};
const SW = sandbox.SW;
const Root = rendered.type;

function draw() {
  slot = 0;
  const tree = Root({});
  while (pendingEffects.length) pendingEffects.shift()();
  return tree;
}

function words(n, out = []) {
  if (n === null || n === undefined || typeof n === 'boolean') return out;
  if (typeof n === 'string' || typeof n === 'number') { out.push(String(n)); return out; }
  if (Array.isArray(n)) { n.forEach((c) => words(c, out)); return out; }
  if (!n.tag) return out;
  Object.entries(n.props).forEach(([key, v]) => {
    if (key === 'className' || typeof v === 'function') return;
    if (v && (typeof v === 'object' || typeof v === 'string')) words(v, out);
  });
  n.children.forEach((c) => words(c, out));
  return out;
}

function tags(n, out = []) {
  if (!n || typeof n !== 'object' || !n.tag) return out;
  out.push(n.tag);
  Object.values(n.props).forEach((v) => { if (v && typeof v === 'object') tags(v, out); });
  n.children.forEach((c) => tags(c, out));
  return out;
}

// The Button whose label is `label`, so its click can be driven rather than read.
function button(n, label) {
  if (!n || typeof n !== 'object' || !n.tag) return null;
  if (n.tag === 'Button' && words(n.children).join('') === label) return n;
  for (const v of [...Object.values(n.props), ...n.children]) {
    const hit = button(v, label);
    if (hit) return hit;
  }
  return null;
}

const settle = () => new Promise((r) => setTimeout(r, 0));

draw();
for (let i = 0; i < 50 && !SW.store.get().ready; i += 1) await settle();
await settle();
const bootTags = tags(draw());

lapsed = true;
const outcome = async (read) => {
  try {
    const value = await read();
    return { value };
  } catch (err) {
    return { error: { name: err.name, message: err.message, status: err.status,
                      typed: !!SW.api.SessionExpiredError && err instanceof SW.api.SessionExpiredError } };
  }
};
const apps = await outcome(() => SW.api.apps());
const buildState = await outcome(() => SW.api.buildState());
const healthz = await outcome(() => SW.api.healthz());
await SW.store.refreshPreview({ statusOnly: true });
const afterPreview = SW.store.get();
const preview = {
  status: afterPreview.previewStatus,
  error: afterPreview.previewDetail && afterPreview.previewDetail.error,
};

const tree = draw();
let reloaded = 0;
sandbox.location.reload = () => { reloaded += 1; };
const signIn = button(tree, 'Sign in again');
if (signIn && signIn.props.onClick) signIn.props.onClick();

console.log(JSON.stringify({
  bootTags,
  apps,
  buildState,
  healthz,
  preview,
  sessionExpired: SW.store.get().sessionExpired === true,
  tags: tags(tree),
  words: words(tree).filter((w) => w.trim()),
  reloaded,
}));
process.exit(0);
