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
  // Named rather than stubbed with objects, so the rendered tree says what each node IS: a table
  // name drawn as a `Button` is one somebody can pick, and the same name drawn as a `span` is one
  // they can only read. That difference is the whole of #186's rule and it exists nowhere else.
  antd: {
    message: { success() {}, info() {}, warning() {}, error() {} },
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space',
    Input: 'Input', Spin: 'Spin',
  },
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
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'components/message-blocks.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

async function settle() {
  for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0));
}

// What the card actually DRAWS, which is a different question from what the block holds.
//
// The block is the store's answer and the tree is the component's, and #186 put a second render
// path between them — the card that fills in while the warehouse is read. A card can hold every
// name a search found and draw none of them, which is exactly what a `total` assertion cannot see.
//
// `React.createElement` is mocked to a plain `{ t, p, c }` node, so this walks the tree and calls
// any node whose type is a function — a thirty-line renderer, which is what it takes to ask "is
// this name a button or a word" without a DOM.
function walk(node, out) {
  if (node === null || node === undefined || node === false) return out;
  if (Array.isArray(node)) { node.forEach((n) => walk(n, out)); return out; }
  if (typeof node !== 'object') { out.text.push(String(node)); return out; }
  const type = node.t;
  // Children go in as `children`, the way React passes them. Calling a component with props alone
  // would drop everything nested inside it — and a table name rendered through a wrapper would
  // then leave `names` and `pickable` both empty, which reads as "no name is answerable" and is
  // how this walker would come to certify a card that draws nothing.
  if (typeof type === 'function') {
    return walk(type({ ...(node.p || {}), children: node.c }), out);
  }
  const cls = String((node.p && node.p.className) || '');
  if (cls.includes('sw-table-pick')) {
    // Every name the card shows, and whether this one is answerable. A `Button` writes the record
    // and starts a build; a `span` is a name somebody is reading while the search finishes.
    const label = walk(node.c, { text: [], names: [], pickable: [], more: false }).text.join('');
    out.names.push(label);
    if (type === 'Button') out.pickable.push(label);
  }
  if (cls.includes('sw-table-more')) out.more = true;
  return walk(node.c, out);
}

function draw(block) {
  const seen = walk(SW.MessageBlock({ block }), {
    text: [], names: [], pickable: [], more: false });
  return { names: seen.names, pickable: seen.pickable, more: seen.more };
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
  // `searching` says which of the two cards this is — the one filling in while the warehouse is
  // read, or the settled one somebody can answer (#186). The streaming frames replace each other in
  // place, so a run that reported several cards would be drawing one per database.
  cards: drawn.map((b) => ({
    live: !!b.live, total: b.total, matched: b.matched, sourceId: b.sourceId,
    prompt: b.prompt, groups: b.groups, searching: !!b.searching, drawn: draw(b),
  })),
  // Assistant turns holding no block at all. Retiring a card can empty the message it was in, and
  // an empty assistant message renders as a turn that said nothing (#186).
  emptyMessages: SW.store.get().buildMessages
    .filter((m) => m.role === 'assistant' && !(m.blocks || []).length).length,
  routes: calls.map((c) => c.url.replace(/^.*\/api\//, 'api/')),
  // The cards still on screen once the click has been answered. The reload the click performs is
  // what takes their buttons back: an answered card must not be answerable a second time.
  cardsAfter: SW.store.get().buildMessages
    .flatMap((m) => m.blocks || [])
    .filter((b) => b.type === 'table_candidates')
    .map((b) => ({ live: !!b.live })),
  // What the transcript says the person did. Drawn by the store the moment the click lands and
  // never streamed back, so it is the only sentence they see while the turn runs (#208).
  bubbles: SW.store.get().buildMessages.filter((m) => m.role === 'user')
    .map((m) => (m.blocks || []).map((b) => b.value || '').join('')),
  click: click ? JSON.parse(click.body) : null,
  replay: replay ? JSON.parse(replay.body) : null,
}));
