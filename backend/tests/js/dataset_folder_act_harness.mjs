// The folder act in the Dataset tree: what every folder row offers, and what the click commits to
// (ADR-0029).
//
// Rendered rather than grepped, because none of the claims is about a string. "The row shows what
// the subtree weighs" is arithmetic over the listing; "the filter does not narrow the act" is a
// question about which numbers reach the row while a query is on screen; and the confirmation is a
// modal the store opens, whose title has to name the count, the size and the app before anything
// is attached.
//
// Nothing is mounted. Hooks are real, per instance, because the tree fetches from an effect and
// draws nothing until the answer lands. The store is BOOTED rather than hand-set: the act reads
// the selected Built App off it, and a fixture assigned into state would be this harness asserting
// its own input back.
//
// Input on stdin: `{ files, folder_act, app, query, press }`.
//   `files` / `folder_act` — the `/files` answer, in the shape the route writes it.
//   `app`                  — the selected Built App's name, or null for none selected.
//   `query`                — the tree's filter box.
//   `press`                — the folder path to press the act on ('' is the Dataset root).
//
// Output: `{ rows, confirm, posted }` — every folder row the tree drew, the confirmation the press
// opened, and the request its OK actually sent.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const input = JSON.parse(fs.readFileSync(0, 'utf8'));

// --- a React with working hooks --------------------------------------------
const slotsOf = new Map();
let counts = null;
let current = null;
let cursor = 0;
let dirty = false;
const pending = [];

function callComponent(fn, props) {
  const name = fn.name || 'anonymous';
  const n = counts.get(name) || 0;
  counts.set(name, n + 1);
  const id = `${name}#${n}`;
  if (!slotsOf.has(id)) slotsOf.set(id, []);
  const wasId = current;
  const wasCursor = cursor;
  current = id;
  cursor = 0;
  try {
    return fn(props);
  } finally {
    current = wasId;
    cursor = wasCursor;
  }
}

const React = {
  createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
  Fragment: 'Fragment',
  useState: (init) => {
    const slots = slotsOf.get(current);
    const i = cursor++;
    if (!(i in slots)) slots[i] = typeof init === 'function' ? init() : init;
    return [slots[i], (next) => {
      const value = typeof next === 'function' ? next(slots[i]) : next;
      if (!Object.is(value, slots[i])) {
        slots[i] = value;
        dirty = true;
      }
    }];
  },
  useEffect: (fn, deps) => {
    pending.push({ slots: slotsOf.get(current), i: cursor++, fn, deps });
  },
  useMemo: (fn) => fn(),
  useRef: (init) => {
    const slots = slotsOf.get(current);
    const i = cursor++;
    if (!(i in slots)) slots[i] = { current: init === undefined ? null : init };
    return slots[i];
  },
};

function runEffects() {
  for (const e of pending.splice(0)) {
    const prev = e.slots[e.i];
    const same = prev && Array.isArray(e.deps) && Array.isArray(prev.deps)
      && prev.deps.length === e.deps.length
      && prev.deps.every((d, k) => Object.is(d, e.deps[k]));
    if (same) continue;
    if (prev && typeof prev.off === 'function') prev.off();
    const off = e.fn();
    e.slots[e.i] = { deps: e.deps, off: typeof off === 'function' ? off : null };
  }
}

// --- the browser -----------------------------------------------------------
const APP = input.app ? [{ id: 'app_a', name: input.app, selected: true }] : [];
const posted = [];
const confirms = [];

const json = (body) => ({
  ok: true, status: 200, statusText: 'OK',
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url, init) {
  const path = String(url).replace(/^\.\/api/, '');
  const method = ((init && init.method) || 'GET').toUpperCase();
  if (/\/files\/attach-folder$/.test(path) && method === 'POST') {
    const body = JSON.parse((init && init.body) || '{}');
    posted.push({ path, folder: body.folder });
    if (input.refuse) {
      return {
        ok: false, status: 413, statusText: 'Payload Too Large',
        headers: { get: () => 'application/json' },
        json: async () => ({ error: input.refuse }),
        text: async () => JSON.stringify({ error: input.refuse }),
      };
    }
    return json({ attached: 4, bytes: 40, dataset: 'Revenue', folder: body.folder });
  }
  if (/^\/project\/assets\/[^/]+\/files$/.test(path)) {
    return json({ files: input.files || [], truncated: !!input.truncated,
                  folder_act: input.folder_act || { available: true, reason: '' } });
  }
  if (path === '/apps') return json({ items: APP });
  if (path === '/project') return json({ attached: [], scratch: [] });
  if (path === '/project/resources') return json({ items: [] });
  if (path === '/bindings') return json({ bindings: [] });
  if (path === '/members') return json({ members: [], directory: [] });
  return json({});
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Infinity, URLSearchParams, Blob, ArrayBuffer, Uint8Array, TextDecoder,
  setTimeout, clearTimeout, setInterval: () => 0,
  clearInterval: () => {},
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '#/build' },
  history: { replaceState() {} },
  addEventListener() {},
  removeEventListener() {},
  React,
  antd: {
    Input: Object.assign(function Input() { return null; }, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag', Tooltip: 'Tooltip', Space: 'Space',
    Spin: 'Spin', Empty: 'Empty', Skeleton: 'Skeleton', Alert: 'Alert', Checkbox: 'Checkbox',
    Drawer: 'Drawer',
    // Kept rather than discarded: the confirmation IS the claim, so its words and its OK have to
    // be reachable from the test that presses it.
    Modal: {
      confirm: (cfg) => {
        confirms.push(cfg);
        return { update: (n) => Object.assign(cfg, n), destroy: () => {} };
      },
    },
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, init) => {
    await new Promise((r) => setTimeout(r, 0));
    return serve(url, init);
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                 'components/platform-error.js', 'components/resource-tree.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

// --- reading the tree ------------------------------------------------------
const SKIP = new Set(['Input', 'anonymous']);

function flatten(node, out = [], depth = 0) {
  if (node === null || node === undefined || typeof node === 'boolean' || depth > 60) return out;
  if (Array.isArray(node)) { node.forEach((n) => flatten(n, out, depth)); return out; }
  if (typeof node === 'string' || typeof node === 'number') return out;
  if (typeof node !== 'object' || !node.t) return out;
  out.push(node);
  if (typeof node.t === 'function' && !SKIP.has(node.t.name)) {
    const child = node.c.filter((c) => c !== undefined);
    flatten(callComponent(node.t, child.length ? Object.assign({}, node.p, { children: child })
                                               : node.p), out, depth + 1);
  }
  flatten(node.c, out, depth + 1);
  return out;
}

function words(node) {
  const out = [];
  const walk = (n, depth) => {
    if (n === null || n === undefined || typeof n === 'boolean' || depth > 40) return;
    if (Array.isArray(n)) { n.forEach((c) => walk(c, depth)); return; }
    if (typeof n === 'string' || typeof n === 'number') { out.push(String(n)); return; }
    if (typeof n === 'object' && n.c) walk(n.c, depth + 1);
  };
  walk(node.c, 0);
  return out.join('');
}

const cls = (node) => String((node.p || {}).className || '');

const settle = () => new Promise((res) => setTimeout(res, 0));

const RESOURCE = { id: 'dataset:ds_1', name: 'Revenue', kind: 'dataset', path: '/mnt/data/revenue',
                   pins: [] };

async function paint() {
  let nodes = [];
  for (let i = 0; i < 15; i += 1) {
    counts = new Map();
    dirty = false;
    nodes = flatten(callComponent(SW.DatasetFileTree,
                                  { resource: RESOURCE, query: input.query || '' }));
    runEffects();
    await settle();
    if (!dirty) break;
  }
  return nodes;
}

// One folder row, read the way a person reads it: what it is called, what it says the subtree
// holds, and whether the act beside it can be pressed — with the reason when it cannot.
function readRow(row) {
  const inner = flatten(row);
  const head = inner.find((n) => cls(n) === 'sw-tree-folder-head' || cls(n) === 'sw-tree-root-name');
  const meta = inner.find((n) => cls(n) === 'sw-tree-folder-meta');
  const button = inner.find((n) => n.t === 'Button');
  const tooltip = inner.find((n) => n.t === 'Tooltip');
  // The folder this row acts on, taken off the act's own props rather than guessed from the name:
  // two partitions can hold a folder with the same name and only the path tells them apart.
  const acts = inner.find((n) => typeof n.t === 'function' && n.t.name === 'FolderActs');
  return {
    name: head ? words(head).trim() : '',
    meta: meta ? words(meta) : '',
    act: button ? words(button) : '',
    disabled: Boolean(button && button.p.disabled),
    reason: tooltip ? String(tooltip.p.title || '') : '',
    press: button && !button.p.disabled ? button.p.onClick : null,
    path: acts ? String(acts.p.path || '') : null,
  };
}

await SW.store.setScope({ id: 'proj', name: 'Demo Project' }, { silent: true });
await SW.store.loadApps();
await settle();

const painted = await paint();
const rows = painted
  .filter((n) => cls(n) === 'sw-tree-folder-row' || cls(n) === 'sw-tree-root-row')
  .map(readRow);

// The press goes through the store's own act, so the confirmation, the app check and the request
// are the real ones. Answering OK is a second step on purpose: what the modal SAYS is the claim
// that has to hold before anybody agrees to it.
let confirm = null;
if (input.press !== undefined && input.press !== null) {
  const target = rows.find((r) => (r.path || '') === input.press);
  if (!target || !target.press) throw new Error(`no act to press on "${input.press}"`);
  target.press();
  await settle();
  const cfg = confirms[confirms.length - 1];
  confirm = cfg ? { title: cfg.title, content: cfg.content, okText: cfg.okText } : null;
  if (cfg && input.confirm) {
    await cfg.onOk();
    await settle();
  }
}

console.log(JSON.stringify({ rows, confirm, posted }));
