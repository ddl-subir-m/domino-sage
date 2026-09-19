// Drives the real store.js through the investigation card in CHAT: what the Thread draws for one,
// and what each of its two buttons calls (#386, ADR-0056).
//
// The table-card harness beside this one argues both halves break silently, and every word of it
// applies here. `historyToMessages` is its own chain of `ev.type === ...` branches, so a row whose
// type has no branch reaches the Thread and disappears — and this card is drawn INSTEAD of an
// answer, so a card that vanishes is a turn that produced nothing at all.
//
// The click is TWO acts, record the decision then ask the question again: drop the second and the
// answer never comes, drop `investigationAnswered` and the person's question is drawn a second time
// under a card that already has it above. And the two buttons must send DIFFERENT decisions to the
// same door — swap them and `Just answer this` grants a shell for the rest of the conversation.
// None of that throws, and the Python suite stays green.
//
// The buttons are PRESSED rather than called around: the harness renders the real component and
// invokes the `onClick` it drew, so the two words cannot be swapped between them without this
// going red. `decision` names which button by position; the labels come back in the answer, so a
// test can say which position means which word.
//
// stdin is a Thread history, a prompt, and which button to press. stdout is one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history, prompt, press } = JSON.parse(fs.readFileSync(0, 'utf8'));

const THREAD = { id: 'thr_1', title: 'The adoption question', artifacts: [], handoff: null };

// What the door writes, and what the reload after it reads back. The bar over the composer draws
// off this and not off the frame, which is the whole reason it survives a reload.
const RECORDED = { open: 'open', decline: 'declined', close: 'closed' };
let state = null;

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

// What the replayed question streams back, once the decision is on the Thread.
const answered = [
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
          : (sent = true, { done: false, value: new TextEncoder().encode(answered) })),
      }) } };
    }
    if (path === '/threads/thr_1/investigation') {
      state = RECORDED[JSON.parse(opts.body).decision] || null;
      return json({ investigation: { state } });
    }
    await new Promise((r) => setTimeout(r, 0));
    // The Thread carries its own transcript AND its own record, which is what the bar reads.
    if (path === '/threads/thr_1') {
      return json({ ...THREAD, history, context: { items: [], investigation: state ? { state } : {} } });
    }
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
  .filter((b) => b.type === 'investigation_offer');
const lines = () => SW.store.get().messages
  .flatMap((m) => m.blocks || [])
  .filter((b) => b.type === 'status')
  .map((b) => b.value);
// Every button the real card drew, in the order it drew them, with the click it carries. Children
// go in as `children`, the way React passes them: calling a component on its props alone drops
// everything nested inside it, and the walker would then certify a card with no buttons at all.
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
// Disclosure on, because this file's claims are about what a reader who asked for it sees. #448
// made `data_used` and the investigation opened/closed line a viewer's choice with the fallback
// OFF, so a run that said nothing about the preference would walk a transcript written for
// somebody who wanted none of it — and disclosure absent by request reads exactly like disclosure
// the store lost.
SW.store.set({ me: { id: 'u1' } });
SW.prefs.set('dataAccessShown', true);

await SW.store.openThread('thr_1');
await settle();
const drawn = cards();

// The card as it arrives over SSE, which is the only shape that carries buttons. The one the
// reload draws above is the same block with `live` off, and `cards[].live` reports that.
const live = buttons(SW.MessageBlock({
  block: { ...(history.find((r) => r.type === 'investigation-offer') || {}),
           type: 'investigation_offer', live: true },
}), []);

calls.length = 0;
if (press === 'close') {
  await SW.store.closeInvestigation('thr_1');
} else {
  await live[press].onClick();
}
await settle();

const click = calls.find((c) => c.url.includes('/investigation'));
const replay = calls.find((c) => c.url.includes('/chat/stream'));
console.log(JSON.stringify({
  // The card, and whether it is still answerable. A row replayed off the server carries no `live`,
  // so an old card is a record of a question rather than a button that grants a capability for the
  // rest of a conversation on a page load nobody connected it to.
  cards: drawn.map((b) => ({ live: !!b.live, prompt: b.prompt, threadId: b.threadId,
                             message: b.message })),
  // What the card offers, in the order it offers it. One primary act per card, and the other is
  // the way past it.
  buttons: live.map((b) => b.label),
  // And the card a reload draws has none of them, which is what `live` is for.
  replayedButtons: buttons(SW.MessageBlock({
    block: { ...(history.find((r) => r.type === 'investigation-offer') || {}),
             type: 'investigation_offer', live: false },
  }), []).length,
  // What the transcript says about the grant itself, which is the half a bar cannot answer.
  lines: lines(),
  routes: calls.map((c) => c.url.replace(/^.*\/api\//, 'api/')),
  cardsAfter: cards().map((b) => ({ live: !!b.live })),
  // The bar over the composer reads this. It is the Thread's record rather than a live frame, so
  // it is true on a reload and in a second tab.
  barOpen: (((SW.store.get().thread || {}).context || {}).investigation || {}).state === 'open',
  // The person asked once. The reload puts their question back on screen from the Thread; the
  // replay must not draw it a second time underneath the card.
  asked: asked(),
  click: click ? JSON.parse(click.body) : null,
  replay: replay ? JSON.parse(replay.body) : null,
}));
