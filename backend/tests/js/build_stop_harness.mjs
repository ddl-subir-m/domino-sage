// Drives the real store.js through a build this tab starts, and reports whether the Stop bar had a
// turn to match WHILE the build was streaming (#126).
//
// Reading the source is not enough here for the same reason it was not enough for the queue: the
// interesting state is the one between two SSE frames. `state.buildRunning` is set optimistically by
// the send, but `state.runningTurn` — the field the Stop bar matches against — was only ever written
// by a `/build/state` poll, and nothing polls while a send is holding its own stream open. The bar
// therefore rendered its "the workspace is busy" caption over a build this very tab had just
// started, and the Stop button appeared only once a mode switch reloaded the state behind it.
//
// Input on stdin: `{ "mode": "build" | "approve" | "chat" | "queued" }`. The first three send and
// then hold the stream open mid-turn, which is where every assertion is taken. The fourth holds it
// at the `pending` frame instead: a turn waiting in line is NOT the turn holding the lock, and must
// not claim to be one.
//
// The stubs are the smallest set store.js touches on this path. React is never rendered.
import fs from 'node:fs';
import vm from 'node:vm';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { mode } = JSON.parse(fs.readFileSync(0, 'utf8'));

const PENDING = {
  type: 'pending',
  ticket: 'turn_abc',
  prompt: 'build me a dashboard',
  message: 'Queued behind the turn that is running.',
};
// The frames each mode streams before the harness pauses. Whatever comes first, it is not the
// queue, so by then the turn is running and the bar has to be able to say so.
const OPENING = {
  build: [{ type: 'agent', kind: 'tool', tool: 'write', detail: 'src/App.tsx' }],
  approve: [{ type: 'agent', kind: 'tool', tool: 'write', detail: 'src/App.tsx' }],
  chat: [{ type: 'delta', text: 'Looking…' }],
  queued: [PENDING],
}[mode];
const REST = {
  build: [{ type: 'done', ok: true, decision: 'built' }],
  approve: [{ type: 'done', ok: true, decision: 'built' }],
  chat: [{ type: 'delta', text: 'Six million rows.', final: true },
         { type: 'done', ok: true, decision: 'answered' }],
  queued: [{ type: 'agent', kind: 'tool', tool: 'write', detail: 'src/App.tsx' },
           { type: 'done', ok: true, decision: 'built' }],
}[mode];

const frame = (ev) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`);
const join = (evs) => evs.map(frame).reduce(
  (a, b) => new Uint8Array([...a, ...b]), new Uint8Array());

// The stream stops after its opening frames until the harness lets it go. That pause is where the
// screen is read, and it is the state a poll would otherwise have had two seconds to repair.
let letGo = () => {};
const gate = new Promise((resolve) => { letGo = resolve; });

// Answered by every `/build/state` read. Deliberately empty of a running turn: this harness is
// about what the tab can say for itself, and a poll that supplied the answer would hide the bug.
const BUILD_STATE = { running: false, wedged: false, pending: 0, running_turn: null };

const sandbox = {
  console, JSON, Math, Date, process, Set, Map, Promise, Array, Object, String, Number, Boolean,
  RegExp, Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout, clearTimeout,
  setInterval, clearInterval, Blob, ArrayBuffer, Uint8Array,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  requestAnimationFrame: (fn) => fn(),
  document: { addEventListener() {}, querySelector: () => null, body: {} },
  React: { createElement: (t, p, ...c) => ({ t, p, c }), useState: () => [null, () => {}],
           useEffect: () => {}, useRef: () => ({ current: null }), Fragment: 'Fragment' },
  antd: { message: { success() {}, error() {}, info() {}, warning() {} }, Modal: { confirm() {} } },
  fetch: async (url, options) => {
    const href = String(url);
    if (options && options.method === 'POST'
        && (href.includes('/build/stream') || href.includes('/build/approve')
            || href.includes('/chat/stream'))) {
      let sent = 0;
      return { ok: true, body: { getReader: () => ({
        read: async () => {
          if (sent === 0) { sent = 1; return { done: false, value: join(OPENING) }; }
          if (sent === 1) { sent = 2; await gate; return { done: false, value: join(REST) }; }
          return { done: true };
        },
      }) } };
    }
    const json = href.includes('/build/state') ? BUILD_STATE
      : (href.includes('/history') || href.includes('/apps') ? [] : {});
    return { ok: true, status: 200, headers: { get: () => 'application/json' },
             json: async () => json, text: async () => '' };
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ['util.js', 'prefs.js', 'api.js', 'store.js']) {
  vm.runInContext(fs.readFileSync(ROOT + f, 'utf8'), sandbox, { filename: f });
}

const SW = sandbox.SW;
SW.store.set({
  thread: { id: 't1', artifacts: [] },
  messages: [],
  scope: { id: 'p', name: 'P' },
  activeApp: { id: 'app_1', name: 'Usage Pulse' },
  apps: [{ id: 'app_1', name: 'Usage Pulse' }],
});

const settle = () => new Promise((r) => setTimeout(r, 0));
const kind = mode === 'chat' ? 'chat' : 'build';
const turn = mode === 'approve' ? SW.store.approveBuild('')
  : mode === 'chat' ? SW.store.sendMessage('how many rows?')
  : SW.store.sendBuildPrompt('build me a dashboard');

// Wait for the first frame to land rather than for a tick count: a fixed number of microtasks
// would be a test of the scheduler.
// Bounded: with the bug present nothing ever claims the turn, the loop runs out, and the readout
// below reports the empty screen that was the complaint.
const arrived = () => (mode === 'queued'
  ? SW.store.get().queuedTurns.length > 0
  : SW.store.get().runningTurn !== null);
for (let i = 0; i < 200 && !arrived(); i += 1) await settle();

// Read out inside the pause. `store.get()` hands back the live state object, so anything held
// across the `await` below would report the end of the turn rather than the middle of it.
const midTurn = {
  // What the mode actually renders its Stop button from: the turn holding the lock is the one on
  // this screen — this conversation, this Built App.
  stopOffered: kind === 'chat'
    ? SW.store.runningTurnHere('chat', 't1')
    : SW.store.runningTurnHere('build', 't1', 'app_1'),
  // And the caption it renders INSTEAD, which is what was on screen while the button was missing.
  elsewhere: kind === 'chat'
    ? SW.store.runningTurnElsewhere('chat', 't1')
    : SW.store.runningTurnElsewhere('build', 't1', 'app_1'),
  // A build in this conversation on a Built App the rail has moved away from is not this screen's
  // turn, so the button must not follow the person over there (#126, #77).
  stopOfferedOnAnotherApp: SW.store.runningTurnHere('build', 't1', 'app_2'),
  // Nor is it Chat's turn to stop, and vice versa.
  stopOfferedInTheOtherMode: kind === 'chat'
    ? SW.store.runningTurnHere('build', 't1', 'app_1')
    : SW.store.runningTurnHere('chat', 't1'),
  running: kind === 'chat' ? SW.store.get().chatRunning : SW.store.get().buildRunning,
};

letGo();
await turn;

console.log(JSON.stringify({
  midTurn,
  // And the bar comes down by itself: the turn this tab named is the turn this tab forgets.
  runningTurnAfter: SW.store.get().runningTurn,
  runningAfter: kind === 'chat' ? SW.store.get().chatRunning : SW.store.get().buildRunning,
}));
