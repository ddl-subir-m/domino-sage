// What Chat's Retry does to the failed answer it sits under (#665).
//
// The transcript comes in through the real `store.conversationMessages`, so the messages are the
// ones `historyToMessages` built from server rows, and `SW.Message` is called on each exactly as
// Chat's transcript calls it. A press goes all the way to the request and back through the real
// stream reducer: the body that reaches `/chat/stream` and the messages left on screen are the
// claim, not which store method was named.
//
// Input on stdin: `{ history, ask, frames, press }` — the Thread as the server wrote it, the
// question `ask.prompt` to send live first with the SSE frames `ask.frames` it streams (optional),
// the SSE frames the retried turn streams, and whether to press Retry on the last assistant message.
// Output: the messages before and after, and the body of every chat request sent.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history, ask = null, frames = [], press = false } = JSON.parse(fs.readFileSync(0, 'utf8'));
const streams = [...(ask ? [ask.frames] : []), frames];
const posted = [];

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url, init) {
  if (String(url).includes('/chat/stream')) {
    posted.push(JSON.parse(init.body));
    const body = (streams.shift() || []).map((f) => `data: ${JSON.stringify(f)}\n\n`).join('');
    let sent = false;
    return { ok: true, headers: { get: () => null }, body: { getReader: () => ({
      read: async () => (sent ? { done: true }
        : (sent = true, { done: false, value: new TextEncoder().encode(body) })),
    }) } };
  }
  return json({});
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, Infinity, encodeURIComponent, decodeURIComponent,
  TextEncoder, TextDecoder, URL, URLSearchParams,
  setTimeout: unrefTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {},
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
  location: { hash: '' },
  addEventListener() {}, removeEventListener() {},
  React: {
    createElement: (t, p, ...c) => ({ t, p: p || {}, c }),
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {}, useMemo: (fn) => fn(), useRef: () => ({ current: null }),
    Fragment: 'Fragment',
  },
  antd: {
    Input: Object.assign(function Input() {}, { TextArea: 'Input.TextArea' }),
    Button: 'Button', Table: 'Table', Tooltip: 'Tooltip', Tag: 'Tag', Space: 'Space',
    Modal: Object.assign(function Modal() {}, { confirm() {} }),
    message: { success() {}, error() {}, info() {}, warning() {} },
  },
  icons: new Proxy({}, { get: (_, name) => String(name) }),
  fetch: async (url, init) => {
    await new Promise((r) => setTimeout(r, 0));
    return serve(url, init);
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'prefs.js', 'store.js', 'components/message-blocks.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;

async function settle() {
  for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0));
}

function* walk(node) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) { for (const c of node) yield* walk(c); return; }
  yield node;
  yield* walk(node.c);
}

const text = (node) => [...walk(node)].flatMap((n) => n.c || [])
  .filter((c) => typeof c === 'string').join(' ');
const retryOn = (message) => [...walk(SW.Message({ message }))]
  .find((n) => (n.p || {})['aria-label'] === 'Retry');
const drawn = () => SW.store.get().messages.map((m) => {
  const tree = SW.Message({ message: m });
  return {
    role: m.role,
    superseded: !!m.superseded,
    retry: !!retryOn(m),
    text: text(tree),
    blocks: [...walk(tree)].filter((n) => n.t === SW.MessageBlock).length,
  };
});

const thread = { id: 't1', title: 'Revenue', artifacts: [] };
SW.store.set({ me: { id: 'u1' }, scope: { id: 'p', name: 'P' }, threads: [], thread, messages: [] });
SW.store.set({ messages: await SW.store.conversationMessages({ ...thread, history }) });
if (ask) {
  await SW.store.sendMessage(ask.prompt);
  await settle();
}
const before = drawn();
if (press) {
  const failed = SW.store.get().messages.filter((m) => m.role === 'assistant').pop();
  await Promise.resolve(retryOn(failed).p.onClick()).catch(() => {});
  await settle();
}

console.log(JSON.stringify({
  before,
  after: drawn(),
  posted: posted.map((b) => ({ prompt: b.prompt, retryOf: b.retryOf })),
}));
