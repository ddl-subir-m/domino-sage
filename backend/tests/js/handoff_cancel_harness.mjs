// Drives the real store.js through Cancel on the handoff sheet, then an explicit redraft, and
// reports what the Thread was left holding and which routes were called with what (#661).
//
// stdout is one JSON line.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;

const calls = [];
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout: unrefTimeout, clearTimeout,
  Blob, ArrayBuffer, Uint8Array,
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, querySelector: () => null, body: {} },
  React: { createElement: (t, p, ...c) => ({ t, p, c }), useState: () => [null, () => {}],
           useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment' },
  antd: { message: { success() {}, error() {}, info() {}, warning() {} }, Modal: { confirm() {} } },
  fetch: async (url, opts) => {
    calls.push({ url: String(url).replace(/^.*\/api\//, 'api/'), method: opts && opts.method,
                 body: opts && opts.body ? JSON.parse(opts.body) : null });
    const json = String(url).includes('/handoff/plan')
      ? { handoff: { status: 'planned', planId: '002' }, title: 'Signal Room', apps: [] }
      : {};
    return { ok: true, status: 200, headers: { get: () => 'application/json' },
             json: async () => json, text: async () => JSON.stringify(json) };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'api.js', 'prefs.js', 'store.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}

const SW = sandbox.SW;
SW.store.set({
  thread: { id: 't1', artifacts: [],
            handoff: { status: 'planned', planId: '001', planPath: '.sage/plan.md',
                       suppressed: false } },
  messages: [],
  scope: { id: 'p', name: 'P' },
  attachments: [],
  handoffOpen: true,
  handoffDraft: { title: 'Pipeline Signal Board', apps: [] },
});

SW.store.cancelHandoffDraft();
for (let i = 0; i < 50; i += 1) await Promise.resolve();
const afterCancel = SW.store.get();
const cancelled = {
  open: !!afterCancel.handoffOpen,
  handoff: afterCancel.thread.handoff,
  offers: afterCancel.messages.filter((m) => (m.blocks || [])
    .some((b) => b.type === 'plan_suggestion')).length,
  routes: calls.splice(0),
};

await SW.store.draftHandoffPlan('t1', { redraft: true });
const redrafted = { routes: calls.splice(0).filter((c) => c.url.includes('/handoff/plan')) };

console.log(JSON.stringify({ cancelled, redrafted }));
