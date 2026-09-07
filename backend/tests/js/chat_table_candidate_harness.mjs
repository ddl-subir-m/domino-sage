// Drives the real store.js through a table candidate card in CHAT: what the Thread draws for one,
// and what a click on it calls (#188).
//
// The Build harness beside this one argues both halves break silently, and every word of it applies
// here to a second set of code. `historyToMessages` is its own chain of `ev.type === ...` branches —
// a separate chain from `buildHistoryToMessages`, so the Build harness proves nothing about it — and
// a row whose type has no branch reaches the Thread and disappears, which turns the card into a turn
// that answered nothing. And the click is TWO acts, write the record then ask the question again:
// drop the second and the answer never comes, drop `skipTableGate` and the turn hands back the same
// card it was answering, drop `echo: false` and the person's question is drawn a second time under
// a card that already has it above. None of that throws, and the Python suite stays green.
//
// stdin is a Thread history. stdout is one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history, prompt } = JSON.parse(fs.readFileSync(0, 'utf8'));

const THREAD = { id: 'thr_1', title: 'The gong question', artifacts: [], handoff: null };

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// What the replayed question streams back, once the table is on the Thread.
const answered = [
  { type: 'agent', kind: 'text', text: 'Calls peaked on Tuesday.' },
  { type: 'done', ok: true, decision: 'answered' },
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
    if (path.startsWith('/threads/thr_1/chat/stream')) {
      let sent = false;
      return { ok: true, body: { getReader: () => ({
        read: async () => (sent ? { done: true }
          : (sent = true, { done: false, value: new TextEncoder().encode(answered) })),
      }) } };
    }
    await new Promise((r) => setTimeout(r, 0));
    // The Thread carries its own transcript, which is what the split view renders from.
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

const cards = () => SW.store.get().messages
  .flatMap((m) => m.blocks || [])
  .filter((b) => b.type === 'table_candidates');
const asked = () => SW.store.get().messages
  .filter((m) => m.role === 'user')
  .flatMap((m) => (m.blocks || []).map((b) => b.value));

SW.store.set({ scope: { id: 'proj', name: 'Demo Project' }, threads: [], attachments: [] });

await SW.store.openThread('thr_1');
await settle();
const drawn = cards();

calls.length = 0;
await SW.store.chooseTableAndAsk(
  prompt, 'thr_1', 'ds-dwh', { database: 'DWH', schema: 'MARTS', table: 'GONG__CALLS' },
);
await settle();

const click = calls.find((c) => c.url.includes('/candidate'));
const replay = calls.find((c) => c.url.includes('/chat/stream'));
console.log(JSON.stringify({
  // Every table on the card, and whether it is still answerable. A row replayed off the server
  // carries no `live`, so an old card is a record of a decision rather than a button that writes a
  // record and runs a turn on a page load nobody connected it to.
  cards: drawn.map((b) => ({
    live: !!b.live, total: b.total, matched: b.matched, sourceId: b.sourceId,
    threadId: b.threadId, prompt: b.prompt, groups: b.groups,
  })),
  routes: calls.map((c) => c.url.replace(/^.*\/api\//, 'api/')),
  cardsAfter: cards().map((b) => ({ live: !!b.live })),
  // The person asked once. The reload puts their question back on screen from the Thread; the
  // replay must not draw it a second time underneath the card.
  asked: asked(),
  click: click ? JSON.parse(click.body) : null,
  replay: replay ? JSON.parse(replay.body) : null,
}));
