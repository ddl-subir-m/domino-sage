// How `store.openThread` READS the Artifacts of a Thread it is replaying, rather than what it
// makes of any one of them (that is `table_artifact_harness.mjs`). Opening a long investigation
// fetched its `.table.json` files one after another — 60 serial round trips through the Domino
// proxy before the first card drew (#451) — so the claims here are about the shape of the traffic
// and the order of the result, not about a block's contents.
//
// The stub fetch serves each file after a per-file delay, so a LATER table can come back BEFORE
// an earlier one; it records every URL the store asks for, and the high-water mark of reads in
// flight at once. A serial loop shows a high-water mark of 1; an unbounded `Promise.all` shows as
// many as there are tables.
//
// Input on stdin: `{thread, files}`. `files` maps a path to `{content?, body?, delayMs?, fail?}`,
// where `fail` is "network" (the fetch itself rejects) or "status" (a 500 with an error body).
// Mounts nothing, same trick as `conversation_view_harness.mjs`.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
const FILES = spec.files || {};

const requests = [];
let inFlight = 0;
let maxInFlight = 0;

const json = (body, status = 200) => ({
  ok: status < 400, status, statusText: status < 400 ? 'OK' : 'Server Error',
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  let m;
  if (path.match(/^\/threads\/([^/]+)\/context$/)) return json({ items: [] });
  if ((m = path.match(/^\/threads\/([^/]+)$/))) {
    return json(spec.thread && spec.thread.id === m[1] ? spec.thread : { id: m[1], history: [] });
  }
  if ((m = path.match(/^\/project\/file\?path=(.+)$/))) {
    const filePath = decodeURIComponent(m[1]);
    requests.push(filePath);
    const file = FILES[filePath] || {};
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    try {
      await sleep(file.delayMs || 0);
      if (file.fail === 'network') throw new Error(`cannot reach ${filePath}`);
      if (file.fail === 'status') return json({ error: 'the file is gone' }, 500);
      return json({ content: file.content ?? JSON.stringify(file.body ?? {}) });
    } finally {
      inFlight -= 1;
    }
  }
  return json({});
}

const backing = new Map();
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, Infinity, encodeURIComponent, decodeURIComponent,
  setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  fetch: (url) => serve(url),
  localStorage: {
    getItem: (k) => (backing.has(k) ? backing.get(k) : null),
    setItem: (k, v) => backing.set(k, String(v)),
    removeItem: (k) => backing.delete(k),
  },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  location: { hash: '' },
  history: { replaceState() {} },
  addEventListener() {},
  removeEventListener() {},
  // Not exercised, but `util.js` destructures both at module load.
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment',
  },
  antd: {
    Input: { TextArea: 'Input.TextArea' }, Button: 'Button', Dropdown: 'Dropdown', Tag: 'Tag',
    Tooltip: 'Tooltip', Space: 'Space', Modal: { confirm() {} }, Table: 'Table',
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const file of ['util.js', 'api.js', 'prefs.js', 'store.js']) {
  vm.runInContext(fs.readFileSync(ROOT + file, 'utf8'), sandbox, { filename: file });
}
const SW = sandbox.SW;

await SW.store.openThread(spec.thread.id);
const messages = SW.store.get().messages || [];
const blocks = messages.flatMap((m) => m.blocks || []);
console.log(JSON.stringify({
  // `path` for a card, `value` for the sentence around it, so an ordering claim can be written
  // against one list whatever kinds it mixes.
  blocks: blocks.map((b) => ({ type: b.type, path: b.path || b.src || null, value: b.value })),
  requests,
  maxInFlight,
}));
