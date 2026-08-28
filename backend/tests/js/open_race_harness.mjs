// Opens two conversations at once and reports which one the store settled on.
//
// `openThread` awaits three times before it writes anything. Unguarded, the response that lands
// LAST wins, which is not the same as the one the person asked for last. This harness makes that
// difference visible by serving one conversation slowly and the other quickly, then asking for the
// slow one first — so a store without a generation guard settles on the conversation nobody
// clicked, while the route and the rail say the other.
//
// Store only: no component is mounted, because the defect is in the store and the two callers
// (modes/chat.js, modes/builder.js) only forward a route parameter into it.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

// --- the server ------------------------------------------------------------
// Two conversations that are distinguishable at every level the store touches: their own title,
// their own turn, their own context row. A view assembled from two of them is not a coherent
// conversation, and that is what the assertions look for.
const THREADS = {
  conv_slow: { id: 'conv_slow', title: 'Slow', history: [{ type: 'user', text: 'slow turn' }],
               artifacts: [], touched: [] },
  conv_fast: { id: 'conv_fast', title: 'Fast', history: [{ type: 'user', text: 'fast turn' }],
               artifacts: [], touched: [] },
};
const CONTEXT = {
  conv_slow: [{ id: 'ctx_slow', kind: 'file', name: 'slow.csv', path: 'slow.csv',
                addedBy: 'user', resourceId: 'file:slow.csv' }],
  conv_fast: [{ id: 'ctx_fast', kind: 'file', name: 'fast.csv', path: 'fast.csv',
                addedBy: 'user', resourceId: 'file:fast.csv' }],
};

// How long each conversation's own GET takes. The point of the whole harness.
let latency = {};

const json = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  let m;
  if ((m = path.match(/^\/threads\/([^/]+)\/context$/))) return json({ items: CONTEXT[m[1]] || [] });
  if ((m = path.match(/^\/threads\/([^/]+)$/))) return json(THREADS[m[1]] || { id: m[1], history: [] });
  if (path === '/threads') return json({ items: Object.values(THREADS) });
  return json({});
}

function delayFor(url) {
  const m = String(url).match(/\/threads\/([^/?]+)/);
  return (m && latency[m[1]]) || 0;
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment',
  },
  antd: {
    Input: { TextArea: 'Input.TextArea' }, Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag',
    Tooltip: 'Tooltip', Space: 'Space', Modal: { confirm() {} },
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url) => {
    await new Promise((r) => setTimeout(r, delayFor(url)));
    return serve(url);
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

function snapshot(extra = {}) {
  const state = SW.store.get();
  return {
    thread: state.thread ? state.thread.id : null,
    title: state.thread ? state.thread.title : null,
    // The rest of the view, so a half-applied open shows up as a mismatch rather than passing.
    turns: (state.messages || []).map((m) => (m.blocks || [])
      .map((b) => b.value || '').join('')).filter(Boolean),
    context: (state.attachments || []).map((a) => a.resourceName || a.name),
    ...extra,
  };
}

const report = [];
for (const step of steps) {
  if (step.race) {
    // Ask for the slow one FIRST. A store that keeps whichever answer lands last settles on it;
    // a store that knows which open is current keeps the second.
    latency = step.race.latency || {};
    const first = SW.store.openThread(step.race.first);
    const second = SW.store.openThread(step.race.second);
    const [a, b] = await Promise.all([first, second]);
    report.push({ step: 'race', ...snapshot({
      firstReturned: a ? a.id : null, secondReturned: b ? b.id : null }) });
  } else if (step.open) {
    // The ordinary path, one at a time. Guarding must not cost this.
    latency = step.latency || {};
    const t = await SW.store.openThread(step.open);
    report.push({ step: `open ${step.open}`, ...snapshot({ returned: t ? t.id : null }) });
  } else {
    throw new Error(`unknown step ${JSON.stringify(step)}`);
  }
}
console.log(JSON.stringify(report));
