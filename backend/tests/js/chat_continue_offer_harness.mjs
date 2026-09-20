// Drives the real store.js through the card the 600s ceiling draws, and through its one button
// (#454). What the Thread makes of the row, and what pressing Continue actually sends.
//
// Both halves break silently, which is why this is driven rather than read. `historyToMessages` is
// a chain of `ev.type === ...` branches, so a row whose type has no branch reaches the Thread and
// disappears — and this row is the ONLY way back into a turn that spent ten minutes and answered
// nothing, so a card that vanishes is ten minutes the person cannot reach. The click is the other
// half: Continue is an ordinary turn on the same Thread, so it must open that Thread first and
// send the ORIGINAL question. Post it without re-reading and a click after the person has moved
// conversations lands the question in the one they moved to, with `echo` off, where they never
// see it — the failure the three cards beside this one are all commented against.
//
// The button is PRESSED rather than called around: the harness renders the real component and
// invokes the `onClick` it drew. A card that renders no button, or draws one on a replayed row,
// cannot pass by the component being right in isolation.
//
// stdin is a Thread history. stdout is one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history } = JSON.parse(fs.readFileSync(0, 'utf8'));

const THREAD = { id: 'thr_1', title: 'The adoption question', artifacts: [], handoff: null };

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// What the resumed turn streams back. It is an ordinary turn: a full clock, no grant, and the
// findings it reads are named into its prompt by the server, not by anything here.
const resumed = [
  { type: 'agent', kind: 'text', text: 'Four accounts score above 0.8.' },
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
  // Named rather than stubbed, so the rendered tree says what each node IS — a `Button` somebody
  // can press against a `span` they can only read. That difference is the whole of the `live` rule.
  antd: {
    message: { success() {}, info() {}, warning() {}, error() {} },
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space',
    Input: 'Input', Spin: 'Spin', Alert: 'Alert', Collapse: 'Collapse', Popconfirm: 'Popconfirm',
    Switch: 'Switch', Select: 'Select', Modal: 'Modal', Segmented: 'Segmented', Empty: 'Empty',
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, opts) => {
    calls.push({ url: String(url), body: opts && opts.body });
    const path = String(url).replace(/^\.\/api/, '');
    if (path.startsWith('/threads/thr_1/chat/stream')) {
      let sent = false;
      return { ok: true, body: { getReader: () => ({
        read: async () => (sent ? { done: true }
          : (sent = true, { done: false, value: new TextEncoder().encode(resumed) })),
      }) } };
    }
    await new Promise((r) => setTimeout(r, 0));
    if (path === '/threads/thr_1') return json({ ...THREAD, history, context: { items: [] } });
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

const cards = () => SW.store.get().messages
  .flatMap((m) => m.blocks || [])
  .filter((b) => b.type === 'continue_offer');

// Every button the real card drew, with the click it carries. Children go in as `children`, the
// way React passes them: calling a component on its props alone drops everything nested inside it,
// and the walker would then certify a card with no buttons at all.
function buttons(node, out) {
  if (node === null || node === undefined || node === false) return out;
  if (Array.isArray(node)) { node.forEach((n) => buttons(n, out)); return out; }
  if (typeof node !== 'object') return out;
  if (typeof node.t === 'function') return buttons(node.t({ ...(node.p || {}), children: node.c }), out);
  if (node.t === 'Button') out.push({ label: label(node.c), onClick: node.p && node.p.onClick });
  return buttons(node.c, out);
}
function label(node) {
  if (node === null || node === undefined || node === false) return '';
  if (Array.isArray(node)) return node.map(label).join('');
  if (typeof node !== 'object') return String(node);
  return label(node.c);
}

const asked = () => SW.store.get().messages
  .filter((m) => m.role === 'user')
  .flatMap((m) => (m.blocks || []).map((b) => b.value));

SW.store.set({ scope: { id: 'proj', name: 'Demo Project' }, threads: [], attachments: [] });
SW.store.set({ me: { id: 'u1' } });
SW.prefs.set('dataAccessShown', true);

await SW.store.openThread('thr_1');
await settle();
const drawn = cards();

const row = history.find((r) => r.type === 'continue-offer') || {};
// The card as it arrives over SSE, which is the only shape that carries a button. The one the
// reload draws above is the same block with `live` off, and `cards[].live` reports that.
const live = buttons(SW.MessageBlock({ block: { ...row, type: 'continue_offer', live: true } }), []);
const replayed = buttons(
  SW.MessageBlock({ block: { ...row, type: 'continue_offer', live: false } }), []);

calls.length = 0;
if (live.length) await live[0].onClick();
await settle();

const replay = calls.find((c) => c.url.includes('/chat/stream'));
console.log(JSON.stringify({
  cards: drawn.map((b) => ({ live: !!b.live, prompt: b.prompt, threadId: b.threadId,
                             message: b.message })),
  buttons: live.map((b) => b.label),
  // A replayed row is a record of a ceiling, not a button that starts ten minutes of work on a
  // page load nobody connected to it.
  replayedButtons: replayed.length,
  // The Thread was re-read before the question was posted, so the replay cannot land in a
  // conversation the person moved to after the ceiling drew this card.
  routes: calls.map((c) => c.url.replace(/^.*\/api\//, 'api/')),
  // The person asked once. The reload puts their question back on screen from the Thread, and
  // Continue must not draw it a second time underneath the card that offered to pick it up.
  asked: asked(),
  replay: replay ? JSON.parse(replay.body) : null,
}));
