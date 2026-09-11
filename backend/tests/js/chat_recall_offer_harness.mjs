// Which offer to start the model over a CHAT transcript still draws (ADR-0022).
//
// The rule is shared with Build and was wrong for both: the reduce picked the newest
// `recall-suggest` whatever followed it, so `clearRecall` — which re-opens the Thread the moment it
// returns — drew the card again, buttons and all, one render after it had been used.
//
// Nothing is mounted. This is `historyToMessages` deciding what the block IS; the component that
// draws it has its own test (`recall_offer_harness.mjs`).
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history, dismiss } = JSON.parse(fs.readFileSync(0, 'utf8'));

const THREAD = { id: 'thr_1', title: 'The claims question', artifacts: [], handoff: null };

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, TextEncoder, TextDecoder, URL, URLSearchParams, Blob, ArrayBuffer, Uint8Array,
  setTimeout, clearTimeout, setInterval, clearInterval,
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
    const path = String(url).replace(/^\.\/api/, '');
    if (path === '/threads/thr_1') return json({ ...THREAD, history });
    if (path.startsWith('/threads/thr_1/context')) return json({ items: [] });
    if (path.startsWith('/threads')) return json({ threads: [] });
    if (path.startsWith('/apps')) return json({ items: [] });
    if (path.startsWith('/bindings')) return json({ bindings: [] });
    return json({});
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

SW.store.set({ scope: { id: 'proj', name: 'Demo Project' }, threads: [], attachments: [] });

await SW.store.openThread('thr_1');
await settle();

// "Not now" is never written to the transcript, so the only way to check it holds is to dismiss and
// then re-open — which is what `clearRecall` and every navigation back to this Thread do.
if (dismiss !== undefined) {
  SW.store.dismissRecallOffer(dismiss);
  await SW.store.openThread('thr_1');
  await settle();
}

const blocks = SW.store.get().messages.flatMap((m) => m.blocks || []);
console.log(JSON.stringify({
  offers: blocks.filter((b) => b.type === 'recall_offer')
    .map((b) => ({ scope: b.scope, surface: b.surface || null, offerKey: b.offerKey })),
  cleared: blocks.filter((b) => b.type === 'recall_cleared').map((b) => b.scope),
}));
