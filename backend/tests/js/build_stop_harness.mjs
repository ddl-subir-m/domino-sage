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
import { unrefTimeout } from './sandbox_timeout.mjs';

const ROOT = new URL('../../sage/workbench/js/', import.meta.url).pathname;
const { mode } = JSON.parse(fs.readFileSync(0, 'utf8'));
const EPOCH = 'boot_current';

const PENDING = {
  type: 'pending',
  ticket: 'turn_abc',
  sequence: 1,
  epoch: EPOCH,
  prompt: 'build me a dashboard',
  message: 'Waiting on the turn that is running.',
};
const PENDING_2 = { ...PENDING, ticket: 'turn_def', sequence: 2 };
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
// The queue's other end (#377). `_acquire_turn` yields it only after a `pending` frame. An
// uncontended turn keeps its established event sequence and receives its exact identity in the
// response header instead. For a queued turn this has the same ticket as the `pending` row.
const RUNNING = { type: 'running', ticket: PENDING.ticket, sequence: PENDING.sequence,
  epoch: PENDING.epoch };
const RUNNING_2 = { type: 'running', ticket: PENDING_2.ticket, sequence: PENDING_2.sequence,
  epoch: PENDING_2.epoch };
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
  droppedBuild: [TOOL],
  droppedApprove: [TOOL],
  droppedReadFailure: [TOOL],
  droppedStateFailure: [TOOL],
  stopStateFailure: [TOOL],
  legacyNoIdentity: [TOOL],
  preframeStopRace: [],
  successorHeaderRace: [TOOL],
  authoritativeHeaders: [USER.chat, { type: 'delta', text: 'Looking…' }],
  lateRunningHeader: [{ type: 'done', ok: true, decision: 'answered' }],
  legacyIdlessLateHeader: [{ type: 'done', ok: true, decision: 'answered' }],
  restartEpochRace: [{ type: 'done', ok: true, decision: 'answered' }],
  localBeatsState: [USER.chat, { type: 'delta', text: 'Looking…' }],
  stoppedLocalBeatsState: [TOOL],
  // Out of the queue and running: the `pending` frame handed the name back, and the `running` frame
  // behind it is the queue letting go. The wait for a first token starts again here, which is why
  // the pause is taken ON that frame — the `user` one Chat sends next, and the first tool call
  // Build sends next, are both the far end of the window, not the near one.
  requeued: [PENDING, RUNNING],
  // The same, for the two sends that have no `user` frame to fall back on — the whole of #377.
  // Nothing follows the grant here because on a build nothing does: the next thing down the wire
  // after it is the first frame of real work, gate and first token and all.
  requeuedBuild: [PENDING, RUNNING],
  requeuedApprove: [PENDING, RUNNING],
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
  droppedBuild: [],
  droppedApprove: [],
  droppedReadFailure: [],
  droppedStateFailure: [],
  stopStateFailure: [],
  legacyNoIdentity: BUILT,
  preframeStopRace: BUILT,
  successorHeaderRace: BUILT,
  authoritativeHeaders: ANSWERED,
  lateRunningHeader: [],
  legacyIdlessLateHeader: [],
  restartEpochRace: [],
  localBeatsState: ANSWERED,
  stoppedLocalBeatsState: BUILT,
  requeued: [USER.chat, ...ANSWERED],
  requeuedBuild: [TOOL, ...BUILT],
  requeuedApprove: [TOOL, ...BUILT],
  secondInLine: ANSWERED,
}[mode];

// Modes that send TWICE, and what the second send's stream says. One lock means the second turn
// waits in line, which is what its `pending` frame reports — and a send that named itself before
// hearing that frame would have named itself OVER the turn that is actually running, so the frame
// handing the name back would blank the bar for a running turn. That is #126 from this direction,
// and it is what the send-time claim has to be careful of.
const SECOND = {
  secondInLine: { opening: [PENDING_2], rest: [RUNNING_2, USER.chat, ...ANSWERED] },
  authoritativeHeaders: {
    opening: [{ ...PENDING, ticket: 'turn_b', sequence: 2 }],
    rest: [{ type: 'running', ticket: 'turn_b', sequence: 2 }, USER.chat, ...ANSWERED],
  },
  lateRunningHeader: {
    opening: [{ ...PENDING, ticket: 'turn_b', sequence: 2 },
      { type: 'running', ticket: 'turn_b', sequence: 2 }],
    rest: [USER.chat, ...ANSWERED],
  },
  legacyIdlessLateHeader: {
    opening: [{ ...PENDING, ticket: 'turn_b', sequence: 2 },
      { type: 'running', ticket: 'turn_b', sequence: 2 }],
    rest: [USER.chat, ...ANSWERED],
  },
  restartEpochRace: {
    opening: [{ ...PENDING, ticket: 'turn_b', sequence: 1, epoch: 'boot_new' },
      { type: 'running', ticket: 'turn_b', sequence: 1, epoch: 'boot_new' }],
    rest: [USER.chat, ...ANSWERED],
  },
}[mode];
const THIRD = mode === 'authoritativeHeaders' ? {
  opening: [{ ...PENDING, ticket: 'turn_c', sequence: 3 }],
  rest: [{ type: 'running', ticket: 'turn_c', sequence: 3 }, USER.chat, ...ANSWERED],
} : null;

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
let pausesLeft = THIRD ? 3 : (SECOND ? 2 : 1);
let reachedPause = () => {};
const atPause = new Promise((resolve) => {
  reachedPause = () => { pausesLeft -= 1; if (pausesLeft === 0) resolve(); };
});

// Answered by every `/build/state` read. Deliberately empty of a running turn: this harness is
// about what the tab can say for itself, and a poll that supplied the answer would hide the bug.
const dropped = ['droppedBuild', 'droppedApprove', 'droppedReadFailure',
  'droppedStateFailure', 'stopStateFailure'].includes(mode);
let backendRunning = dropped || mode === 'legacyStateReconstruction';
let backendTurnId = mode === 'legacyStateReconstruction' ? 'turn_b' : 'turn_abc';
let backendEpoch = EPOCH;
let backendKind = 'build';
const buildState = () => ({ running: backendRunning, wedged: false, pending: 0,
  turn_epoch: mode === 'legacyStateReconstruction' ? undefined : backendEpoch,
  running_turn: backendRunning
    ? { kind: backendKind, conversation: 't1', app: backendKind === 'build' ? 'app_1' : '',
      turnId: backendTurnId,
      sequence: mode === 'legacyStateReconstruction' ? undefined : 1,
      epoch: mode === 'legacyStateReconstruction' ? undefined : backendEpoch } : null });
const intervalCallbacks = [];
let buildStateReads = 0;
let failBuildState = mode === 'droppedStateFailure';
const stateAnswers = [];
const stateGates = [0, 1].map((_, index) => new Promise((resolve) => {
  stateAnswers[index] = resolve;
}));
let answerRaceState = () => {};
const raceStateGate = new Promise((resolve) => { answerRaceState = resolve; });
let answerCrossSourceState = () => {};
const crossSourceStateGate = new Promise((resolve) => { answerCrossSourceState = resolve; });
let markRequestStarted = () => {};
const requestStarted = new Promise((resolve) => { markRequestStarted = resolve; });
let answerResponse = () => {};
const responseGate = new Promise((resolve) => { answerResponse = resolve; });
const headerAnswers = [];
const headerGates = Array.from({ length: 3 }, (_, index) => new Promise((resolve) => {
  headerAnswers[index] = resolve;
}));
const pauseAnswers = [];
const pauseGates = Array.from({ length: 3 }, (_, index) => new Promise((resolve) => {
  pauseAnswers[index] = resolve;
}));

// Which send each opened stream is answering. Only the two-send modes ever pass 1.
let posts = 0;
const stopBodies = [];
let buildStateReadsAtStop = null;

const sandbox = {
  console, JSON, Math, Date, process, Set, Map, Promise, Array, Object, String, Number, Boolean,
  RegExp, Error, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout: unrefTimeout, clearTimeout,
  setInterval: (fn) => { intervalCallbacks.push(fn); return intervalCallbacks.length; },
  clearInterval() {}, Blob, ArrayBuffer, Uint8Array,
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
      const index = posts;
      const first = index === 0;
      posts += 1;
      // Loud on purpose. A mode that opens a stream this file did not plan for would otherwise get
      // a `TypeError` the send catches into an `error` frame, and the run would still print a
      // verdict — about a turn that never streamed.
      if (!first && !SECOND) throw new Error(`mode ${mode} opened a second stream`);
      if (index > 1 && !THIRD) throw new Error(`mode ${mode} opened a third stream`);
      const later = index === 1 ? SECOND : THIRD;
      const opening = first ? OPENING : later.opening;
      const rest = first ? REST : later.rest;
      const responseTurnId = (mode === 'legacyIdlessLateHeader' && first)
          || mode === 'legacyNoIdentity' ? ''
        : mode === 'restartEpochRace' ? (first ? 'turn_a' : 'turn_b')
        : ['localBeatsState', 'stoppedLocalBeatsState'].includes(mode) ? 'turn_b'
        : mode === 'successorHeaderRace' ? 'turn_b'
        : mode === 'authoritativeHeaders' ? ['turn_a', 'turn_b', 'turn_c'][index]
          : (first ? 'turn_abc' : 'turn_def');
      const pendingResponse = ['queued', 'queuedChat', 'queuedApprove', 'requeued',
        'requeuedBuild', 'requeuedApprove'].includes(mode)
        || (!first && ['secondInLine', 'authoritativeHeaders', 'lateRunningHeader',
          'legacyIdlessLateHeader'].includes(mode));
      if (mode === 'successorHeaderRace') {
        markRequestStarted();
        await responseGate;
      }
      if (mode === 'authoritativeHeaders') await headerGates[index];
      if (['lateRunningHeader', 'legacyIdlessLateHeader', 'restartEpochRace'].includes(mode)
          && first) {
        await headerGates[index];
      }
      let sent = 0;
      return { ok: true, headers: { get: (name) => {
        const header = String(name).toLowerCase();
        if (header === 'x-sage-turn-id') return responseTurnId;
        if (header === 'x-sage-turn-state') {
          if (mode === 'legacyNoIdentity') return null;
          return pendingResponse ? 'pending' : 'running';
        }
        if (header === 'x-sage-turn-sequence') {
          if (mode === 'legacyNoIdentity' || mode === 'successorHeaderRace'
              || (mode === 'legacyIdlessLateHeader' && first)) return null;
          if (mode === 'restartEpochRace') return first ? '9' : '1';
          if (['localBeatsState', 'stoppedLocalBeatsState'].includes(mode)) return '1';
          return String(index + 1);
        }
        if (header === 'x-sage-turn-epoch') {
          if (mode === 'legacyNoIdentity') return null;
          if (mode === 'restartEpochRace') return first ? 'boot_old' : 'boot_new';
          if (['localBeatsState', 'stoppedLocalBeatsState'].includes(mode)) return 'boot_new';
          return EPOCH;
        }
        return 'text/event-stream';
      } }, body: { getReader: () => ({
        read: async () => {
          if (sent === 0) { sent = 1; return { done: false, value: join(opening) }; }
          if (sent === 1) {
            sent = 2;
            reachedPause();
            pauseAnswers[index]();
            await gate;
            if (dropped) {
              throw new TypeError('network error');
            }
            return { done: false, value: join(rest) };
          }
          return { done: true };
        },
      }) } };
    }
    if (href.includes('/build/stop')) {
      buildStateReadsAtStop = buildStateReads;
      const body = JSON.parse(options.body);
      stopBodies.push(body);
      return { ok: true, status: 200, headers: { get: () => 'application/json' },
               json: async () => ({ stopped: true, turnId: body.turnId }), text: async () => '' };
    }
    if (mode === 'droppedReadFailure' && !backendRunning
        && (href.includes('/history') || href.includes('/apps'))) {
      throw new TypeError('refresh read failed');
    }
    if (href.includes('/build/state')) {
      buildStateReads += 1;
      if (mode === 'preframeStopRace') await raceStateGate;
      if (failBuildState) throw new TypeError('state network error');
      if (mode === 'chatStateReverse') {
        const index = buildStateReads - 1;
        await stateGates[index];
        const id = index === 0 ? 'turn_a' : 'turn_b';
        const sequence = index === 0 ? 8 : 1;
        const epoch = index === 0 ? 'boot_old' : 'boot_new';
        const answer = { running: true, wedged: false, pending: 0, turn_epoch: epoch,
          running_turn: { kind: 'chat', conversation: 't1', app: '', turnId: id,
            sequence, epoch } };
        return { ok: true, status: 200, headers: { get: () => 'application/json' },
                 json: async () => answer, text: async () => '' };
      }
      if (['localBeatsState', 'stoppedLocalBeatsState'].includes(mode)) {
        const index = buildStateReads - 1;
        if (index === 0) {
          await crossSourceStateGate;
          const build = mode === 'stoppedLocalBeatsState';
          const answer = { running: true, wedged: false, pending: 0, turn_epoch: 'boot_old',
            running_turn: { kind: build ? 'build' : 'chat', conversation: 't1',
              app: build ? 'app_1' : '', turnId: 'turn_a', sequence: 9,
              epoch: 'boot_old' } };
          return { ok: true, status: 200, headers: { get: () => 'application/json' },
                   json: async () => answer, text: async () => '' };
        }
        backendRunning = true;
        backendKind = mode === 'stoppedLocalBeatsState' ? 'build' : 'chat';
        backendTurnId = 'turn_b';
        backendEpoch = 'boot_new';
      }
    }
    const json = href.includes('/build/state') ? buildState()
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
  runningTurn: ['successorHeaderRace', 'legacyStateReconstruction'].includes(mode)
    ? { kind: 'build', conversation: 't1', app: 'app_1', turnId: 'turn_a' } : null,
});

if (mode === 'legacyStateReconstruction') {
  // No request is live in this tab. A legacy backend supplies no sequence, but its state endpoint
  // is authoritative: completed A must be replaced by current B.
  await SW.store.loadBuild({ keepPreview: true });
  const current = SW.store.get().runningTurn;
  console.log(JSON.stringify({ turnId: current && current.turnId,
    sequence: current && current.sequence,
    stopOffered: SW.store.runningTurnHere('build', 't1', 'app_1') }));
  process.exit(0);
}

if (mode === 'chatStateReverse') {
  const first = SW.store.refreshTurnState();
  const second = SW.store.refreshTurnState();
  stateAnswers[1]();
  await second;
  stateAnswers[0]();
  await first;
  const current = SW.store.get().runningTurn;
  console.log(JSON.stringify({ turnId: current && current.turnId,
    epoch: current && current.epoch, sequence: current && current.sequence }));
  process.exit(0);
}

let delayedCrossSourceState = null;
if (['localBeatsState', 'stoppedLocalBeatsState'].includes(mode)) {
  delayedCrossSourceState = SW.store.refreshTurnState();
}

if (mode === 'restartEpochRace') {
  backendRunning = true;
  backendKind = 'chat';
  backendTurnId = 'turn_a';
  backendEpoch = 'boot_old';
  await SW.store.refreshTurnState();
}

// Which of the three sends each mode drives. Two of them are a build turn and one is a chat turn,
// and that is the only axis the readout below cares about.
const SEND = {
  build: 'build', approve: 'approve', chat: 'chat', queued: 'build',
  opening: 'chat', openingBuild: 'build', openingApprove: 'approve', requeued: 'chat',
  requeuedBuild: 'build', requeuedApprove: 'approve',
  droppedBuild: 'build', droppedApprove: 'approve',
  droppedReadFailure: 'build',
  droppedStateFailure: 'build', stopStateFailure: 'build',
  legacyNoIdentity: 'build', restartEpochRace: 'chat',
  localBeatsState: 'chat', stoppedLocalBeatsState: 'build',
  preframeStopRace: 'build',
  successorHeaderRace: 'build',
  authoritativeHeaders: 'chat',
  lateRunningHeader: 'chat',
  legacyIdlessLateHeader: 'chat',
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
const third = THIRD ? SW.store.sendMessage('and how many regions?') : null;

if (mode === 'authoritativeHeaders') {
  // HTTP scheduling returns C, then B, then A. The static server state, not arrival order, says A
  // owns the lock and B/C are pending.
  headerAnswers[2]();
  await Promise.resolve();
  headerAnswers[1]();
  await Promise.resolve();
  headerAnswers[0]();
}

if (['lateRunningHeader', 'legacyIdlessLateHeader'].includes(mode)) {
  // B reaches its server-ordered running event while A's older response callback is delayed.
  // The late A header must not replace B, and A's unwind must not clear B.
  await pauseGates[1];
  headerAnswers[0]();
}

if (mode === 'restartEpochRace') {
  // State established old-process A before either local response arrived. B's current response
  // establishes the restarted backend directly; no state poll is needed between B and late A.
  await pauseGates[1];
  headerAnswers[0]();
}

if (mode === 'successorHeaderRace') {
  // B was sent while A owned the claim, so B could not name itself at send time. A ends before
  // B's response arrives. B has no pending/running queue frame; its response header must survive
  // in B's request and name the fallback claim without ever renaming A.
  await requestStarted;
  SW.store.set({ runningTurn: null });
  answerResponse();
}

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
  // The composer's own queue rows (#79), which are the other thing a `pending` frame writes and the
  // `running` frame behind it has to take back: "waiting in line · Cancel" over a turn the bar is
  // at the same moment offering a Stop for, with a Cancel the server can no longer act on.
  queued: SW.store.get().queuedTurns.length,
  // The build transcript, by row type. Here because the `running` frame is a state row and not a
  // receipt: reach `applyBuildEvent` with one and it falls past every branch to `appendBuildRow`,
  // which writes it into the transcript the person reads. The claim above would still be right and
  // the row would still be junk, so the two need separate readouts.
  rows: SW.store.get().buildHistory.map((r) => r.type),
  // A build in this conversation on a Built App the rail has moved away from is not this screen's
  // turn, so the button must not follow the person over there (#126, #77).
  stopOfferedOnAnotherApp: SW.store.runningTurnHere('build', 't1', 'app_2'),
  // Nor is it Chat's turn to stop, and vice versa.
  stopOfferedInTheOtherMode: kind === 'chat'
    ? SW.store.runningTurnHere('build', 't1', 'app_1')
    : SW.store.runningTurnHere('chat', 't1'),
  running: kind === 'chat' ? SW.store.get().chatRunning : SW.store.get().buildRunning,
  turnId: SW.store.get().runningTurn && SW.store.get().runningTurn.turnId,
  sequence: SW.store.get().runningTurn && SW.store.get().runningTurn.sequence,
  epoch: SW.store.get().runningTurn && SW.store.get().runningTurn.epoch,
};

if (mode === 'legacyNoIdentity') {
  await SW.store.stopBuild();
  letGo();
  await turn;
  console.log(JSON.stringify({ stopOffered: midTurn.stopOffered,
    message: midTurn.elsewhere && midTurn.elsewhere.text,
    stopPosts: stopBodies.length, buildStateReads }));
  process.exit(0);
}

if (mode === 'preframeStopRace') {
  // The static header has already bound A's exact ticket, although no SSE frame has arrived. A
  // Stop must use that ticket directly and must never poll state, where a same-scope B could be
  // mistaken for A after the stream finishes.
  const stop = SW.store.stopBuild();
  await Promise.resolve();
  await Promise.resolve();
  letGo();
  await turn;
  backendRunning = true;
  backendTurnId = 'turn_b';
  answerRaceState();
  await stop;
  console.log(JSON.stringify({
    buildStateReads,
    buildStateReadsAtStop,
    stopPosts: stopBodies.length,
    requestedTurnId: stopBodies[0] && stopBodies[0].turnId,
  }));
  process.exit(0);
}

if (mode === 'authoritativeHeaders') {
  const stop = SW.store.stopChat();
  await stop;
  letGo();
  await Promise.all([turn, second, third]);
  console.log(JSON.stringify({
    turnId: midTurn.turnId,
    sequence: midTurn.sequence,
    queued: midTurn.queued,
    stopPosts: stopBodies.length,
    requestedTurnId: stopBodies[0] && stopBodies[0].turnId,
  }));
  process.exit(0);
}

if (['lateRunningHeader', 'legacyIdlessLateHeader'].includes(mode)) {
  const stop = SW.store.stopChat();
  await stop;
  letGo();
  await Promise.all([turn, second]);
  console.log(JSON.stringify({
    turnId: midTurn.turnId,
    sequence: midTurn.sequence,
    stopPosts: stopBodies.length,
    requestedTurnId: stopBodies[0] && stopBodies[0].turnId,
  }));
  process.exit(0);
}

if (mode === 'restartEpochRace') {
  await SW.store.stopChat();
  letGo();
  await Promise.all([turn, second]);
  console.log(JSON.stringify({ turnId: midTurn.turnId, epoch: midTurn.epoch,
    sequence: midTurn.sequence, stopPosts: stopBodies.length,
    requestedTurnId: stopBodies[0] && stopBodies[0].turnId }));
  process.exit(0);
}

if (mode === 'localBeatsState') {
  answerCrossSourceState();
  await delayedCrossSourceState;
  const afterState = SW.store.get().runningTurn;
  await SW.store.stopChat();
  letGo();
  await turn;
  console.log(JSON.stringify({ turnId: afterState && afterState.turnId,
    epoch: afterState && afterState.epoch, sequence: afterState && afterState.sequence,
    stopPosts: stopBodies.length, requestedTurnId: stopBodies[0] && stopBodies[0].turnId }));
  process.exit(0);
}

if (mode === 'stoppedLocalBeatsState') {
  await SW.store.stopBuild();
  answerCrossSourceState();
  await delayedCrossSourceState;
  const afterState = SW.store.get().runningTurn;
  letGo();
  await turn;
  console.log(JSON.stringify({ running: SW.store.get().buildRunning,
    turnId: afterState && afterState.turnId, stopPosts: stopBodies.length,
    requestedTurnId: stopBodies[0] && stopBodies[0].turnId }));
  process.exit(0);
}

if (mode === 'successorHeaderRace') {
  const stop = SW.store.stopBuild();
  await stop;
  letGo();
  await turn;
  console.log(JSON.stringify({
    turnId: midTurn.turnId,
    stopPosts: stopBodies.length,
    requestedTurnId: stopBodies[0] && stopBodies[0].turnId,
  }));
  process.exit(0);
}

letGo();
await Promise.all(second ? [turn, second] : [turn]);

if (dropped) {
  const afterDrop = {
    running: SW.store.get().buildRunning,
    stopOffered: SW.store.runningTurnHere('build', 't1', 'app_1'),
    typing: SW.store.get().buildTyping,
    watcher: intervalCallbacks.length > 0,
  };
  if (mode === 'droppedStateFailure') {
    console.log(JSON.stringify({ ...afterDrop,
      turnId: SW.store.get().runningTurn && SW.store.get().runningTurn.turnId }));
    process.exit(0);
  }
  // A refresh has no live stream or browser claim. It must rebuild both from `/build/state`.
  SW.store.set({ buildRunning: false, runningTurn: null });
  await SW.store.loadBuild({ keepPreview: true });
  const afterRefresh = {
    running: SW.store.get().buildRunning,
    stopOffered: SW.store.runningTurnHere('build', 't1', 'app_1'),
  };
  if (mode === 'stopStateFailure') failBuildState = true;
  await SW.store.stopBuild();
  const afterCancel = {
    running: SW.store.get().buildRunning,
    stopOffered: SW.store.runningTurnHere('build', 't1', 'app_1'),
    requestedTurnId: stopBodies[0] && stopBodies[0].turnId,
  };
  if (mode === 'stopStateFailure') {
    failBuildState = false;
    // The stopped turn is still unwinding. A successful read of the same ticket must consume the
    // accepted-Stop latch without putting Building back on screen.
    await intervalCallbacks[intervalCallbacks.length - 1]();
    const afterUnwind = {
      running: SW.store.get().buildRunning,
      stopOffered: SW.store.runningTurnHere('build', 't1', 'app_1'),
    };
    backendRunning = false;
    await intervalCallbacks[intervalCallbacks.length - 1]();
    const afterRelease = {
      running: SW.store.get().buildRunning,
      stopOffered: SW.store.runningTurnHere('build', 't1', 'app_1'),
    };
    console.log(JSON.stringify({ afterDrop, afterRefresh, afterCancel,
      afterUnwind, afterRelease }));
    process.exit(0);
  }
  // The accepted stop is still unwinding above. Once the backend releases it, the watcher settles.
  backendRunning = false;
  await intervalCallbacks[intervalCallbacks.length - 1]();
  const afterRelease = {
    running: SW.store.get().buildRunning,
    stopOffered: SW.store.runningTurnHere('build', 't1', 'app_1'),
  };
  console.log(JSON.stringify({ afterDrop, afterRefresh, afterCancel, afterRelease }));
  process.exit(0);
}

console.log(JSON.stringify({
  midTurn,
  // And the bar comes down by itself: the turn this tab named is the turn this tab forgets.
  runningTurnAfter: SW.store.get().runningTurn,
  runningAfter: kind === 'chat' ? SW.store.get().chatRunning : SW.store.get().buildRunning,
}));
