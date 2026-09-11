// Whether a Build transcript draws the offer to start the model over, and whether it stops.
//
// Two branches under test, both in `buildHistoryToMessages`. The card had no branch at all in
// Build, so a `recall-suggest` the server wrote reached the transcript and disappeared — the same
// shape as the `data-leak` defect (#94), which is why this harness is that one's. And the rule
// deciding which offer is LIVE is shared with Chat: a clear retires the offers above it, so the
// re-read that runs straight after a clear stopped drawing the card it had just acted on.
//
// Nothing is mounted. The branch decides what the block IS; the component that draws it has its own
// test (`recall_offer_harness.mjs`).
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history, dismiss } = JSON.parse(fs.readFileSync(0, 'utf8'));

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

// "Not now" is this tab's alone and is deliberately never written to the transcript, so the only
// way to check it holds is to dismiss and then make the transcript be rebuilt — which is what the
// poll does every two seconds, and what used to bring the card straight back.
if (dismiss !== undefined) {
  SW.store.dismissBuildRecallOffer(dismiss);
  await SW.store.loadBuild();
  await settle();
}

const blocks = SW.store.get().buildMessages.flatMap((m) => m.blocks || []);
console.log(JSON.stringify({
  types: blocks.map((b) => b.type),
  offers: blocks.filter((b) => b.type === 'recall_offer')
    .map((b) => ({ scope: b.scope, surface: b.surface, offerKey: b.offerKey })),
  cleared: blocks.filter((b) => b.type === 'recall_cleared').map((b) => b.scope),
  statuses: blocks.filter((b) => b.type === 'status').map((b) => b.value),
}));
