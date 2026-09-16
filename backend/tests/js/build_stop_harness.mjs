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
// Input on stdin: `{ "mode": ... }`, one of the keys of `OPENING` below. Each mode sends and then
// holds the stream open at a chosen frame, which is where every assertion is taken. `queued` holds
// it at the `pending` frame: a turn waiting in line is NOT the turn holding the lock, and must not
// claim to be one. The `opening*` modes hold it EARLIER than any of the others — at the moment the
// person's question is on screen and the model has not answered yet, which is the window the Stop
// button used to be missing for (#371).
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
  message: 'Waiting on the turn that is running.',
};
// The frame the server really sends first on the CHAT path, and only there: it paints the person's
// own question, and it is yielded as soon as the turn has the lock, long before the model has said
// anything (`service.py` `_chat_stream`, `yield user_ev`). The store's handler skips it, so it
// claims nothing — which is the whole of the window #371 is about.
//
// The build paths have no equivalent and this file must not invent one. Each of their generators
// writes the user row with `append_history(ev, ...)` under `if ev["type"] != "user"`, so it reaches
// the transcript and never the stream. A build's first STREAMED frame is its first real work frame,
// which is why `openingBuild` and `openingApprove` below pause on no frame at all.
const USER = { chat: { type: 'user', text: 'how many rows?' } };
const TOOL = { type: 'agent', kind: 'tool', tool: 'write', detail: 'src/App.tsx' };
const BUILT = [{ type: 'done', ok: true, decision: 'built' }];
const ANSWERED = [{ type: 'delta', text: 'Six million rows.', final: true },
                  { type: 'done', ok: true, decision: 'answered' }];
// The frames each mode streams before the harness pauses.
//
// The `opening*` modes are the ones this file was missing. Every other mode pauses after a frame
// that claims the turn, so none of them can express the state before one arrives — which is
// precisely the state a person sits in while Sage runs the gate and waits for a first token.
//
// Chat pauses on the `user` frame alone: that frame proves the stream is open and flowing and
// STILL claims nothing, which is what makes the missing button so clearly wrong. Build and approve
// pause on no frame at all, because they have none to pause on — their user row never leaves the
// server, so the next thing down the wire after the POST is the first frame of real work, gate and
// first token and all.
const OPENING = {
  build: [TOOL],
  approve: [TOOL],
  chat: [USER.chat, { type: 'delta', text: 'Looking…' }],
  queued: [PENDING],
  // The same question of the other two sends. `queued` only ever asked it of sendBuildPrompt,
  // and each send has a name of its own to hand back now.
  queuedChat: [PENDING],
  queuedApprove: [PENDING],
  opening: [USER.chat],
  openingBuild: [],
  openingApprove: [],
  // Out of the queue and running: the `pending` frame handed the name back, and the `user` frame
  // behind it is the queue letting go. The wait for a first token starts again here.
  requeued: [PENDING, USER.chat],
  // The first of this mode's two turns, and the one that is really running.
  secondInLine: [USER.chat, { type: 'delta', text: 'Looking…' }],
}[mode];
const REST = {
  build: BUILT,
  approve: BUILT,
  chat: ANSWERED,
  queued: [TOOL, ...BUILT],
  queuedChat: [USER.chat, ...ANSWERED],
  queuedApprove: [TOOL, ...BUILT],
  opening: ANSWERED,
  openingBuild: [TOOL, ...BUILT],
  openingApprove: [TOOL, ...BUILT],
  requeued: ANSWERED,
  secondInLine: ANSWERED,
}[mode];

// Modes that send TWICE, and what the second send's stream says. One lock means the second turn
// waits in line, which is what its `pending` frame reports — and a send that named itself before
// hearing that frame would have named itself OVER the turn that is actually running, so the frame
// handing the name back would blank the bar for a running turn. That is #126 from this direction,
// and it is what the send-time claim has to be careful of.
const SECOND = {
  secondInLine: { opening: [PENDING], rest: [USER.chat, ...ANSWERED] },
}[mode];

const frame = (ev) => new TextEncoder().encode(`data: ${JSON.stringify(ev)}\n\n`);
const join = (evs) => evs.map(frame).reduce(
  (a, b) => new Uint8Array([...a, ...b]), new Uint8Array());

// The stream stops after its opening frames until the harness lets it go. That pause is where the
// screen is read, and it is the state a poll would otherwise have had two seconds to repair.
let letGo = () => {};
const gate = new Promise((resolve) => { letGo = resolve; });

// Resolved the moment the reader comes back for a second chunk. `readSSE` drains a chunk into the
// handler before it reads again, so by then every opening frame has been delivered AND handled —
// which names the pause exactly, rather than counting ticks and testing the scheduler. It has to
// be this and not "wait until the turn is claimed": the claim is what is under test, and a mode
// whose whole point is the window before one would wait for ever.
//
// Every stream this mode opens has to be at that pause before the screen is read, which for the
// two-send modes is both of them.
let pausesLeft = SECOND ? 2 : 1;
let reachedPause = () => {};
const atPause = new Promise((resolve) => {
  reachedPause = () => { pausesLeft -= 1; if (pausesLeft === 0) resolve(); };
});

// Answered by every `/build/state` read. Deliberately empty of a running turn: this harness is
// about what the tab can say for itself, and a poll that supplied the answer would hide the bug.
const BUILD_STATE = { running: false, wedged: false, pending: 0, running_turn: null };

// Which send each opened stream is answering. Only the two-send modes ever pass 1.
let posts = 0;

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
      const first = posts === 0;
      posts += 1;
      // Loud on purpose. A mode that opens a stream this file did not plan for would otherwise get
      // a `TypeError` the send catches into an `error` frame, and the run would still print a
      // verdict — about a turn that never streamed.
      if (!first && !SECOND) throw new Error(`mode ${mode} opened a second stream`);
      const opening = first ? OPENING : SECOND.opening;
      const rest = first ? REST : SECOND.rest;
      let sent = 0;
      return { ok: true, body: { getReader: () => ({
        read: async () => {
          if (sent === 0) { sent = 1; return { done: false, value: join(opening) }; }
          if (sent === 1) { sent = 2; reachedPause(); await gate; return { done: false, value: join(rest) }; }
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

// Which of the three sends each mode drives. Two of them are a build turn and one is a chat turn,
// and that is the only axis the readout below cares about.
const SEND = {
  build: 'build', approve: 'approve', chat: 'chat', queued: 'build',
  opening: 'chat', openingBuild: 'build', openingApprove: 'approve', requeued: 'chat',
  secondInLine: 'chat', queuedChat: 'chat', queuedApprove: 'approve',
}[mode];
const kind = SEND === 'chat' ? 'chat' : 'build';
const turn = SEND === 'approve' ? SW.store.approveBuild('')
  : SEND === 'chat' ? SW.store.sendMessage('how many rows?')
  : SW.store.sendBuildPrompt('build me a dashboard');
// Sent while the first is still streaming, and synchronously after it: a send takes its name
// before its first `await`, so which of the two got there first is decided here and not by the
// scheduler.
const second = SECOND ? SW.store.sendMessage('and how many columns?') : null;

// Bounded, so that a send which never opens a stream at all says so rather than hanging: the
// timeout is an error about the harness, not a verdict about the store.
let bail;
await Promise.race([atPause, new Promise((_, reject) => {
  bail = setTimeout(() => reject(new Error('the stream never paused: no POST was made')), 10000);
})]);
clearTimeout(bail);

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
await Promise.all(second ? [turn, second] : [turn]);

console.log(JSON.stringify({
  midTurn,
  // And the bar comes down by itself: the turn this tab named is the turn this tab forgets.
  runningTurnAfter: SW.store.get().runningTurn,
  runningAfter: kind === 'chat' ? SW.store.get().chatRunning : SW.store.get().buildRunning,
}));
