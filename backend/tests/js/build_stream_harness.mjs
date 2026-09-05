// Drives a real Build turn through the real `store.sendBuildPrompt` and reports what the person
// would have been left looking at.
//
// Sibling of build_events_harness, and NOT a duplicate of it: that one replays
// `.sage/history.jsonl` through `buildHistoryToMessages`, which is the reloaded transcript. This
// one feeds SSE frames through `readSSE`, which is the live one. The two see different events —
// `plan-stale` is yielded but never persisted, so it exists only on this path — and they answer
// different questions. Whether a plan card still offers its Approve button while the person sits
// and watches is a fact about the live path alone.
//
// It also counts `/health` reads, because `readSSE` fires `store.refreshProblems()` once per failed
// stream (ADR-0027). Whether a turn is a failure worth paying a gateway listing for is a fact about
// sequence, not shape, so reading the source cannot answer it.
//
// Input on stdin: `{ "history": [...], "events": [...] }` — the transcript the turn starts from,
// then the frames the server sends for it.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history = [], events = [] } = JSON.parse(fs.readFileSync(0, 'utf8'));

let healthCalls = 0;

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// One SSE frame per chunk, which is how they actually arrive — a single blob would let a reader
// that only ever splits once still pass.
function sseResponse(frames) {
  const enc = new TextEncoder();
  const chunks = frames.map((ev) => enc.encode(`data: ${JSON.stringify(ev)}\n\n`));
  let i = 0;
  return {
    ok: true, status: 200,
    headers: { get: () => 'text/event-stream' },
    body: {
      getReader: () => ({
        read: async () => (i < chunks.length
          ? { done: false, value: chunks[i++] }
          : { done: true, value: undefined }),
      }),
    },
  };
}

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  if (path.includes('health')) { healthCalls += 1; return json({ problems: [] }); }
  if (path.startsWith('/project/build/stream')) return sseResponse(events);
  if (path.startsWith('/project/history')) return json({ history });
  if (path.startsWith('/apps')) return json({ items: [] });
  if (path.startsWith('/bindings')) return json({ bindings: [] });
  return json({});
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, TextEncoder, TextDecoder,
  setTimeout, clearTimeout, setInterval, clearInterval,
  encodeURIComponent, decodeURIComponent, URLSearchParams,
  requestAnimationFrame: (fn) => fn(),
  localStorage: (() => {
    const backing = new Map();
    return {
      getItem: (k) => (backing.has(k) ? backing.get(k) : null),
      setItem: (k, v) => backing.set(k, String(v)),
      removeItem: (k) => backing.delete(k),
    };
  })(),
  location: { search: '', pathname: '/', href: 'http://localhost/', hash: '#/build' },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment',
  },
  antd: { message: { success() {}, info() {}, warning() {}, error() {} } },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url) => {
    await new Promise((r) => setTimeout(r, 0));
    return serve(url);
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

async function settle() {
  for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0));
}

SW.store.set({
  thread: { id: 'conv_1', title: 'The desk talk', artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  threads: [],
  activeApp: { id: 'app_a' },
});

// The transcript this turn starts from — usually a plan already awaiting approval.
await SW.store.loadBuild();
await settle();
// `refreshProblems` is armed by the boot path too; only the turn's own reads are the question here.
healthCalls = 0;

await SW.store.sendBuildPrompt('run: env | grep -i canary');
await settle();

const blocks = SW.store.get().buildMessages.flatMap((m) => m.blocks || []);
console.log(JSON.stringify({
  healthCalls,
  values: blocks.filter((b) => b.type === 'status').map((b) => b.value),
  plans: blocks.filter((b) => b.type === 'build_plan')
    .map((b) => ({ pending: !!b.pending, cancelled: !!b.cancelled })),
}));
