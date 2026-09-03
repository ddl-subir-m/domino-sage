// Drives the real store through one whole Chat turn and counts the Preflights it paid for
// (ADR-0027).
//
// Source cannot answer this. Whether a turn asks again is decided by which frames came down the
// stream, and "once per stream, however many frames said so" is only visible by running one: a turn
// that failed on its tenth tool call is ONE failed turn, and a turn that stopped because the person
// pressed Stop is not a failed turn at all.
//
// Input on stdin: `{ "frames": [ ...SSE events... ] }` — the stream, in order, after the send.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { frames } = JSON.parse(fs.readFileSync(0, 'utf8'));

const frame = (ev) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`);
let healthCalls = 0;

const sandbox = {
  console, JSON, Math, Date, process, Set, Map, Promise, Array, Object, String, Number, Boolean,
  RegExp, Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout, clearTimeout,
  setInterval: () => 1, clearInterval: () => {}, Blob, ArrayBuffer, Uint8Array,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, querySelector: () => null, body: {} },
  React: { createElement: (t, p, ...c) => ({ t, p, c }), useState: () => [null, () => {}],
           useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment' },
  antd: { message: { success() {}, error() {}, info() {}, warning() {} }, Modal: { confirm() {} } },
  fetch: async (url, options) => {
    const href = String(url);
    if (href.includes('/health')) {
      healthCalls += 1;
      return { ok: true, status: 200, headers: { get: () => 'application/json' },
               json: async () => ({ problems: [] }), text: async () => '{}' };
    }
    if (href.includes('/chat/stream')) {
      let sent = false;
      return { ok: true, body: { getReader: () => ({
        read: async () => {
          if (sent) return { done: true };
          sent = true;
          return { done: false, value: frames.map(frame).reduce(
            (a, b) => new Uint8Array([...a, ...b]), new Uint8Array()) };
        },
      }) } };
    }
    return { ok: true, status: 200, headers: { get: () => 'application/json' },
             json: async () => (href.includes('/history') ? [] : {}), text: async () => '' };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'prefs.js', 'api.js', 'store.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;
SW.store.set({ thread: { id: 't1', artifacts: [] }, messages: [], scope: { id: 'p', name: 'P' } });

// Whatever the boot would have spent is not this turn's, so the count starts after the send is set
// up and before the stream is read.
healthCalls = 0;
await SW.store.sendMessage('how many rows?');
for (let i = 0; i < 40; i += 1) await new Promise((r) => setTimeout(r, 0));

console.log(JSON.stringify({ preflights: healthCalls }));
