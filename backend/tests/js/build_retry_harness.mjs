// Whether a failed Build turn draws Chat's Retry, and what pressing it sends (#663).
//
// The transcript comes in through a real `store.loadBuild()`, so the message under test is the one
// `buildHistoryToMessages` built from server rows, and `SW.Message` is called on it exactly as
// Build's transcript calls it. The press goes all the way to the request: what reaches
// `/project/build/stream` is the claim, not which store method was named.
//
// Input on stdin: `{ history }`, the Build log as the server writes it.
// Output: the Retry buttons per message, and the body of every build request a press sent.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { history } = JSON.parse(fs.readFileSync(0, 'utf8'));
const posted = [];

const json = (body) => ({
  ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

function serve(url, init) {
  const path = String(url).replace(/^\.\/api/, '');
  if (path.startsWith('/project/build/stream')) {
    posted.push(JSON.parse(init.body));
    return { ok: false, status: 500, statusText: 'not under test', headers: { get: () => null },
             json: async () => ({}) };
  }
  if (path.startsWith('/project/history')) return json({ history });
  if (path.startsWith('/apps')) return json({ items: [] });
  if (path.startsWith('/bindings')) return json({ bindings: [] });
  return json({});
}

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, Blob, ArrayBuffer, Uint8Array, Infinity, encodeURIComponent, decodeURIComponent,
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

SW.store.set({
  thread: { id: 'conv_1', title: 'The desk talk', artifacts: [] },
  scope: { id: 'proj', name: 'Demo Project' },
  threads: [],
  activeApp: { id: 'app_a' },
});
await SW.store.loadBuild();
await settle();
// A chip the request names, so the press can be seen carrying it.
SW.store.set({ resourceGroups: { file: [{ id: 'r1', name: 'sales.csv', path: 'sales.csv', kind: 'file' }] } });

const retryOf = (message) => [...walk(SW.Message({ message }))]
  .find((n) => (n.p || {})['aria-label'] === 'Retry');

const messages = SW.store.get().buildMessages;
const retries = messages.map((m) => ({ role: m.role, retry: !!retryOf(m) }));
// The same rows as Chat's merged read draws them: built again, so not Build's own messages.
const elsewhere = messages.map((m) => ({ ...m })).some((m) => !!retryOf(m));
const pressed = messages.map(retryOf).find(Boolean);
if (pressed) {
  await Promise.resolve(pressed.p.onClick()).catch(() => {});
  await settle();
}

console.log(JSON.stringify({
  retries,
  elsewhere,
  posted: posted.map((b) => ({ prompt: b.prompt, mentions: b.mentions, howSageWorks: b.howSageWorks })),
}));
