// Hold the real store's SSE open after done, then run another turn through its late tail.
import fs from 'node:fs';
import vm from 'node:vm';
import { unrefTimeout } from './sandbox_timeout.mjs';
const mode = JSON.parse(fs.readFileSync(0, 'utf8')).mode;
const root = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const streams = [];
function stream() {
  let waiting;
  const queued = [];
  const send = (row) => {
    if (waiting) { const wake = waiting; waiting = null; wake(row); }
    else queued.push(row);
  };
  return {
    frame: (ev) => send({ done: false, value: new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`) }),
    close: () => send({ done: true }),
    fail: () => send({ error: new Error('late stream failure') }),
    response: { ok: true, body: { getReader: () => ({ read: async () => {
      const row = queued.length ? queued.shift() : await new Promise((r) => { waiting = r; });
      if (row.error) throw row.error;
      return row;
    } }) } },
  };
}
const sandbox = {
  console, JSON, Math, Date, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout: unrefTimeout, clearTimeout,
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, querySelector: () => null, body: {} },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  React: { createElement: (t, p, ...c) => ({ t, p, c }), useState: () => [null, () => {}],
    useEffect() {}, useRef: () => ({ current: null }), Fragment: 'Fragment' },
  antd: { message: { success() {}, error() {}, info() {}, warning() {} }, Modal: { confirm() {} } },
  fetch: async (url) => {
    if (String(url).includes('/chat/stream')) { const s = stream(); streams.push(s); return s.response; }
    return { ok: true, status: 200, headers: { get: () => 'application/json' },
      json: async () => (String(url).includes('/history') ? [] : {}), text: async () => '' };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const name of ['util.js', 'api.js', 'prefs.js', 'store.js']) {
  vm.runInContext(fs.readFileSync(root + name, 'utf8'), sandbox, { filename: name });
}
const store = sandbox.SW.store;
store.set({ thread: { id: 't1', artifacts: [] }, messages: [], scope: { id: 'p', name: 'P' } });
const settle = () => new Promise((r) => setTimeout(r, 0));
const snapshot = () => {
  const s = store.get();
  return JSON.parse(JSON.stringify({ busy: s.chatRunning, typing: s.typing, running: s.runningTurn,
    suggestions: s.messages.flatMap((m) => m.blocks || []).filter((b) => b.type === 'plan_suggestion').length }));
};
let firstClosed = false;
const first = store.sendMessage('hi').then(() => { firstClosed = true; });
await settle();
const whileAnswering = snapshot();
if (mode === 'early-eof' || mode === 'early-error') {
  if (mode === 'early-eof') streams[0].close(); else streams[0].fail();
  await first;
  console.log(JSON.stringify({ whileAnswering, afterClose: snapshot() }));
} else {
  streams[0].frame({ type: 'agent', kind: 'text', text: 'Hello.' });
  if (mode === 'left') store.set({ thread: { id: 't2', artifacts: [] }, messages: [], typing: null });
  streams[0].frame({ type: 'done', ok: mode !== 'failed', decision: mode === 'failed' ? 'step failed' : 'answered' });
  await settle();
  const atDone = snapshot();
  const streamStillOpen = !firstClosed;
  const second = store.sendMessage('next question');
  await settle();
  streams[1].frame({ type: 'agent', kind: 'tool', name: 'live_read_query' });
  await settle();
  const beforeTail = snapshot();
  streams[0].frame({ type: 'handoff-suggest', reason: 'classifier' });
  if (mode === 'duplicate-done') streams[0].frame({ type: 'done', ok: true, decision: 'answered' });
  await settle();
  const afterTail = snapshot();
  if (mode === 'late-error') streams[0].fail(); else streams[0].close();
  await first;
  const afterOldClose = snapshot();
  streams[1].frame({ type: 'done', ok: true, decision: 'answered' });
  await settle();
  const afterSecondDone = snapshot();
  streams[1].close();
  await second;
  console.log(JSON.stringify({ whileAnswering, atDone, streamStillOpen, beforeTail, afterTail,
    afterOldClose, afterSecondDone, afterClose: snapshot(), streams: streams.length }));
}
