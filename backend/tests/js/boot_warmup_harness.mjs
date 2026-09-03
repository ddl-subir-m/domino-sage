// Drives the real app.js boot — Root, store.init and api.js together — against a proxy that is
// still warming up, and reports what the screen said at every step (ADR-0027).
//
// Reading the source cannot answer this one. The interesting states are the ones BETWEEN two
// tries: whether the boot screen is a bare spinner or a sentence while it waits, and whether the
// full-page wall arrives for a 502 that was clearing itself the whole time. Both are moments, not
// lines, and only running the boot has them.
//
// Input on stdin: `{ "mode": "clears" | "outlasts" | "unhealthy" }`.
//
//   clears    nginx answers 502 for the first three seconds, the way a Domino workspace whose
//             proxy is in front of a port nobody is listening on yet does, and then serves. This
//             is the whole point: nobody should ever learn that this happened.
//   outlasts  502 forever. The retry is a wait, not a blindfold — a fault that outlives the
//             budget still has to reach the wall, and with the platform's own words on it.
//   unhealthy /healthz alone answers 500 and everything else serves: a Sage that was reached and
//             has something to say. Not a warm-up, so it is neither waited out nor walled.
//
// Time is virtual. The budget in api.js is ten real seconds and stays that way; the timers below
// are a queue the pump drains in order, so this harness proves the shipped budget rather than a
// shortened one, and does it in milliseconds.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { mode } = JSON.parse(fs.readFileSync(0, 'utf8'));

// When the proxy starts serving, in virtual milliseconds. Inside api.js's ten-second budget on
// purpose, and past enough of the backoff that the wait is observable rather than a single blink.
const SERVING_AT = 3000;

const PROJECT = {
  id: 'p-acme-risk',
  name: 'Acme Risk Review',
  untitled: false,
  workspace: '/mnt/code',
  attached: [],
  scratch: [],
  model: {
    mode: 'ask',
    selected_mode: 'plan',
    phase: 'idle',
    picked_model: 'claude-sonnet-4',
    chat_model: 'claude-sonnet-4',
    reasoning_effort: null,
    catalog: { plan: ['claude-sonnet-4'], implement: ['claude-sonnet-4'], ask: ['claude-sonnet-4'] },
  },
  cost: null,
  manage: null,
};

// ---- virtual clock ---------------------------------------------------------------------------
// Every timer the boot sets lands here instead of the event loop. `now` only moves when the pump
// fires one, so a backoff that api.js thinks took two seconds costs this process nothing.
let now = 1_700_000_000_000;
let nextTimer = 1;
const timers = new Map();
const fakeSetTimeout = (fn, ms = 0) => {
  const id = nextTimer++;
  timers.set(id, { at: now + (Number(ms) || 0), seq: id, fn });
  return id;
};
const fakeClear = (id) => { timers.delete(id); };
function fireEarliestTimer() {
  let pick = null;
  for (const [id, t] of timers) {
    if (!pick || t.at < pick.t.at || (t.at === pick.t.at && t.seq < pick.t.seq)) pick = { id, t };
  }
  if (!pick) return false;
  timers.delete(pick.id);
  now = Math.max(now, pick.t.at);
  pick.t.fn();
  return true;
}
const RealDate = Date;
class FakeDate extends RealDate {
  static now() { return now; }
}
// Virtual milliseconds since the boot started, which is what the fetch stub answers on.
const started = now;

// ---- what the browser would build ------------------------------------------------------------
// Shallow on purpose: a child component is recorded by name and not called, so this reads the tree
// app.js builds rather than the whole Workbench under it.
function node(type, props, ...children) {
  const flat = [];
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    flat.push(child);
  }
  const tag = typeof type === 'function' ? (type.name || 'Component') : String(type);
  // `type` is kept as well as its name: the element app.js hands ReactDOM is the only reference to
  // its Root, and calling it is how this harness renders at all.
  return { tag, type, props: props || {}, children: flat };
}

// ---- hooks -----------------------------------------------------------------------------------
// The smallest thing that keeps `error` across renders: one slot list, walked in order, reset at
// the top of each render. Effects run once per slot unless their deps change, and a re-render is
// the pump's job rather than a scheduler's — nothing here is testing React.
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

const fetched = [];
const sandbox = {
  console, JSON, Math, Date: FakeDate, process, Set, Map, Promise, Array, Object, String, Number,
  Boolean, RegExp, Error, Proxy, TextEncoder, TextDecoder, URL, URLSearchParams,
  setTimeout: fakeSetTimeout, clearTimeout: fakeClear,
  setInterval: fakeSetTimeout, clearInterval: fakeClear,
  Blob, ArrayBuffer, Uint8Array,
  React,
  ReactDOM,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  requestAnimationFrame: (fn) => fn(),
  location: { hash: '#/chat' },
  addEventListener() {},
  removeEventListener() {},
  document: {
    addEventListener() {},
    removeEventListener() {},
    querySelector: () => null,
    getElementById: () => ({}),
    body: {},
    documentElement: { style: { setProperty() {} } },
    title: '',
  },
  antd: {
    ConfigProvider: function ConfigProvider() {}, App: function AntApp() {},
    Result: function Result() {}, Button: function Button() {}, Spin: function Spin() {},
    message: { success() {}, error() {}, info() {}, warning() {} },
    notification: { open() {}, error() {}, warning() {}, info() {} },
    Modal: { confirm() {}, info() {}, error() {} },
    theme: { defaultAlgorithm: null },
  },
  // What nginx answers in front of a port nobody is listening on yet: a 502 whose body is HTML, so
  // `api.js` finds no JSON `error` field and is left holding the bare status line. That is the
  // string this whole ticket is about, and it has to come from the stub rather than be asserted
  // into existence.
  fetch: async (url) => {
    const href = String(url);
    fetched.push({ href, at: now - started });
    if (mode === 'outlasts' || (mode !== 'unhealthy' && now - started < SERVING_AT)) {
      return {
        ok: false, status: 502, statusText: 'Bad Gateway',
        headers: { get: () => 'text/html' },
        json: async () => { throw new Error('Unexpected token < in JSON'); },
        text: async () => '<html><head><title>502 Bad Gateway</title></head></html>',
      };
    }
    // Sage's own refusal, in Sage's own shape: a status and a sentence. The proxy is serving.
    if (mode === 'unhealthy' && href.includes('healthz')) {
      return {
        ok: false, status: 500, statusText: 'Internal Server Error',
        headers: { get: () => 'application/json' },
        json: async () => ({ detail: 'the gateway did not answer' }),
        text: async () => '{"detail": "the gateway did not answer"}',
      };
    }
    const json = (body) => ({
      ok: true, status: 200, headers: { get: () => 'application/json' },
      json: async () => body, text: async () => JSON.stringify(body),
    });
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

// The page's own order (index.html), because app.js's boot is the thing under test and it closes
// over whatever is on `SW` when it runs.
for (const f of ['theme.js', 'util.js', 'api.js', 'store.js', 'prefs.js', 'router.js',
                 'components/platform-error.js', 'app.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
// Named so a booted Workbench is recognisable in the tree; the real one is not loaded, because
// what it draws is not this harness's question.
sandbox.SW.Shell = function Shell() {};

// `root.render(h(Root))` ran at load, so the element it handed ReactDOM is the way in.
const Root = rendered.type;

// One render, then the effects it queued. The first of those is the one that starts the boot.
function draw() {
  slot = 0;
  const tree = Root({});
  while (pendingEffects.length) pendingEffects.shift()();
  return tree;
}

// Everything the screen says, flattened. Prop values are walked as well as children, because a
// `Result` carries its title and its body in props and that is where the wall's words live.
function words(n, out = []) {
  if (n === null || n === undefined || typeof n === 'boolean') return out;
  if (typeof n === 'string' || typeof n === 'number') { out.push(String(n)); return out; }
  if (Array.isArray(n)) { n.forEach((c) => words(c, out)); return out; }
  if (!n.tag) return out;
  Object.entries(n.props).forEach(([key, v]) => {
    // `className` is a hook for the tests to find an element by, never something anybody reads.
    if (key === 'className') return;
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

// The label under the spinner, or null when the screen is not the boot screen.
function bootLabel(tree) {
  const found = [];
  (function walk(n) {
    if (!n || typeof n !== 'object' || !n.tag) return;
    if (n.props.className === 'sw-boot-label') found.push(words(n).join(''));
    n.children.forEach(walk);
  }(tree));
  return found.length ? found[0] : null;
}

// The pump: microtasks, then the earliest virtual timer, then a redraw — until the boot settles.
// Every screen it draws on the way is kept, because "what did they see while it waited" is the
// question and the answer is a sequence, not an end state.
const screens = [];
let initSettled = false;
let initError = null;
const realTick = () => new Promise((r) => setTimeout(r, 0));

// Root's effect calls store.init(); this watches the same promise from outside, so a reject is
// recorded even though app.js swallows it into `error`.
const realInit = sandbox.SW.store.init;
sandbox.SW.store.init = function init(...args) {
  return realInit.apply(this, args).then(
    (v) => { initSettled = true; return v; },
    (err) => { initSettled = true; initError = String((err && err.message) || err); throw err; },
  );
};

screens.push(bootLabel(draw()));
for (let i = 0; i < 4000; i += 1) {
  await realTick();
  const label = bootLabel(draw());
  if (label !== screens[screens.length - 1]) screens.push(label);
  if (initSettled && timers.size === 0) break;
  if (!fireEarliestTimer()) await realTick();
}

// One last pass, after the state the settled boot left behind.
const finalTree = draw();
const state = sandbox.SW.store.get();
console.log(JSON.stringify({
  // Every distinct thing the boot screen said, in order. `null` is a screen that is not the boot
  // screen — the Workbench, or the wall.
  screens,
  ready: state.ready,
  bootStatus: state.bootStatus,
  initResolved: initError === null,
  initError,
  // What is on screen at the end: `Shell` for a Workbench, `Result` for the wall.
  finalTags: tags(finalTree),
  finalWords: words(finalTree).filter((w) => w.trim()),
  // Virtual milliseconds and calls spent getting there, so a budget that stopped retrying — or
  // never stopped — is visible rather than inferred.
  elapsedMs: now - started,
  healthzTries: fetched.filter((f) => f.href.includes('healthz')).length,
}));
process.exit(0);
