// Drives the real store.js through a scripted Chat SSE turn and reports what the Thread looked
// like at every repaint. The streaming reducer is the one piece of the Workbench where reading the
// source is not enough: it decides whether the answer appears once or twice, and the failure is a
// duplicated paragraph rather than an exception.
//
// The stubs are the smallest set store.js touches on this path. React is never rendered — the
// assertions are about state.messages, not about the DOM.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const frames = JSON.parse(fs.readFileSync(0, 'utf8'));
const body = frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join('');

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout, clearTimeout,
  // Paint on demand: the batching is an optimisation, and a test that waited for real frames
  // would be testing the event loop rather than the reducer.
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, querySelector: () => null, body: {} },
  React: { createElement: (t, p, ...c) => ({ t, p, c }), useState: () => [null, () => {}],
           useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment' },
  antd: { message: { success() {}, error() {}, info() {}, warning() {} }, Modal: { confirm() {} } },
  fetch: async (url) => {
    if (String(url).includes('/chat/stream')) {
      let sent = false;
      return { ok: true, body: { getReader: () => ({
        read: async () => (sent ? { done: true }
          : (sent = true, { done: false, value: new TextEncoder().encode(body) })),
      }) } };
    }
    return { ok: true, status: 200, headers: { get: () => 'application/json' },
             json: async () => ({}), text: async () => '' };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'store.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}

const SW = sandbox.SW;
SW.store.set({ thread: { id: 't1', artifacts: [] }, messages: [], scope: { id: 'p', name: 'P' } });
const seen = [];
SW.store.subscribe((s) => seen.push(JSON.stringify(s.messages)));
await SW.store.sendMessage('q');

// One line per distinct repaint: "~" is text still arriving, "=" is the recorded answer.
const steps = [];
for (const snap of seen) {
  const a = JSON.parse(snap).find((m) => m.role === 'assistant');
  const line = a ? a.blocks.map((b) => `${b.streaming ? '~' : '='}${b.value}`).join(' | ') : '';
  if (line !== steps[steps.length - 1]) steps.push(line);
}
const assistant = SW.store.get().messages.find((m) => m.role === 'assistant');
console.log(JSON.stringify({ steps, final: (assistant ? assistant.blocks : []) }));
