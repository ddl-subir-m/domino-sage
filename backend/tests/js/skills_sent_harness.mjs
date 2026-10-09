// What a turn says about the Project skills named with @ that went to the model (#737), read off its
// `done` row: a Chat reload, a Build reload, and a Chat turn arriving live.
//
// stdin is `{ lane: 'chat' | 'build' | 'live', history, done }`. stdout is one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { lane, history = [], done = {} } = JSON.parse(fs.readFileSync(0, 'utf8'));

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

const frames = [{ type: 'agent', kind: 'text', text: 'Weighted pipeline is $1.2M.' }, done]
  .map((f) => `data: ${JSON.stringify(f)}\n\n`).join('');

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, TextEncoder, TextDecoder, URL, URLSearchParams, Blob, ArrayBuffer, Uint8Array,
  setTimeout: unrefTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (fn) => fn(),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  document: { addEventListener() {}, removeEventListener() {}, querySelector: () => null, body: {} },
  React: {
    createElement: (t, p, ...c) => ({ t, p, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment',
  },
  antd: { message: { success() {}, info() {}, warning() {}, error() {} }, Modal: { confirm() {} } },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url) => {
    const path = String(url).replace(/^\.\/api/, '');
    if (path.includes('/chat/stream')) {
      let sent = false;
      return { ok: true, body: { getReader: () => ({
        read: async () => (sent ? { done: true }
          : (sent = true, { done: false, value: new TextEncoder().encode(frames) })),
      }) } };
    }
    await new Promise((r) => setTimeout(r, 0));
    if (path === '/threads/thr_1') return json({ id: 'thr_1', title: 'T', artifacts: [], history });
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
for (const f of ['util.js', 'api.js', 'store.js', 'prefs.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

async function settle() {
  for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0));
}

SW.store.set({ scope: { id: 'proj', name: 'Demo Project' }, threads: [], attachments: [],
               me: { id: 'u1' } });
let messages;
if (lane === 'build') {
  SW.store.set({ thread: { id: 'conv_1', title: 'T', artifacts: [] }, activeApp: { id: 'app_a' } });
  await SW.store.loadBuild();
  await settle();
  messages = SW.store.get().buildMessages;
} else if (lane === 'live') {
  SW.store.set({ thread: { id: 'thr_1', artifacts: [] }, messages: [] });
  await SW.store.sendMessage('Chart pipeline and follow @revops-conventions');
  await settle();
  messages = SW.store.get().messages;
} else {
  await SW.store.openThread('thr_1');
  await settle();
  messages = SW.store.get().messages;
}
const statuses = messages.flatMap((m) => m.blocks || [])
  .filter((b) => b.type === 'status' && b.fromEvent === 'skills')
  .map((b) => ({ value: b.value, ok: b.ok, warn: !!b.warn }));
console.log(JSON.stringify({ statuses }));
