// Drives the real store.js through a Data Source card: what the transcript draws for one, and what
// each of its buttons calls.
//
// Run rather than read, for the reason the table harness beside it is: both halves break silently.
// `buildHistoryToMessages` is a chain of `ev.type === ...` branches, and a row whose type has no
// branch reaches the transcript and disappears — which would make this card a turn that asked
// nothing and answered nothing. And the click is TWO acts, record the Binding then send the request
// the person already made: drop the second and the question they answered bought them nothing, drop
// `chosenSource` from it and the search runs against whatever the prose happened to name. Neither
// throws, and the Python suite stays green.
//
// stdin is a Build history. stdout is one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history, prompt } = JSON.parse(fs.readFileSync(0, 'utf8'));

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// What the replayed turn streams back: the table card, which is where picking a store leads.
const built = [
  { type: 'table-candidates', prompt, message: 'Pick the Table.', sourceId: 'ds-dwh',
    sourceName: 'Snowflake-Data-Warehouse', groups: [], allGroups: [], total: 0, matched: 0 },
  { type: 'done', ok: false, decision: 'table candidates' },
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
    calls.push({ url: String(url), method: (opts && opts.method) || 'GET', body: opts && opts.body });
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
  .filter((b) => b.type === 'source_candidates');

// Answered off the DRAWN card rather than off the input, because that is what the button does:
// the gates this turn was already past reach the click through the transcript, and a card that
// dropped them on the way would look identical here.
const answered = drawn[0] && drawn[0].answered;

calls.length = 0;
await SW.store.chooseSourceAndSearch(prompt, 'ds-dwh', 'Snowflake-Data-Warehouse', answered);
await settle();
const routes = calls.map((c) => `${c.method} ${c.url.replace(/^.*\/api\//, 'api/')}`);
const bind = calls.find((c) => c.method === 'POST' && /\/bindings$/.test(c.url));
const replay = calls.find((c) => c.url.includes('/build/stream'));

// Read before the second act below, which reloads the transcript out from under them: what the
// screen holds once the click has been answered is a fact about the click.
const cardsAfter = SW.store.get().buildMessages
  .flatMap((m) => m.blocks || [])
  .filter((b) => b.type === 'source_candidates')
  .map((b) => ({ live: !!b.live }));
const bubbles = SW.store.get().buildMessages.filter((m) => m.role === 'user')
  .map((m) => (m.blocks || []).map((b) => b.value || '').join(''));

// The other button, from a clean slate: the same card answered the other way.
calls.length = 0;
await SW.store.buildWithoutSource(prompt, answered);
await settle();
const without = calls.find((c) => c.url.includes('/build/stream'));

console.log(JSON.stringify({
  // Every store on the card, and whether it is still answerable. A row replayed off the server
  // carries no `live`, so an old card is a record of a decision rather than a button that records
  // a Binding and rebuilds an app on a page load.
  cards: drawn.map((b) => ({
    live: !!b.live, prompt: b.prompt, sources: b.sources, answered: b.answered, named: b.named,
  })),
  routes,
  // The cards still on screen once the click has been answered. The reload the click performs is
  // what takes their buttons back: an answered card must not be answerable a second time.
  cardsAfter,
  // What the transcript says the person did, which is neither the request again nor "Build it."
  bubbles,
  bind: bind ? JSON.parse(bind.body) : null,
  replay: replay ? JSON.parse(replay.body) : null,
  withoutOne: without ? JSON.parse(without.body) : null,
}));
