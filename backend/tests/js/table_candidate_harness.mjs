// Drives the real store.js through a table candidate card: what the transcript draws for one, and
// what a click on it calls.
//
// Run rather than read, because both halves break silently. `buildHistoryToMessages` is a chain of
// `ev.type === ...` branches and a row whose type has no branch reaches the transcript and
// disappears — which is how a candidate card would become a turn that answered nothing. And the
// click is TWO acts, write the record then send the request the person already made against it:
// drop the second and their dashboard is never built, drop `skipTableGate` from it and the build
// hands back the same card it was answering. Neither throws, and the Python suite stays green.
//
// stdin is a Build history. stdout is one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history, prompt, answered } = JSON.parse(fs.readFileSync(0, 'utf8'));

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// What the replayed build streams back, once the record is written and the card is answered.
const built = [
  { type: 'agent', kind: 'text', text: 'Built the daily calls dashboard.' },
  { type: 'done', ok: true, decision: 'built' },
].map((f) => `data: ${JSON.stringify(f)}\n\n`).join('');

const calls = [];
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
  fetch: async (url, opts) => {
    calls.push({ url: String(url), body: opts && opts.body });
    const path = String(url).replace(/^\.\/api/, '');
    if (path.startsWith('/project/build/stream')) {
      let sent = false;
      return { ok: true, body: { getReader: () => ({
        read: async () => (sent ? { done: true }
          : (sent = true, { done: false, value: new TextEncoder().encode(built) })),
      }) } };
    }
    await new Promise((r) => setTimeout(r, 0));
    if (path.startsWith('/project/history')) return json({ history });
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

SW.store.set({
  thread: { id: 'conv_1', title: 'The desk talk', artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  threads: [],
  buildMode: 'auto',
  activeApp: { id: 'app_a' },
  attachments: [],
});

await SW.store.loadBuild();
await settle();
const drawn = SW.store.get().buildMessages
  .flatMap((m) => m.blocks || [])
  .filter((b) => b.type === 'table_candidates');

calls.length = 0;
await SW.store.chooseTableAndBuild(
  prompt, 'ds-dwh', { database: 'DWH', schema: 'MARTS', table: 'GONG__CALLS' },
  answered,
);
await settle();

const click = calls.find((c) => c.url.includes('/candidate'));
const replay = calls.find((c) => c.url.includes('/build/stream'));
console.log(JSON.stringify({
  // Every table on the card, and whether it is still answerable. A row replayed off the server
  // carries no `live`, so an old card is a record of a decision rather than a button that writes a
  // record and rebuilds an app on a page load.
  cards: drawn.map((b) => ({
    live: !!b.live, total: b.total, matched: b.matched, sourceId: b.sourceId,
    prompt: b.prompt, groups: b.groups,
  })),
  routes: calls.map((c) => c.url.replace(/^.*\/api\//, 'api/')),
  // The cards still on screen once the click has been answered. The reload the click performs is
  // what takes their buttons back: an answered card must not be answerable a second time.
  cardsAfter: SW.store.get().buildMessages
    .flatMap((m) => m.blocks || [])
    .filter((b) => b.type === 'table_candidates')
    .map((b) => ({ live: !!b.live })),
  click: click ? JSON.parse(click.body) : null,
  replay: replay ? JSON.parse(replay.body) : null,
}));
