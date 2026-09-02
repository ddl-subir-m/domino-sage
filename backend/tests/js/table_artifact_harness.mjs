// What `store.openThread` turns a `.table.json` Artifact into, for #131-style regression: a
// `sage-chat` turn wrote `rows` as pandas-record objects (keyed by column name) instead of the
// documented positional-array shape, and `TableBlock` looked cells up by numeric index — every
// cell rendered blank while the header row and "Show all N rows" count still looked right. This
// drives the real store against a stubbed fetch and mounts nothing (same trick as
// `conversation_view_harness.mjs`): the claim is about the block's `columns`/`rows` data, not
// about antd painting it.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const steps = JSON.parse(fs.readFileSync(0, 'utf8'));

const THREADS = {};
const FILES = {};

const json = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  let m;
  if ((m = path.match(/^\/threads\/([^/]+)\/context$/))) return json({ items: [] });
  if ((m = path.match(/^\/threads\/([^/]+)$/))) return json(THREADS[m[1]] || { id: m[1], history: [] });
  if ((m = path.match(/^\/project\/file\?path=(.+)$/))) {
    const filePath = decodeURIComponent(m[1]);
    return json({ content: FILES[filePath] || '{}' });
  }
  return json({});
}

const backing = new Map();
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, Infinity, encodeURIComponent, decodeURIComponent,
  setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  fetch: (url) => Promise.resolve(serve(url)),
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
  // Not exercised (nothing here calls React or antd APIs), but `util.js` destructures both at
  // module load, so the sandbox needs something there even for a data-only test.
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

const report = [];
for (const step of steps) {
  if (step.thread) {
    THREADS[step.thread.id] = step.thread;
    if (step.file) FILES[step.file.path] = JSON.stringify(step.file.body);
    report.push({ step: `seed ${step.thread.id}` });
  } else if (step.open) {
    await SW.store.openThread(step.open);
    const messages = SW.store.get().messages || [];
    const tableBlocks = messages.flatMap((m) => m.blocks || []).filter((b) => b.type === 'table');
    report.push({ step: `open ${step.open}`, tables: tableBlocks });
  } else {
    throw new Error(`unknown step ${JSON.stringify(step)}`);
  }
}
console.log(JSON.stringify(report));
