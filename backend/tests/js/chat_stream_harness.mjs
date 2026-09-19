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

// Counted, not stubbed silently: `readSSE` fires `store.refreshProblems()` once per failed
// stream (ADR-0027), and whether a given ending is worth paying that for is a fact about the
// sequence rather than the shape — reading store.js cannot answer it. The Build harness has
// counted this since it shipped; Chat could not see it at all until #435.
let healthCalls = 0;

const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout, clearTimeout,
  // Paint on demand: the batching is an optimisation, and a test that waited for real frames
  // would be testing the event loop rather than the reducer.
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, querySelector: () => null, body: {} },
  // Where the viewer's preferences live. prefs.js treats storage it cannot reach as "no answer on
  // file" and refuses every write, so without this the preference set below would silently read
  // back as its fallback and this harness would measure the wrong reader.
  localStorage: (() => {
    const backing = new Map();
    return {
      getItem: (k) => (backing.has(k) ? backing.get(k) : null),
      setItem: (k, v) => backing.set(k, String(v)),
      removeItem: (k) => backing.delete(k),
    };
  })(),
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
    if (String(url).includes('health')) healthCalls += 1;
    return { ok: true, status: 200, headers: { get: () => 'application/json' },
             json: async () => ({ problems: [] }), text: async () => '' };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// prefs.js is not optional here any more. Since #448 the reducer asks the viewer's preference
// before it puts a `data_used` block on an answer, so a sandbox without `SW.prefs` throws inside
// the stream — and the reducer catches, which turns a missing stub into a dropped block rather
// than an error anybody can read.
for (const f of ['util.js', 'api.js', 'prefs.js', 'store.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}

const SW = sandbox.SW;
SW.store.set({ thread: { id: 't1', artifacts: [] }, messages: [], scope: { id: 'p', name: 'P' } });
// Disclosure on, because this file's claims are about what a reader who asked for it sees. #448
// made it a viewer's choice with the fallback OFF, so a run that said nothing would measure a
// transcript written for somebody who wanted none of it — and a `data_used` block absent by
// request reads exactly like one the reducer dropped.
SW.store.set({ me: { id: 'u1' } });
SW.prefs.set('dataAccessShown', true);
const seen = [];
SW.store.subscribe((s) => seen.push(JSON.stringify({ messages: s.messages, typing: s.typing })));
await SW.store.sendMessage('q');

// One line per distinct repaint: "~" is text still arriving, "=" is the recorded answer.
// `typing` is what the spinner said, in order, which is the only account of a turn's slow parts.
const steps = [];
const typings = [];
for (const snap of seen) {
  const { messages, typing } = JSON.parse(snap);
  const a = messages.find((m) => m.role === 'assistant');
  const line = a ? a.blocks.map((b) => `${b.streaming ? '~' : '='}${b.value}`).join(' | ') : '';
  if (line !== steps[steps.length - 1]) steps.push(line);
  if (typing && typing !== typings[typings.length - 1]) typings.push(typing);
}
const assistant = SW.store.get().messages.find((m) => m.role === 'assistant');
console.log(JSON.stringify({ steps, typings, healthCalls,
                             final: (assistant ? assistant.blocks : []) }));
