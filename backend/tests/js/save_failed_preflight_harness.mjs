// Drives the real store through the two doors a failed save arrives by, and counts the Preflights
// each one paid for (ADR-0064).
//
// Source cannot answer this. `saveFailed` arrives on a thread payload like any other field, and
// the leave-Chat flush answers with the same dict directly; whether the store reacts is a fact
// about what runs, which is why `problem_preflight_harness.mjs` exists for a turn. What is under
// test is a nudge, not a render: nothing on screen draws `saveFailed`, and the sentence a person
// eventually reads is the server's standing Problem.
//
// Input on stdin: `{ "steps": [...] }`, each step one of
//   { "open":  <thread payload> }   — `store.openThread`, the door a Conversation comes in by
//   { "flush": <save answer> }      — `POST /api/threads/save`'s answer, the leave-Chat retry
// A step may carry `"toastThrows": true`, which makes the toast throw so a step can ask whether
// the caller survives a Preflight that went wrong AFTER the fetch landed.
//
// Output: `{ "preflights": n, "rejected": [...indexes of steps that threw at the caller] }`.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { steps } = JSON.parse(fs.readFileSync(0, 'utf8'));

let healthCalls = 0;
let nextThread = null;
let toastThrows = false;

const json = (body) => ({
  ok: true,
  status: 200,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean,
  RegExp, Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout: unrefTimeout, clearTimeout,
  setInterval: () => 1, clearInterval: () => {}, Blob, ArrayBuffer, Uint8Array,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, querySelector: () => null, body: {} },
  React: { createElement: (t, p, ...c) => ({ t, p, c }), useState: () => [null, () => {}],
           useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment' },
  antd: {
    // The one that is real, because a Preflight that turns something up ends in a toast — and a
    // toast that throws is the failure the kick's `catch` is for. `refreshProblems` handles its
    // own fetch rejection; everything after it is the caller's problem without that catch.
    message: {
      success() {}, error() {}, info() {},
      warning() { if (toastThrows) throw new Error('the toast blew up'); },
    },
    Modal: { confirm() {} },
  },
  EventSource: function () {},
  fetch: async (url) => {
    const path = String(url).split('?')[0].replace(/^\.\/api/, '');
    if (path === '/health') {
      healthCalls += 1;
      // One Problem, so the toast has something to say and the throw above is reachable.
      return json({ problems: [{ id: 'workspace-unsent-work', message: 'm', fix: 'f',
                                 owner: 'you' }] });
    }
    if (/^\/threads\/[^/]+$/.test(path)) return json(nextThread || {});
    if (path.endsWith('/history')) return json([]);
    return json({});
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'prefs.js', 'api.js', 'store.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}
const SW = sandbox.SW;
SW.store.set({ scope: { id: 'p', name: 'P' }, projects: [{ id: 'p', name: 'P' }], messages: [] });

// Whatever the boot would have spent is not any step's, so the count starts after the setup.
healthCalls = 0;
const rejected = [];
for (const [i, step] of steps.entries()) {
  toastThrows = Boolean(step.toastThrows);
  try {
    if (step.open) {
      nextThread = step.open;
      await SW.store.openThread(step.open.id);
    } else {
      // Not `await`ed through a promise the caller made: `app.js` hands the flush's answer
      // straight in and walks away, so the question is whether THAT throws.
      SW.store.noteSaveFailed(step.flush);
    }
  } catch {
    rejected.push(i);
  }
  for (let n = 0; n < 40; n += 1) await new Promise((r) => setTimeout(r, 0));
}

console.log(JSON.stringify({ preflights: healthCalls, rejected }));
