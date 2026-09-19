// Drives the real store.js through a QUEUED Chat turn and reports what the screen looked like
// while it waited (#79). Reading the source is not enough here for the same reason it was not
// enough for the streaming reducer: the interesting states are the ones between two SSE frames —
// a queued row on screen with nothing in the transcript, and a question handed back to the composer
// after the bubble for it had already been drawn.
//
// Input on stdin:
// `{ "mode": "queued" | "cancelled" | "cancel-declined" | "cancel-unreadable" | "cancel-twice"
//    | "context-changed" | "two-in-flight" }`. The first six script the same shape — the server
// accepts the turn, holds it, and then one of the six things happens to it — and hold the stream
// open between the `pending` frame and the rest, because that pause IS the feature. The seventh
// sends twice and lets the FIRST finish first, which is the one arrangement where a tab's own
// bookkeeping can lie about whether it is still busy.
//
// The stubs are the smallest set store.js touches on this path. React is never rendered.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { mode } = JSON.parse(fs.readFileSync(0, 'utf8'));

const PENDING = {
  type: 'pending',
  ticket: 'turn_abc',
  prompt: 'how many rows?',
  message: 'Waiting on the turn that is running. Nothing has run yet — you can cancel.',
};
// The two Cancels that do not cancel (#385): the one the server declined, and the one whose answer
// came back unreadable. Both script a turn that RUNS — the `running` row the grant streams, and
// then the answer — because in both the queue had already let this turn go when the POST landed.
// They differ only in what the cancel reply says, which is the stub further down and not these
// frames, and sharing the array is what keeps that true.
const GRANTED_ANYWAY = [
  { type: 'running', ticket: 'turn_abc' },
  { type: 'delta', text: 'Six million rows.', final: true },
  { type: 'agent', kind: 'text', text: 'Six million rows.' },
  { type: 'done', ok: true, decision: 'answered' },
];
const REST = {
  queued: [
    { type: 'delta', text: 'Six million rows.', final: true },
    { type: 'agent', kind: 'text', text: 'Six million rows.' },
    { type: 'done', ok: true, decision: 'answered' },
  ],
  cancelled: [{ type: 'done', ok: false, decision: 'cancelled' }],
  'cancel-declined': GRANTED_ANYWAY,
  'cancel-unreadable': GRANTED_ANYWAY,
  // Two presses on the row one WORKING cancel leaves up (#385). Same frames as `cancelled`,
  // because that is what the first press earns; the question is what the second one does.
  'cancel-twice': [{ type: 'done', ok: false, decision: 'cancelled' }],
  'context-changed': [
    { type: 'error', contextChanged: true, prompt: 'how many rows?',
      message: 'Your attachments changed, so this didn\'t run. The text is back in the box — send it again.' },
    { type: 'done', ok: false, decision: 'context changed' },
  ],
  'two-in-flight': [{ type: 'done', ok: true, decision: 'answered' }],
}[mode];

const frame = (ev) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`);

// Each stream stops after its `pending` frame until the harness lets that one go. Every assertion
// about a WAITING turn is taken inside that pause, and holding the streams SEPARATELY is what lets
// `two-in-flight` finish the first turn while the second is still open.
const gates = [];
const letGo = (i = 0) => gates[i] && gates[i]();

const posted = [];
// Every sentence the store said out loud, in order. A press whose only outcome is a message has
// nothing else to assert on: no row comes down for it and no frame arrives on account of it.
const said = [];
// What the cancel route actually answers when it answers at all (`app.py:4142` returns
// `{"cancelled": <bool>}` for every call). `False` is the ticket no longer being on `_waiting` by
// the time `_TurnQueue.cancel` scanned it. The stub used to answer `{}` here, which is a shape the
// route cannot produce and the one shape that hides the verdict from the client. `cancel-unreadable`
// does not use this: its reply never parses, which is a different thing from a verdict of `false`.
const CANCEL_VERDICT = mode !== 'cancel-declined';
let streams = 0;
const sandbox = {
  console, JSON, Math, Date, process, Set, Map, Promise, Array, Object, String, Number, Boolean, RegExp,
  Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout, clearTimeout, setInterval,
  clearInterval, Blob, ArrayBuffer, Uint8Array,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, querySelector: () => null, body: {} },
  React: { createElement: (t, p, ...c) => ({ t, p, c }), useState: () => [null, () => {}],
           useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment' },
  antd: { message: {
    success: (text) => said.push(['success', text]),
    error: (text) => said.push(['error', text]),
    info: (text) => said.push(['info', text]),
    warning: (text) => said.push(['warning', text]),
  }, Modal: { confirm() {} } },
  fetch: async (url, options) => {
    const href = String(url);
    if (options && options.method === 'POST') posted.push(href);
    if (href.includes('/chat/stream')) {
      const mine = streams++;
      const gate = new Promise((resolve) => { gates[mine] = resolve; });
      let sent = 0;
      return { ok: true, body: { getReader: () => ({
        read: async () => {
          if (sent === 0) { sent = 1; return { done: false, value: frame(PENDING) }; }
          if (sent === 1) {
            sent = 2;
            await gate;
            return { done: false, value: REST.map(frame).reduce(
              (a, b) => new Uint8Array([...a, ...b]), new Uint8Array()) };
          }
          return { done: true };
        },
      }) } };
    }
    // The cancel route is the one read with a verdict on it, so it is the one read this stub
    // cannot answer `{}` to: throwing the verdict away here leaves the client's reading of it
    // untested whichever way the client reads it.
    if (href.includes('/turn/cancel')) {
      // A 200 that is not JSON — the proxy answering instead of the app, which `api.js:45` hands
      // on as `{}`. Neither a verdict nor an error, and the only shape that tells `=== false`
      // apart from a falsy test. Reachable on a freshly opened Workbench; see api.js:52.
      if (mode === 'cancel-unreadable') {
        return { ok: true, status: 200, headers: { get: () => 'text/html' },
                 json: async () => { throw new Error('Unexpected token <'); },
                 text: async () => '<html>502</html>' };
      }
      return { ok: true, status: 200, headers: { get: () => 'application/json' },
               json: async () => ({ cancelled: CANCEL_VERDICT }), text: async () => '' };
    }
    // Every other read answers empty, which is also what makes the re-read after a turn that never
    // ran observable: the server has no record of the question, so the bubble goes.
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

const settle = () => new Promise((r) => setTimeout(r, 0));
const turn = SW.store.sendMessage('how many rows?');

if (mode === 'two-in-flight') {
  // Both sends open, then the FIRST one finishes while the second is still going. That is the
  // arrangement a per-turn flag gets wrong and a per-tab one gets right, and it is not reachable
  // by finishing them in order.
  const second = SW.store.sendMessage('and by desk?');
  for (let i = 0; i < 200 && SW.store.get().queuedTurns.length < 2; i += 1) await settle();
  letGo(0);
  await turn;
  // Read out now, not later: `store.get()` hands back the live state object, so anything held on
  // to across the `await` below would report the end of the turn rather than the middle of it.
  const busyWhileTheSecondIsOpen = SW.store.get().chatRunning;
  letGo(1);
  await second;
  console.log(JSON.stringify({
    // True while a turn this tab asked for is still open, whatever the earlier one did on its way
    // out — and false once the last of them is done.
    busyWhileTheSecondIsOpen,
    busyAfterBoth: SW.store.get().chatRunning,
    // Two streams opened means the second question actually reached the server rather than being
    // dropped by the composer, which is what it used to do while a turn was running.
    streamsOpened: streams,
  }));
  process.exit(0);
}

// Wait for the queued row rather than for a tick count: the pause is real, and a fixed number of
// microtasks would be a test of the scheduler.
for (let i = 0; i < 200 && SW.store.get().queuedTurns.length === 0; i += 1) await settle();

const waiting = SW.store.get();
const whileWaiting = {
  queued: waiting.queuedTurns.map((q) => ({ ticket: q.ticket, text: q.text, message: q.message })),
  // The composer is open behind the queued row: a second question is the whole point.
  chatRunning: waiting.chatRunning,
  // Nothing in the transcript beyond the question the send drew optimistically.
  roles: waiting.messages.map((m) => m.role),
};

if (mode === 'cancelled' || mode === 'cancel-declined' || mode === 'cancel-unreadable') {
  await SW.store.cancelQueuedTurn(waiting.queuedTurns[0].ticket);
}
if (mode === 'cancel-twice') {
  // NOT awaited in turn: the second press is the one that lands while the first is still in flight
  // and its row is therefore still on screen, which is the whole of the double-press. Awaiting the
  // first would test a press against a row that no longer exists, which is a different bug.
  const first = SW.store.cancelQueuedTurn(waiting.queuedTurns[0].ticket);
  const second = SW.store.cancelQueuedTurn(waiting.queuedTurns[0].ticket);
  await Promise.all([first, second]);
}
letGo(0);
await turn;

const after = SW.store.get();
console.log(JSON.stringify({
  whileWaiting,
  queuedAfter: after.queuedTurns.length,
  rolesAfter: after.messages.map((m) => m.role),
  answer: after.messages.flatMap((m) => (m.blocks || []).map((b) => b.value)).filter(Boolean),
  composerSeed: after.composerSeed,
  posted,
  said,
}));
