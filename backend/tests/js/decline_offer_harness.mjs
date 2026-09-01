// Drives the real store.js through `Not now` on a Build offer and reports what the Thread was left
// holding, and which route was called.
//
// Run rather than read, for the reason the streaming harness beside it gives: the failure this
// guards is a duplicated question in the transcript, not an exception. The person in the live run
// produced that transcript by hand — their sentence twice, once because declining the offer did
// nothing — and a fix that answers the question by re-sending it through the ordinary path would
// reproduce it faithfully.
//
// stdin is the seeded transcript. stdout is one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const seed = JSON.parse(fs.readFileSync(0, 'utf8'));
// The answer the declined question gets. Only reached when the decline route is the one called.
const body = [
  { type: 'agent', kind: 'text', text: 'Here is what that data holds.' },
  { type: 'done', ok: true, decision: 'answered' },
].map((f) => `data: ${JSON.stringify(f)}\n\n`).join('');

const calls = [];
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout, clearTimeout,
  // `api.js` type-tests every request body against these before it serialises one. Missing, the
  // first PATCH throws a ReferenceError inside the api layer's own try — which the layer then
  // reports as a failed request, so the harness sees a route that was never called.
  Blob, ArrayBuffer, Uint8Array,
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, querySelector: () => null, body: {} },
  React: { createElement: (t, p, ...c) => ({ t, p, c }), useState: () => [null, () => {}],
           useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment' },
  antd: { message: { success() {}, error() {}, info() {}, warning() {} }, Modal: { confirm() {} } },
  fetch: async (url, opts) => {
    calls.push({ url: String(url), body: opts && opts.body });
    if (String(url).includes('/handoff/decline')) {
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
SW.store.set({
  thread: { id: 't1', artifacts: [], handoff: { status: 'suggested' } },
  messages: seed,
  scope: { id: 'p', name: 'P' },
  attachments: [],
});
SW.store.dismissPlanSuggestion();
// The decline streams, and `dismissPlanSuggestion` does not await it — the callout has to come off
// the screen on the click rather than when the turn ends. Drain the microtasks it queued.
for (let i = 0; i < 50; i += 1) await Promise.resolve();

const messages = SW.store.get().messages;
console.log(JSON.stringify({
  routes: calls.map((c) => c.url.replace(/^.*\/api\//, 'api/')),
  users: messages.filter((m) => m.role === 'user')
    .map((m) => (m.blocks.find((b) => b.type === 'text') || {}).value),
  offers: messages.filter((m) => (m.blocks || []).some((b) => b.type === 'plan_suggestion')).length,
  answers: messages.filter((m) => m.role === 'assistant')
    .flatMap((m) => m.blocks.map((b) => b.value)),
  handoff: SW.store.get().thread.handoff,
}));
