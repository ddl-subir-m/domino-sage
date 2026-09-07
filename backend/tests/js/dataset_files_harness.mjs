// Drives the real store.js through a Dataset card, in Build and in Chat (#196, ADR-0039): what the
// transcript draws for one, what the card renders, and what a click on it calls.
//
// Run rather than read, because both halves break silently. `buildHistoryToMessages` and
// `historyToMessages` are two separate chains of `ev.type === ...` branches — the Build harness
// proves nothing about the Chat one — and a turn event no branch reaches disappears into the
// transcript, which is how a card becomes a turn that asked nothing and answered nothing.
//
// And the click is TWO acts, deliberately: it writes the record — an Attachment in Build, a
// `dsfile:` chip in Chat — and then sends the request the person already made. Drop the second and
// the dashboard is never built. Drop `skipDatasetGate` from it and the turn hands back the same
// card it was answering, forever. Neither shows up in Python, because neither crosses it.
//
// stdin is `{mode, history, prompt, answered, click}`. stdout is one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { mode, history, prompt, answered, click: picked } = JSON.parse(fs.readFileSync(0, 'utf8'));
const chat = mode === 'chat';

const THREAD = { id: 'thr_1', title: 'The calls question', artifacts: [], handoff: null };

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// What the replayed request streams back, once the record is written and the card is answered.
const ran = [
  { type: 'agent', kind: 'text', text: 'Built the daily summary.' },
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
  // Named rather than stubbed objects, so the rendered tree says what each node IS: a row drawn as
  // a `Button` is one somebody can pick, and the same row drawn as a `span` is one they can only
  // read. That difference is the whole of the `live` rule.
  antd: {
    message: { success() {}, info() {}, warning() {}, error() {} },
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space',
    Input: 'Input', Spin: 'Spin',
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, opts) => {
    calls.push({ url: String(url), body: opts && opts.body });
    const path = String(url).replace(/^\.\/api/, '');
    const stream = (p) => p.startsWith('/project/build/stream') || p.startsWith('/threads/thr_1/chat/stream');
    if (stream(path)) {
      let sent = false;
      return { ok: true, body: { getReader: () => ({
        read: async () => (sent ? { done: true }
          : (sent = true, { done: false, value: new TextEncoder().encode(ran) })),
      }) } };
    }
    await new Promise((r) => setTimeout(r, 0));
    if (path === '/threads/thr_1') return json({ ...THREAD, history });
    if (path.startsWith('/project/history')) return json({ history });
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
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js', 'components/message-blocks.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

async function settle() {
  for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0));
}

// What the card actually DRAWS, which is a different question from what the block holds. A card can
// carry every row the listing found and render none of them, and `total` cannot see the difference.
function walk(node, out) {
  if (node === null || node === undefined || node === false) return out;
  if (Array.isArray(node)) { node.forEach((n) => walk(n, out)); return out; }
  if (typeof node !== 'object') { out.text.push(String(node)); return out; }
  const type = node.t;
  // Children go in as `children`, the way React passes them: calling a component on its props alone
  // would drop everything nested inside it, and the walker would then certify that a card holding
  // rows draws none of them.
  if (typeof type === 'function') return walk(type({ ...(node.p || {}), children: node.c }), out);
  const cls = String((node.p && node.p.className) || '');
  if (cls.includes('sw-dataset-pick')) {
    const label = walk(node.c, { text: [], rows: [], pickable: [], more: false, past: false }).text;
    out.rows.push(label.join(''));
    if (type === 'Button') out.pickable.push(label.join(''));
  } else if (cls.includes('sw-dataset-more')) {
    out.more = true;
  } else if (type === 'Button' && !cls) {
    // The way past the card, which is the only unclassed button this component draws.
    out.past = true;
  }
  return walk(node.c, out);
}

const draw = (block) => walk(SW.MessageBlock({ block }),
  { text: [], rows: [], pickable: [], more: false, past: false });

const blocks = () => (chat ? SW.store.get().messages : SW.store.get().buildMessages)
  .flatMap((m) => m.blocks || [])
  .filter((b) => b.type === 'dataset_files');

SW.store.set({
  thread: chat ? null : { id: 'thr_1', title: 'The desk talk', artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  threads: [], buildMode: 'auto', activeApp: { id: 'app_a' }, attachments: [],
});

if (chat) {
  await SW.store.openThread('thr_1');
} else {
  await SW.store.loadBuild();
}
await settle();
const drawn = blocks().map((b) => ({
  live: !!b.live,
  total: b.total,
  matched: b.matched,
  datasetId: b.datasetId,
  threadId: b.threadId || '',
  prompt: b.prompt,
  rows: b.rows,
  drawn: (({ rows, pickable, more, past }) => ({ rows, pickable, more, past }))(draw(b)),
}));

calls.length = 0;
if (picked.kind === 'past') {
  // The way past the card, which is the one act that has to name the Dataset: the request it sends
  // answers this turn, and `datasetDismissed` is what answers the app.
  if (chat) await SW.store.askWithoutAttaching(prompt, 'thr_1', 'ds_revenue');
  else await SW.store.buildWithoutAttaching(prompt, 'ds_revenue', answered);
} else if (chat) {
  await SW.store.pinDatasetFileAndAsk(prompt, 'thr_1', 'ds_revenue', picked.path);
} else if (picked.kind === 'folder') {
  await SW.store.attachFolderAndBuild(prompt, 'ds_revenue', picked.path, answered);
} else {
  await SW.store.attachFileAndBuild(prompt, 'ds_revenue', picked.path, answered);
}
await settle();

const record = calls.find((c) => c.url.includes('/files/attach') || c.url.includes('/context/dataset/'));
const replay = calls.find((c) => c.url.includes('/build/stream') || c.url.includes('/chat/stream'));
console.log(JSON.stringify({
  cards: drawn,
  routes: calls.map((c) => c.url.replace(/^.*\/api\//, 'api/')),
  cardsAfter: blocks().map((b) => ({ live: !!b.live })),
  record: record ? { url: record.url.replace(/^.*\/api\//, 'api/'), body: JSON.parse(record.body) } : null,
  replay: replay ? JSON.parse(replay.body) : null,
}));
