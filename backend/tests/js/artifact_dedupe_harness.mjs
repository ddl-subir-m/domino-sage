// How many cards one Artifact gets, when the server names it twice in a single Chat stream.
//
// `_chat_stream` sends the turn's artifact list as an `artifacts` frame AND again on the `done`
// that closes the turn (service.py: `done["artifacts"] = artifacts`). The live reducer is the only
// thing that keeps that from being two cards, and it fails silently in one direction: a second card
// rather than an exception. `chat_stream_harness.mjs` drives the same reducer but answers every
// file read with `{}`, so a table there never carries the title its OWN JSON gives it — which is
// the case that broke. This harness serves file bodies, so the block's title and the manifest row's
// title can differ the way they do in a real Thread.
//
// Input on stdin: `{ "frames": [...], "files": { "<path>": <json body> } }`.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { frames, files = {} } = JSON.parse(fs.readFileSync(0, 'utf8'));
const body = frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join('');

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout, clearTimeout,
  encodeURIComponent, decodeURIComponent,
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, querySelector: () => null, body: {} },
  React: { createElement: (t, p, ...c) => ({ t, p, c }), useState: () => [null, () => {}],
           useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment' },
  antd: { message: { success() {}, error() {}, info() {}, warning() {} }, Modal: { confirm() {} } },
  fetch: async (url) => {
    const s = String(url);
    if (s.includes('/chat/stream')) {
      let sent = false;
      return { ok: true, body: { getReader: () => ({
        read: async () => (sent ? { done: true }
          : (sent = true, { done: false, value: new TextEncoder().encode(body) })),
      }) } };
    }
    const m = s.match(/\/project\/file\?path=(.+)$/);
    if (m) {
      const content = JSON.stringify(files[decodeURIComponent(m[1])] ?? {});
      return { ok: true, status: 200, headers: { get: () => 'application/json' },
               json: async () => ({ content }), text: async () => content };
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
await SW.store.sendMessage('q');

const assistant = SW.store.get().messages.find((m) => m.role === 'assistant');
const blocks = (assistant ? assistant.blocks : [])
  .filter((b) => b.type === 'image' || b.type === 'table' || b.type === 'file');
// One entry per card, in the order the Thread shows them. The count IS the claim.
console.log(JSON.stringify(blocks.map((b) => ({ type: b.type, title: b.title, path: b.path }))));
