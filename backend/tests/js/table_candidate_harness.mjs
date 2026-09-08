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
// Two runs, chosen by stdin. Without `stream` it is the replay run above: a history already on
// disk, and a click on the card it draws. With `stream` it is the live run (#209): a typed turn
// whose frames arrive, and then a re-read landing on top of them.
//
// stdin is a Build history. stdout is one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history, prompt, answered, stream } = JSON.parse(fs.readFileSync(0, 'utf8'));

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

const sse = (frames) => frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join('');

// What the replayed build streams back, once the record is written and the card is answered.
const built = sse([
  { type: 'agent', kind: 'text', text: 'Built the daily calls dashboard.' },
  { type: 'done', ok: true, decision: 'built' },
]);

// What the server would hand back if something re-read the transcript right now. Fixed for a replay
// run, because there the rows are already on disk before anything starts. In a LIVE run it changes
// under the turn — nothing before the frames, this turn's rows after them — because the server
// appends each row as it yields it, and a re-read that lands mid-turn is the whole of #209.
let served = stream ? [] : history;

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
      // The turn's own frames in a live run, and the rows they wrote become what a re-read finds.
      const body = stream ? sse(stream) : built;
      served = history;
      let sent = false;
      return { ok: true, body: { getReader: () => ({
        read: async () => (sent ? { done: true }
          : (sent = true, { done: false, value: new TextEncoder().encode(body) })),
      }) } };
    }
    await new Promise((r) => setTimeout(r, 0));
    if (path.startsWith('/project/history')) return json({ history: served });
    // A rail with the selected app on it, for the live run only. One of the seven cards draws its
    // buttons against the app selected NOW rather than against the row — a refusal names the app
    // that was selected then, and #135 will not act on it once the two disagree — so with an empty
    // rail that card is unanswerable for a reason that has nothing to do with #209.
    if (path.startsWith('/apps')) {
      return json({ items: stream ? [{ id: 'app_a', name: 'Demo app', selected: true }] : [] });
    }
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
const blank = () => ({ text: [], names: [], pickable: [], buttons: [], more: false });

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
    const label = walk(node.c, blank()).text.join('');
    out.names.push(label);
    if (type === 'Button') out.pickable.push(label);
  }
  // The same question the two lists above ask about one table name, asked of the whole card (#209):
  // every offer here draws its way forward as a `Button` and nothing else does, so an empty list is
  // a card somebody is reading rather than one they can answer. Needed because the four offers with
  // no `sourceId` have no table names to count — a reset offer that lost its buttons still draws
  // every word it drew before.
  if (type === 'Button') out.buttons.push(walk(node.c, blank()).text.join(''));
  if (cls.includes('sw-table-more')) out.more = true;
  return walk(node.c, out);
}

function draw(block) {
  const seen = walk(SW.MessageBlock({ block }), blank());
  return {
    names: seen.names, pickable: seen.pickable, buttons: seen.buttons, more: seen.more,
  };
}

SW.store.set({
  thread: { id: 'conv_1', title: 'The desk talk', artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  threads: [],
  buildMode: 'auto',
  activeApp: { id: 'app_a' },
  attachments: [],
});

// The live run (#209), which is a different question from the replay run below it: not what a click
// calls, but what is still clickable when nobody clicked anything.
//
// `live` is not a server fact. It is this tab's memory of watching a frame arrive, stamped as the
// frame lands — so the only way to test it is to watch one land and then re-read on top of it. Both
// ends of that are already covered: a card off the server has no buttons, and a card off the stream
// has them. The MIDDLE hop is invisible from either end, and it is the one that was broken.
//
// Its own program and its own line rather than more keys on the one below, because the two runs
// disagree about what the server holds: here the rows appear under the turn, the way they really do.
if (stream) {
  // Every block carrying the flag, and what it DRAWS. The flag is the store's answer and the
  // buttons are the component's, and this bug lives in the first and shows up only in the second.
  //
  // `reads` rides along because the thing being tested is a re-read landing, and a run where no
  // re-read ever happened would pass every assertion below while proving nothing at all.
  const snap = () => ({
    reads: calls.filter((c) => c.url.includes('/project/history')).length,
    blocks: SW.store.get().buildMessages
      .flatMap((m) => m.blocks || [])
      .filter((b) => b.live !== undefined)
      .map((b) => ({ type: b.type, live: !!b.live, buttons: draw(b).buttons })),
  });

  await SW.store.loadBuild();
  await settle();
  await SW.store.sendBuildPrompt(prompt);
  await settle();
  const arrived = snap();

  // The re-read. Whichever of the three fires — the 2s poll of a running turn, the app rail, a
  // route change — all of them land in `applyBuildRead`, which is where the flag was being lost.
  await SW.store.loadBuild();
  await settle();
  const reread = snap();

  // The rail moved to another conversation and back. Watching a card arrive is a memory of THIS
  // conversation, and it must not follow the reader into another one and back out again — the card
  // standing there afterwards is one being read back, exactly as it is after a page reload.
  SW.store.set({ thread: { id: 'conv_2', title: 'Somewhere else', artifacts: [] } });
  await SW.store.loadBuild();
  await settle();
  SW.store.set({ thread: { id: 'conv_1', title: 'The desk talk', artifacts: [] } });
  await SW.store.loadBuild();
  await settle();
  const afterSwitch = snap();

  // Written rather than logged, because `process.exit` below would not wait for a pipe to drain.
  fs.writeSync(1, `${JSON.stringify({ arrived, reread, afterSwitch })}\n`);
  process.exit(0);
}

await SW.store.loadBuild();
await settle();
const drawn = SW.store.get().buildMessages
  .flatMap((m) => m.blocks || [])
  .filter((b) => b.type === 'table_candidates');

calls.length = 0;
await SW.store.chooseTableAndBuild(
  prompt, 'ds-dwh', { database: 'DWH', schema: 'MARTS', table: 'GONG__CALLS' },
  // Off the drawn card rather than passed in, so the flag is followed the whole way the button
  // follows it (#206): the history row carries it, the store copies it onto the block, and the
  // click sends it. Reading it here is the only place that proves the middle step.
  answered, drawn[0] && drawn[0].bindFirst,
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
