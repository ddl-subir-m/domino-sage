// Drives the real store.js through the two moments #588 changed, and reports what is left on screen.
//
// `reload`: opens a Thread off the server and reports the blocks drawn, so a declined Build offer
// can be checked to leave the same note on a reload as it did on the click.
// `send`: seeds a transcript holding a live calculation offer, sends the next message, and reports
// whether the offer still has its button — once synchronously on the call, once after the turn.
//
// The send is awaited to the end and the stream reader answers `done`, so no reader or quiet-socket
// timer outlives the print; the sandbox's timers are unref'd for the same reason.
//
// stdin is `{ mode, history?, handoff?, messages? }`. stdout is one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));

const THREAD = { id: 't1', title: 'Seats', artifacts: [], handoff: spec.handoff || null };
const stream = [
  { type: 'agent', kind: 'text', text: 'Signups rose 12%.' },
  { type: 'done', ok: true, decision: 'answered' },
].map((f) => `data: ${JSON.stringify(f)}\n\n`).join('');

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

const posted = [];
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, TextEncoder, TextDecoder, URL, URLSearchParams, Blob, ArrayBuffer, Uint8Array,
  setTimeout: unrefTimeout, clearTimeout,
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
  antd: { message: { success() {}, info() {}, warning() {}, error() {} }, Modal: { confirm() {} } },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, opts) => {
    const path = String(url).replace(/^\.\/api/, '');
    if (path.endsWith('/chat/stream')) {
      posted.push(JSON.parse((opts && opts.body) || '{}'));
      let sent = false;
      return { ok: true, status: 200, body: { getReader: () => ({
        read: async () => (sent ? { done: true }
          : (sent = true, { done: false, value: new TextEncoder().encode(stream) })),
        cancel: async () => {},
      }) } };
    }
    if (path === '/threads/t1') return json({ ...THREAD, history: spec.history || [] });
    if (path.startsWith('/threads/t1/context')) return json({ items: [] });
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

const blocks = () => SW.store.get().messages.flatMap((m) => m.blocks || []);
const liveOffers = () => blocks().filter((b) => b.type === 'other_lane_offer' && b.live).length;

if (spec.mode === 'reload') {
  SW.store.set({ scope: { id: 'proj', name: 'Demo Project' }, threads: [], attachments: [] });
  await SW.store.openThread('t1');
  console.log(JSON.stringify({ types: blocks().map((b) => b.type) }));
} else if (spec.mode === 'effort') {
  // A level clicked on the judging-effort card (#606): what the turn it starts was sent.
  SW.store.set({ scope: { id: 'proj', name: 'Demo Project' }, threads: [], attachments: [] });
  await SW.store.chooseJudgingEffort('t1', spec.model, spec.level);
  console.log(JSON.stringify({ prompts: posted.map((b) => b.prompt) }));
} else {
  SW.store.set({
    thread: { ...THREAD },
    messages: spec.messages,
    scope: { id: 'proj', name: 'Demo Project' },
    threads: [],
    attachments: [],
  });
  const before = liveOffers();
  const sending = SW.store.sendMessage('and by region?');
  const onSend = liveOffers();
  await sending;
  console.log(JSON.stringify({
    before,
    onSend,
    after: liveOffers(),
    otherLive: blocks().filter((b) => b.type !== 'other_lane_offer' && b.live).length,
    grants: posted.map((b) => b.otherLaneGrant || ''),
  }));
}
