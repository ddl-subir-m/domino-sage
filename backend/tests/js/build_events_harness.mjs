// Loads a Build transcript from the server and reports the blocks it drew.
//
// The defect (#94) is a turn event that persists to `.sage/history.jsonl` and is never drawn:
// `buildHistoryToMessages` is a chain of `ev.type === ...` branches, and a row whose type has no
// branch reaches the transcript and disappears. `data-leak` had shipped that way. Reading
// `buildMessages` after a real `store.loadBuild()` is what tells the two apart — a hand-built block
// would pass whether or not the branch exists.
//
// Nothing is mounted: the branch under test decides what a block IS, and the components that draw a
// `status` block have their own tests.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history } = JSON.parse(fs.readFileSync(0, 'utf8'));

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url) {
  const path = String(url).replace(/^\.\/api/, '');
  if (path.startsWith('/project/history')) return json({ history });
  if (path.startsWith('/apps')) return json({ items: [] });
  if (path.startsWith('/bindings')) return json({ bindings: [] });
  return json({});
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  localStorage: (() => {
    const backing = new Map();
    return {
      getItem: (k) => (backing.has(k) ? backing.get(k) : null),
      setItem: (k, v) => backing.set(k, String(v)),
      removeItem: (k) => backing.delete(k),
    };
  })(),
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
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

await SW.store.loadBuild();
await settle();

const blocks = SW.store.get().buildMessages.flatMap((m) => m.blocks || []);
console.log(JSON.stringify({
  values: blocks.filter((b) => b.type === 'status').map((b) => b.value),
  types: blocks.map((b) => b.type),
}));
